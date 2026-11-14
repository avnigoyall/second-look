"""Product link finder for trend alternatives. Runs offline at trend seed time and as a trend agent step,
never at verdict time.

One Claude call (CLAUDE_MODEL) per trend with the server-side web search tool, restricted to the
basics retailers in backend.alternatives.RETAILERS. Rules enforced in code, not left to the model:
- a product URL is used only if it came back in a web_search_tool_result block (the model's own
  text is never trusted for a URL); the stored URL is the search result's URL
- the URL must belong to a listed retailer and must not be a home, search, or error page
- the URL is checked over HTTP: 200, same registrable domain after redirects, not a home or search
  page. A 403 or 429 (bot protection) keeps the link with verified_at null, since it came straight
  from a search result
- price comes from the product page when it loads, else only from text Claude cited from that URL
- the name comes from the page title, else the search result title, else the model's text
Alternatives with no surviving product link get a store search link (link_kind "search").
"""
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from backend.alternatives import (RETAILERS, LinkCheck, check_client, check_url, clean_title,
                                  is_product_like, normalize_url, now, retailer_for, with_search_fallback)

MAX_SEARCHES = 3
MAX_PAUSE_RESUMES = 3
SEARCH_TIMEOUT_S = 180
MAX_PRODUCTS_PER_TREND = 2
WEB_SEARCH_TOOL = {"type": "web_search_20260318", "name": "web_search", "max_uses": MAX_SEARCHES,
                   "allowed_callers": ["direct"], "allowed_domains": sorted(RETAILERS)}

PROMPT = """Today is {today}. A shopping assistant warns people about a short-lived clothing trend and
suggests a basic, longer-lasting piece to buy instead. Find real product pages for those pieces.

Trend: {trend}
Pieces to suggest instead (index: type):
{alternatives}

Search the retailers' sites for 1 or 2 products in total, at most one per piece. Pick basic,
core-line items in the US store that match the piece's type (for example a plain straight-leg jean
or a classic crew-neck tee), not trend pieces, collaborations, or sale-only items. Use only product
page URLs exactly as they appear in your search results: a page for one product, never a category,
search, or home page. Do not guess or edit URLs.

End your answer with one JSON block, nothing after it:
```json
{{"products": [{{"index": 0, "url": "https://...", "product_name": "...", "price": 49.9, "store": "..."}}]}}
```
price is the US dollar price shown in the search result, or null. Use an empty list if no search
result is a product page for one of the pieces."""


@dataclass
class SearchOutput:
    text: str = ""
    result_urls: dict = field(default_factory=dict)      # normalized url -> {url, title}
    cited_text: dict = field(default_factory=dict)       # normalized url -> [cited passages]
    queries: list = field(default_factory=list)
    errors: list = field(default_factory=list)


class FinderError(Exception):
    pass


def parse_content(content) -> SearchOutput:
    """Collect text, every search result URL, and cited passages from Claude's content blocks.
    A web_search_tool_result whose content is not a list is an error object (HTTP 200, no exception)."""
    out = SearchOutput()
    for block in content:
        btype = getattr(block, "type", None)
        if btype == "server_tool_use" and getattr(block, "name", None) == "web_search":
            q = (block.input or {}).get("query")
            if q:
                out.queries.append(q)
        elif btype == "web_search_tool_result":
            if isinstance(block.content, list):
                for r in block.content:
                    url = getattr(r, "url", None)
                    if url:
                        out.result_urls.setdefault(normalize_url(url), {"url": url, "title": getattr(r, "title", None)})
            else:
                out.errors.append(getattr(block.content, "error_code", "unknown_error"))
        elif btype == "text":
            out.text += block.text
            for c in getattr(block, "citations", None) or []:
                if getattr(c, "type", None) == "web_search_result_location" and c.url:
                    out.cited_text.setdefault(normalize_url(c.url), []).append(c.cited_text or "")
    return out


def parse_candidates(text: str) -> list[dict]:
    """The last JSON object with a "products" list in the model's text. Malformed means no candidates."""
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.findall(r"(\{\s*\"products\".*\})", text, re.S)
    for raw in reversed(blocks):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("products"), list):
            return [p for p in data["products"] if isinstance(p, dict)]
    return []


def price_from_citations(price, passages: list[str]) -> float | None:
    """Keep the model's price only when a passage Claude cited from that page shows it."""
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    forms = {f"{value:.2f}", f"{value:g}"}
    for passage in passages:
        for m in re.finditer(r"\$\s?(\d{1,5}(?:\.\d{1,2})?)", passage or ""):
            if m.group(1) in forms or f"{float(m.group(1)):.2f}" in forms:
                return value
    return None


def screen_candidates(candidates: list[dict], search: SearchOutput, n_alternatives: int) -> tuple[list[dict], list[str]]:
    """Provenance and shape checks, no network. Returns (kept, notes). Kept candidates carry the
    search result's own URL and title."""
    kept, notes, used_index = [], [], set()
    for c in candidates:
        url = str(c.get("url") or "").strip()
        try:
            index = int(c.get("index"))
        except (TypeError, ValueError):
            notes.append(f"dropped {url or '(no url)'}: no valid piece index")
            continue
        if not 0 <= index < n_alternatives:
            notes.append(f"dropped {url}: piece index {index} out of range")
            continue
        hit = search.result_urls.get(normalize_url(url)) if url else None
        if hit is None:
            notes.append(f"dropped {url or '(no url)'}: not a URL from the web search results")
            continue
        if retailer_for(hit["url"]) is None:
            notes.append(f"dropped {url}: not a listed basics retailer")
            continue
        if not is_product_like(hit["url"]):
            notes.append(f"dropped {url}: home, search, or error page")
            continue
        if index in used_index:
            notes.append(f"dropped {url}: piece {index} already has a product")
            continue
        used_index.add(index)
        kept.append({**c, "index": index, "url": hit["url"], "result_title": hit.get("title")})
    return kept, notes


def link_fields(candidate: dict, check: LinkCheck, search: SearchOutput) -> dict:
    """Alternative fields for a checked product link."""
    price = check.page_price
    if price is None:
        price = price_from_citations(candidate.get("price"), search.cited_text.get(normalize_url(candidate["url"]), []))
    name = check.page_title or clean_title(candidate.get("result_title")) or str(candidate.get("product_name") or "").strip()
    if name.isupper():  # some stores title pages in capitals: "COTTON CREW-NECK T-SHIRT"
        name = name.title()
    return {"product_name": name[:100] or None, "product_url": candidate["url"], "price": price,
            "store": retailer_for(candidate["url"]) or candidate.get("store"),
            "verified_at": now() if check.verified else None, "link_kind": "product"}


def search_call(trend: dict, client: anthropic.Anthropic | None = None) -> SearchOutput:
    model = os.getenv("CLAUDE_MODEL")
    if not os.getenv("ANTHROPIC_API_KEY") or not model:
        raise FinderError("ANTHROPIC_API_KEY and CLAUDE_MODEL must be set in .env")
    client = client or anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=SEARCH_TIMEOUT_S)
    alts = "\n".join(f"{i}: {a.get('type')}" for i, a in enumerate(trend.get("alternatives") or []))
    trend_line = f"{trend.get('name')} (category {trend.get('category') or 'unknown'})"
    prompt = PROMPT.format(today=datetime.now(timezone.utc).date().isoformat(), trend=trend_line, alternatives=alts)
    messages = [{"role": "user", "content": prompt}]
    content = []
    try:
        for _ in range(MAX_PAUSE_RESUMES + 1):
            response = client.messages.create(model=model, max_tokens=4000, messages=messages, tools=[WEB_SEARCH_TOOL])
            content += response.content
            if response.stop_reason != "pause_turn":
                break
            # Resume a paused server-side search turn: send the paused assistant message back unchanged.
            messages = [messages[0], {"role": "assistant", "content": content}]
    except anthropic.APIStatusError as e:
        raise FinderError(f"HTTP {e.status_code}: {e.message}")
    except anthropic.APIConnectionError as e:
        raise FinderError(f"could not reach the Anthropic API ({type(e).__name__})")
    if response.stop_reason == "refusal":
        raise FinderError("Claude declined the request (stop_reason refusal)")
    return parse_content(content)


def find_links(trend: dict, client: anthropic.Anthropic | None = None, http=None, log=print) -> tuple[dict, dict]:
    """Give each alternative of a trend (a TrendDoc dump) a link. Returns (updated trend, audit record).

    Alternatives that already have a product link are left alone. A failed search call is not fatal:
    the alternatives fall back to store search links."""
    alts = [dict(a) for a in trend.get("alternatives") or []]
    audit = {"trend": trend.get("name"), "at": now().isoformat(), "notes": [], "checks": []}
    todo = [i for i, a in enumerate(alts) if not (a.get("product_url") and a.get("link_kind") == "product")]
    if todo:
        sub = {**trend, "alternatives": [alts[i] for i in todo]}
        try:
            search = search_call(sub, client)
        except FinderError as e:
            search = None
            audit["notes"].append(f"search failed: {e}")
            log(f"    links: search failed for '{trend.get('name')}': {e}")
        if search is not None:
            audit.update(queries=search.queries, search_errors=search.errors,
                         result_count=len(search.result_urls), model_text=search.text[-2000:])
            candidates, notes = screen_candidates(parse_candidates(search.text), search, len(todo))
            candidates = candidates[:MAX_PRODUCTS_PER_TREND]
            audit["notes"] += notes
            own_http = http is None
            http = http or check_client()
            try:
                for c in candidates:
                    check = check_url(c["url"], from_search_result=True, client=http)
                    audit["checks"].append({"url": c["url"], "status": check.status, "final_url": check.final_url,
                                            "ok": check.ok, "verified": check.verified, "reason": check.reason})
                    if check.ok:
                        alts[todo[c["index"]]].update(link_fields(c, check, search))
                    else:
                        audit["notes"].append(f"dropped {c['url']}: {check.reason}")
            finally:
                if own_http:
                    http.close()
    category = trend.get("category")
    alts = [with_search_fallback(a, category) for a in alts]
    for a in alts:
        kind = "product" if a["link_kind"] == "product" else "search fallback"
        log(f"    link ({kind}): {a.get('type')} -> {a.get('product_name') or ''} {a['product_url']}"
            f"{' $%.2f' % a['price'] if a.get('price') is not None else ''}"
            f"{'' if a.get('verified_at') or a['link_kind'] == 'search' else ' (not verified: store blocks checks)'}")
    for n in audit["notes"]:
        log(f"    link note: {n}")
    return {**trend, "alternatives": alts}, audit


def reuse_links(new_alts: list[dict], old_alts: list[dict]) -> list[dict]:
    """Copy product links from a stored trend onto the same alternative types in a re-seeded trend,
    so re-running the seed script does not search again."""
    old = {str(a.get("type", "")).strip().casefold(): a for a in old_alts or []
           if a.get("product_url") and a.get("link_kind", "product") == "product"}
    out = []
    for a in new_alts:
        prev = old.get(str(a.get("type", "")).strip().casefold())
        if prev and not a.get("product_url"):
            a = {**a, **{k: prev.get(k) for k in ("product_name", "product_url", "price", "store", "verified_at")},
                 "link_kind": "product"}
        out.append(a)
    return out


def save_audit(records: list[dict], runs_dir: Path) -> Path:
    runs_dir.mkdir(exist_ok=True)
    path = runs_dir / f"links-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(records, indent=2, default=str))
    return path
