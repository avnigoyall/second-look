"""Trend agent: finds current apparel micro-trends with Claude web search and adds them to the trends collection.

Run from the project root:
    python scripts/run_trend_agent.py                      # default Gen Z and college queries
    python scripts/run_trend_agent.py -q "..." -q "..."    # your own focus queries
    python scripts/run_trend_agent.py --dry-run            # show what would be added, write nothing
    python scripts/run_trend_agent.py --from-grounded .trend_runs/grounded-....json   # reuse a saved search

    python scripts/run_trend_agent.py --no-links           # skip the product link search

Two calls, then a link step:
  (a) search: Claude (CLAUDE_MODEL) with the server-side web search tool (at most 5 searches). Keeps
      the report text, the sources Claude cited (url and title), and which sentences cite which
      source. Saved to .trend_runs/.
  (b) structuring: Gemini, no tools. Turns the text from (a) into trend documents. It can cite only the
      numbered sources from (a), by id. The code maps ids back to URLs, so no URL can be invented.
  (c) links: for each kept trend, one Claude web search call finds 1 or 2 basic product pages for its
      alternatives (backend/alternative_finder.py). Only URLs from search results, checked over HTTP.
      Alternatives with no product link get a store search link. Audit saved to .trend_runs/.

Guards in code (not left to the model):
- a trend needs a category from attributes.json and at least one source from (a)
- sources older than 12 months (by page_age, or a date in the URL) are dropped; a trend left with
  no source is discarded
- a lifespan number or bucket needs a cited, recent source, and both are null when sources disagree
  on whether the trend is rising or fading
- dedupe against existing trends and within the run: by name (case-insensitive), and by the same
  category plus the same cut, color and fabric
- alternative links must come from a source, at most 5 new trends per run, origin "agent"
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic  # noqa: E402
from google import genai  # noqa: E402
from google.genai import errors as genai_errors  # noqa: E402
from google.genai import types  # noqa: E402
from pydantic import BaseModel, Field, ValidationError  # noqa: E402
from pymongo.errors import DuplicateKeyError  # noqa: E402

from backend.settings import ATTRIBUTES  # noqa: E402
from backend.trends import TrendDoc, bucket_for_weeks, feature_signature, name_key  # noqa: E402

MAX_NEW_PER_RUN = 5
RUNS_DIR = ROOT / ".trend_runs"
CALL_TIMEOUT_MS = 180_000
SEARCH_TIMEOUT_S = 300
MAX_SEARCHES = 5
MAX_PAUSE_RESUMES = 3
MAX_SOURCE_AGE_DAYS = 365
STRUCTURE_RETRY_WAITS_S = (5, 20)  # Gemini 429/5xx ("high demand") are often brief
# Newest web search version (checked against the Anthropic docs, Nov 2026). allowed_callers "direct"
# makes Claude call search itself, so every result and citation comes back as a plain content block.
WEB_SEARCH_TOOL = {"type": "web_search_20260318", "name": "web_search", "max_uses": MAX_SEARCHES,
                   "allowed_callers": ["direct"]}

DEFAULT_QUERIES = [
    "Gen Z fashion micro-trends right now",
    "viral TikTok clothing trends among college students this season",
    "college campus outfit trends this semester",
    "clothing trends that are fading or predicted to die out soon",
]

GROUNDED_PROMPT = """Today is {today}. You are researching short-lived clothing micro-trends among Gen Z and
college students in the US, so a shopping assistant can warn people before they buy into a trend that
will fade. Search the web for these focus topics:
{queries}

Write a factual research report, citing your sources, on 5 to 10 specific, currently popular apparel
micro-trends. A trend is a specific garment, shoe, bag, or accessory style (for example "barrel-leg
jeans" or "jelly sandals"). A color, print, aesthetic, or styling idea on its own ("cherry red",
"quiet luxury", "coquette") is not a trend; skip it. No makeup, hair, or nails. For each trend give:
- its common name and what the piece looks like: garment type, cut or silhouette, color, fabric
- when and where it took off (platform, celebrity, or brand), with dates if a source gives them
- any lifespan evidence: a source predicting how long it will last, or saying it is already fading.
  Quote numbers exactly as the source gives them. If no source gives a lifespan, say so. If sources
  disagree (one says it is rising or here to stay, another says it is fading), say so.
- longer-lasting classic pieces that give a similar look
Only report what your sources say. Use articles published in the last 12 months, and prefer fashion
publications and trend reports over shopping blogs."""

STRUCTURE_PROMPT = """Turn the research report below into trend records. Today is {today}.

The report marks claims with source ids like [S3]. The numbered sources are:
{sources}

These trends already exist. Do not create a record for any of them, or for the same item under
another name:
{existing}

Rules:
- A trend is a specific garment, shoe, bag, or accessory style, for example "Barrel-leg jeans" or
  "Jelly sandals". A color, print, aesthetic, or styling idea on its own ("Cherry red", "Quiet
  luxury", "Coquette") is NOT a trend: skip it. Skip non-apparel trends.
- Up to {limit} records, strongest evidence first.
- name: short common name of the item.
- category: required, one of the allowed values. If no allowed category fits, skip the trend.
- cut, color, fabric: pick the nearest allowed value (silver or metallic -> gray, cream -> beige,
  barrel or baggy leg -> wide), or null if the report does not say.
- source_ids: ids from the list above whose text supports this trend. Only ids that appear in the
  report next to claims about this trend. Never invent ids. Never cite a source marked "do not cite".
- lifespan_direction_conflict: true if the cited sources disagree on direction (one says the trend is
  rising, current, or lasting; another says it is fading or out). Otherwise false.
- lifespan_weeks_est: a number of weeks ONLY if a source states or directly implies a duration
  (for example "one season" is about 12 weeks). Otherwise null.
- lifespan_bucket: short (fades within about a season, 16 weeks or less), medium (lasts up to about
  a year), or long (lasts more than a year), ONLY if a source supports it (for example "already
  fading" supports short). Otherwise null.
- lifespan_source_id: the id of the source behind lifespan_weeks_est or lifespan_bucket, else null.
- lifespan_label: one short sentence with the lifespan evidence, naming the source, for example
  "Vogue calls it a one-season trend", "No source gives a lifespan", or "Sources disagree: Glamour
  says it is fading, Elle says it is here to stay". Name sources by publication, never by id (no "S3").
- alternatives: 1 to 2 longer-lasting pieces. type = kind of piece, why_it_lasts = one sentence,
  where_to_look = kind of store or section (no brand product links). source_id only if a source in the
  list names that exact alternative, else null.

Research report:
{report}"""


# ---------- structuring schema ----------

def _choice(key: str):
    return Literal[tuple(ATTRIBUTES[key])] | None


class StructuredAlternative(BaseModel):
    type: str
    why_it_lasts: str
    where_to_look: str
    source_id: str | None = None


class StructuredTrend(BaseModel):
    name: str
    category: _choice("category") = None
    cut: _choice("cut") = None
    color: _choice("color_family") = None
    fabric: _choice("fabric") = None
    source_ids: list[str] = Field(default_factory=list)
    lifespan_direction_conflict: bool = False
    lifespan_weeks_est: float | None = None
    lifespan_bucket: Literal["short", "medium", "long"] | None = None
    lifespan_source_id: str | None = None
    lifespan_label: str
    alternatives: list[StructuredAlternative] = Field(default_factory=list)


class StructuredOutput(BaseModel):
    trends: list[StructuredTrend]


# ---------- helpers ----------

def log(msg: str = ""):
    print(msg, flush=True)


def gemini_client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is not set. Add it to .env in the project root.")
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=CALL_TIMEOUT_MS))


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
                                       "oct", "nov", "dec"], start=1)}
RELATIVE_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def parse_page_age(value: str | None, today: date) -> date | None:
    """Parse a search result's page_age: "April 30, 2025", "30 Apr 2025", "2025-04-30", "3 weeks ago".
    Returns None when the text is not a recognizable date."""
    if not value:
        return None
    v = value.strip().lower()
    if v in ("today", "just now"):
        return today
    if v == "yesterday":
        return today - timedelta(days=1)
    m = re.match(r"^(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago$", v)
    if m:
        return today - timedelta(days=int(m.group(1)) * RELATIVE_DAYS.get(m.group(2), 0))
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"^([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$", v) or re.match(r"^(\d{1,2})\s+([a-z]+)\.?,?\s+(\d{4})$", v)
        if not m:
            return None
        a, b, y = m.groups()
        month_word, d = (a, b) if a.isalpha() else (b, a)
        mo, d, y = MONTHS.get(month_word[:3]), int(d), int(y)
        if mo is None:
            return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def date_from_url(url: str) -> date | None:
    """A /2025/05/15/ or /2025/05/ style date in the URL path."""
    m = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])(?:/(0[1-9]|[12]\d|3[01]))?/", urlparse(url).path + "/")
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))


def source_date(chunk: dict, today: date) -> tuple[date | None, str | None]:
    """(date, where it came from) for a source, from page_age first, then the URL."""
    d = parse_page_age(chunk.get("page_age"), today)
    if d:
        return d, "page_age"
    d = date_from_url(chunk["url"])
    return (d, "url") if d else (None, None)


def stale_indexes(chunks: list[dict], today: date) -> set[int]:
    cutoff = today - timedelta(days=MAX_SOURCE_AGE_DAYS)
    return {i for i, c in enumerate(chunks) if (d := source_date(c, today)[0]) and d < cutoff}


def annotate(text: str, supports: list[dict]) -> str:
    """Insert [S1] style markers after each passage that cites a source.
    end_index values are byte offsets into the UTF-8 text."""
    raw = text.encode("utf-8")
    inserts = {}
    for s in supports:
        end = s.get("end_index")
        if end is None or not s.get("chunk_indices") or end > len(raw):
            continue
        ids = inserts.setdefault(end, [])
        ids += [i for i in s["chunk_indices"] if i not in ids]
    for end in sorted(inserts, reverse=True):
        marker = " " + "".join(f"[S{i + 1}]" for i in inserts[end])
        raw = raw[:end] + marker.encode() + raw[end:]
    return raw.decode("utf-8", errors="replace")


# ---------- call (a): Claude web search ----------

def search_call(queries: list[str]) -> dict:
    model = os.getenv("CLAUDE_MODEL")
    if not os.getenv("ANTHROPIC_API_KEY") or not model:
        raise SystemExit("ANTHROPIC_API_KEY and CLAUDE_MODEL must be set in .env for the search call.")
    today = datetime.now(timezone.utc).date().isoformat()
    prompt = GROUNDED_PROMPT.format(today=today, queries="\n".join(f"- {q}" for q in queries))
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=SEARCH_TIMEOUT_S)
    messages = [{"role": "user", "content": prompt}]
    content = []
    try:
        for _ in range(MAX_PAUSE_RESUMES + 1):
            response = client.messages.create(model=model, max_tokens=16000, messages=messages,
                                              tools=[WEB_SEARCH_TOOL])
            content += response.content
            if response.stop_reason != "pause_turn":
                break
            # Resume a paused server-side search turn: send the paused assistant message back unchanged.
            messages = [messages[0], {"role": "assistant", "content": content}]
    except anthropic.BadRequestError as e:
        if "web search" in str(e.message).lower() or "web_search" in str(e.message).lower():
            raise SystemExit(
                f"Search call failed: web search is not available for this API key ({e.message}). "
                "An org admin may need to enable it in the Anthropic Console under Settings > Capabilities.")
        raise SystemExit(f"Search call failed: HTTP 400: {e.message}")
    except anthropic.APIStatusError as e:
        raise SystemExit(f"Search call failed: HTTP {e.status_code}: {e.message}")
    except anthropic.APIConnectionError as e:
        raise SystemExit(f"Search call failed: could not reach the Anthropic API ({type(e).__name__})")
    if response.stop_reason == "refusal":
        raise SystemExit("Search call failed: Claude declined the request (stop_reason refusal).")
    return {"created_at": datetime.now(timezone.utc).isoformat(), "provider": "claude", "model": model,
            "queries": queries, **parse_search_content(content)}


def parse_search_content(content) -> dict:
    """Turn Claude's content blocks into the run format call (b) reads.

    chunks: one entry per cited URL, in order of first citation (these become S1, S2, ...).
    supports: byte offset of the end of each cited text block plus the chunks it cites, so annotate()
    can put [S1] markers after the claims. Also counts every search result and search error."""
    text, chunks, supports, queries, errors = "", [], [], [], []
    result_count, index_of, page_age = 0, {}, {}
    for block in content:
        if block.type == "server_tool_use" and block.name == "web_search":
            query = (block.input or {}).get("query")
            if query:
                queries.append(query)
        elif block.type == "web_search_tool_result":
            if isinstance(block.content, list):
                result_count += len(block.content)
                for r in block.content:
                    if getattr(r, "page_age", None):
                        page_age.setdefault(r.url, r.page_age)
            else:
                errors.append(block.content.error_code)
        elif block.type == "text":
            text += block.text
            cited = []
            for c in block.citations or []:
                if c.type != "web_search_result_location" or not c.url:
                    continue
                if c.url not in index_of:
                    index_of[c.url] = len(chunks)
                    chunks.append({"title": (c.title or urlparse(c.url).hostname or c.url).strip(),
                                   "url": c.url, "domain": urlparse(c.url).hostname, "page_age": None})
                if index_of[c.url] not in cited:
                    cited.append(index_of[c.url])
            if cited:
                supports.append({"end_index": len(text.encode("utf-8")), "chunk_indices": cited})
    for c in chunks:
        c["page_age"] = page_age.get(c["url"])
    return {"text": text, "web_search_queries": queries, "search_result_count": result_count,
            "search_errors": errors, "chunks": chunks, "supports": supports, "search_entry_point_html": None}


def save_run(run: dict) -> Path:
    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / f"grounded-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(run, indent=2))
    return path


# ---------- call (b): structuring ----------

def source_line(i: int, c: dict, today: date, stale: set[int]) -> str:
    d, _ = source_date(c, today)
    line = f"S{i + 1}: {c['title']} ({c.get('domain') or urlparse(c['url']).hostname}"
    line += f", published {d.isoformat()})" if d else ", date unknown)"
    return line + (" OLDER THAN 12 MONTHS, do not cite" if i in stale else "")


def structuring_call(client: genai.Client, model: str, run: dict, limit: int,
                     existing_names: list[str]) -> list[StructuredTrend]:
    today = datetime.now(timezone.utc).date()
    stale = stale_indexes(run["chunks"], today)
    sources = "\n".join(source_line(i, c, today, stale) for i, c in enumerate(run["chunks"]))
    existing = "\n".join(f"- {n}" for n in sorted(existing_names)) or "(none yet)"
    prompt = STRUCTURE_PROMPT.format(today=today.isoformat(), sources=sources, limit=limit, existing=existing,
                                     report=annotate(run["text"], run["supports"]))
    config = types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                         response_schema=StructuredOutput)
    for wait in (*STRUCTURE_RETRY_WAITS_S, None):
        try:
            response = client.models.generate_content(model=model, contents=prompt, config=config)
            break
        except genai_errors.APIError as e:
            if wait is None or not (e.code == 429 or (e.code or 0) >= 500):
                raise SystemExit(f"Structuring call failed: HTTP {e.code} {e.status}: {str(e.message)[:300]}")
            log(f"    Gemini HTTP {e.code}, retrying in {wait}s")
            time.sleep(wait)
    try:
        parsed = response.parsed if isinstance(response.parsed, StructuredOutput) \
            else StructuredOutput.model_validate_json(response.text or "")
    except (ValidationError, ValueError) as e:
        raise SystemExit(f"Structuring call returned a malformed reply: {str(e)[:300]}")
    return parsed.trends


# ---------- guards ----------

SOURCE_ID_IN_TEXT = re.compile(r"\s*\(?\[?\bS\d{1,3}\b\]?\)?")


def clean_label(label: str) -> str:
    """Remove internal source ids ("S3", "[S3]") that the model put into user-facing text."""
    return re.sub(r"\s+([,.;:])", r"\1", SOURCE_ID_IN_TEXT.sub("", label)).strip()


def source_for(sid: str | None, chunks: list[dict]) -> tuple[dict | None, int | None]:
    """Map an id like "S3" to its source and chunk index. (None, None) for an id that is not in the list."""
    if not sid:
        return None, None
    sid = sid.strip().strip("[]").upper()
    if not sid.startswith("S") or not sid[1:].isdigit():
        return None, None
    i = int(sid[1:]) - 1
    if not 0 <= i < len(chunks):
        return None, None
    return {"title": chunks[i]["title"] or chunks[i]["url"], "url": chunks[i]["url"]}, i


def apply_guards(structured: list[StructuredTrend], run: dict, existing: list[dict], max_new: int,
                 today: date | None = None) -> tuple[list[TrendDoc], list[str]]:
    """existing: trend documents already stored, each with name, category, and features."""
    today = today or datetime.now(timezone.utc).date()
    stale = stale_indexes(run["chunks"], today)
    seen_names = {name_key(d["name"]) for d in existing}
    seen_features = {feature_signature(d.get("category"), d.get("features") or {}): d["name"] for d in existing}
    kept, notes = [], []
    first_seen = datetime.now(timezone.utc)

    def usable(sid):
        src, i = source_for(sid, run["chunks"])
        return (None, False) if src is None else (None, True) if i in stale else (src, False)

    for t in structured:
        if t.category is None:
            notes.append(f"discarded '{t.name}': no category from attributes.json")
            continue
        sources, dropped_old = [], 0
        for sid in t.source_ids:
            src, old = usable(sid)
            dropped_old += old
            if src and src["url"] not in {x["url"] for x in sources}:
                sources.append(src)
        if not sources:
            why = "every source is older than 12 months" if dropped_old else "no source from the web search"
            notes.append(f"discarded '{t.name}': {why}")
            continue
        if dropped_old:
            notes.append(f"'{t.name}': dropped {dropped_old} source(s) older than 12 months")
        if name_key(t.name) in seen_names:
            notes.append(f"discarded '{t.name}': a trend with this name already exists")
            continue
        signature = (t.category, t.cut, t.color, t.fabric)
        if signature in seen_features:
            notes.append(f"discarded '{t.name}': same category and features as '{seen_features[signature]}'")
            continue

        weeks, bucket, lifespan_url = t.lifespan_weeks_est, t.lifespan_bucket, None
        if weeks is not None and weeks <= 0:
            weeks = None
        if t.lifespan_direction_conflict and (weeks is not None or bucket is not None):
            notes.append(f"'{t.name}': sources disagree on direction, lifespan number and bucket set to null")
            weeks = bucket = None
        if weeks is not None or bucket is not None:
            src, old = usable(t.lifespan_source_id)
            if src is None:
                reason = "its source is older than 12 months" if old else "no cited source for it"
                notes.append(f"'{t.name}': lifespan (weeks={weeks}, bucket={bucket}) dropped, {reason}")
                weeks = bucket = None
            else:
                lifespan_url = src["url"]
                if weeks is not None:
                    bucket = bucket_for_weeks(weeks)
                if lifespan_url not in {s["url"] for s in sources}:
                    sources.append(src)

        alternatives = []
        for a in t.alternatives:
            alt_src, _ = usable(a.source_id)
            if alt_src and alt_src["url"] not in {s["url"] for s in sources}:
                sources.append(alt_src)
            alternatives.append({"type": a.type, "why_it_lasts": a.why_it_lasts,
                                 "where_to_look": a.where_to_look, "url": alt_src["url"] if alt_src else None})
        try:
            doc = TrendDoc(
                name=t.name, category=t.category,
                features={"cut": t.cut, "color": t.color, "fabric": t.fabric},
                first_seen=first_seen, lifespan_weeks_est=weeks, lifespan_bucket=bucket,
                lifespan_label=clean_label(t.lifespan_label), lifespan_source_url=lifespan_url, sources=sources,
                example_images=[], alternatives=alternatives, origin="agent",
                search_suggestions_html=run.get("search_entry_point_html"))
        except ValidationError as e:
            notes.append(f"discarded '{t.name}': {e.errors()[0]['msg']}")
            continue
        if len(kept) >= max_new:
            notes.append(f"not added '{t.name}': cap of {max_new} new trends per run reached")
            continue
        seen_names.add(name_key(t.name))
        seen_features[signature] = t.name
        kept.append(doc)
    return kept, notes


# ---------- step (c): alternative links ----------

def add_links(docs: list[TrendDoc], find: bool) -> list[TrendDoc]:
    """Every alternative gets a link: a checked product page from web search, else a store search."""
    from backend.alternative_finder import find_links, save_audit
    from backend.alternatives import with_search_fallback

    if find and any(d.alternatives for d in docs):
        log(f"\n(c) Product links with {os.getenv('CLAUDE_MODEL')} web search")
    out, audits = [], []
    for doc in docs:
        data = doc.model_dump()
        if find and data["alternatives"]:
            log(f"    '{doc.name}':")
            data, audit = find_links(data, log=log)
            audits.append(audit)
        else:
            data["alternatives"] = [with_search_fallback(a, data.get("category")) for a in data["alternatives"]]
        out.append(TrendDoc.model_validate(data))
    if audits:
        log(f"    link audit saved to {save_audit(audits, RUNS_DIR).relative_to(ROOT)}")
    return out


# ---------- main ----------

def print_trend(doc: TrendDoc):
    f = doc.features
    log(f"+ {doc.name}  [{doc.category or '?'}; cut={f.cut}, color={f.color}, fabric={f.fabric}]")
    weeks = f"{doc.lifespan_weeks_est:g} weeks" if doc.lifespan_weeks_est is not None else "no number"
    log(f"    lifespan: {weeks}, bucket {doc.lifespan_bucket or 'none'}. {doc.lifespan_label}")
    for s in doc.sources:
        log(f"    source: {s.title} - {s.url}")
    for a in doc.alternatives:
        log(f"    instead: {a.type} ({a.where_to_look}){' - ' + a.url if a.url else ''}")
        if a.product_url:
            label = "buy" if a.link_kind == "product" else "search"
            log(f"      {label}: {a.product_name or a.store or ''} {a.product_url}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("-q", "--query", action="append", dest="queries",
                        help="focus query (repeat for several). Default: Gen Z and college apparel trends")
    parser.add_argument("--max-new", type=int, default=MAX_NEW_PER_RUN,
                        help=f"new trends to add this run, at most {MAX_NEW_PER_RUN}")
    parser.add_argument("--model", default=os.getenv("TREND_AGENT_MODEL") or "gemini-3.5-flash",
                        help="Gemini model for the structuring call (env TREND_AGENT_MODEL, default "
                             "gemini-3.5-flash). The search call uses CLAUDE_MODEL")
    parser.add_argument("--dry-run", action="store_true", help="print the trends, write nothing to the database")
    parser.add_argument("--no-links", action="store_true",
                        help="skip the product link search; alternatives get store search links")
    parser.add_argument("--from-grounded", type=Path,
                        help="reuse a saved call (a) result from .trend_runs/ instead of searching again")
    args = parser.parse_args()
    max_new = max(1, min(args.max_new, MAX_NEW_PER_RUN))
    client = gemini_client()

    if args.from_grounded:
        run = json.loads(args.from_grounded.read_text())
        log(f"Using saved search result {args.from_grounded}")
    else:
        queries = args.queries or DEFAULT_QUERIES
        log(f"(a) Web search with {os.getenv('CLAUDE_MODEL')} (at most {MAX_SEARCHES} searches):")
        for q in queries:
            log(f"    - {q}")
        run = search_call(queries)
        args.from_grounded = save_run(run).relative_to(ROOT)
        log(f"    saved to {args.from_grounded}")
    if run.get("web_search_queries"):
        log(f"    searches run: {'; '.join(run['web_search_queries'])}")
    log(f"    {run.get('search_result_count', 0)} search results, {len(run['chunks'])} cited sources, "
        f"{len(run['supports'])} cited passages")
    if run.get("search_errors"):
        log(f"    search errors: {', '.join(run['search_errors'])}")
    today = datetime.now(timezone.utc).date()
    stale = stale_indexes(run["chunks"], today)
    for i, c in enumerate(run["chunks"]):
        d, where = source_date(c, today)
        when = f"{d.isoformat()} ({where})" if d else "date unknown"
        log(f"    S{i + 1}: [{when}{', OLDER THAN 12 MONTHS' if i in stale else ''}] {c['title']} - {c['url']}")

    if not run["chunks"]:
        log("\nWARNING: the web search call returned no cited sources, so there is nothing to cite.")
        log("Every trend would be discarded by the source guard. Nothing was added. Try other queries.")
        raise SystemExit(2)

    from backend.db import ensure_indexes, trends  # imported late so --help works without a database
    ensure_indexes()
    existing = list(trends.find({}, {"name": 1, "category": 1, "features": 1}))

    log(f"\n(b) Structuring with {args.model}")
    try:
        structured = structuring_call(client, args.model, run, limit=max_new + 3,
                                      existing_names=[d["name"] for d in existing])
    except SystemExit:
        log(f"The search result is saved. Retry without searching again: --from-grounded {args.from_grounded}")
        raise
    log(f"    model returned {len(structured)} trends")
    kept, notes = apply_guards(structured, run, existing, max_new)
    for n in notes:
        log(f"    guard: {n}")

    kept = add_links(kept, find=not args.no_links)

    added = []
    for doc in kept:
        if args.dry_run:
            added.append(doc)
            continue
        try:
            trends.insert_one(doc.model_dump())
            added.append(doc)
        except DuplicateKeyError:
            log(f"    guard: discarded '{doc.name}': a trend with this name already exists")

    log(f"\n{'Would add' if args.dry_run else 'Added'} {len(added)} trend(s):")
    for doc in added:
        print_trend(doc)
    if not args.dry_run:
        log(f"\ntrends collection now holds {trends.count_documents({})} trends "
            f"({trends.count_documents({'origin': 'seed'})} seed, {trends.count_documents({'origin': 'agent'})} agent)")


if __name__ == "__main__":
    main()
