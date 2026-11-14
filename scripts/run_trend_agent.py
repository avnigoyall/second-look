"""Trend agent: finds current apparel micro-trends with Gemini + Google Search and adds them to the trends collection.

Run from the project root:
    python scripts/run_trend_agent.py                      # default Gen Z and college queries
    python scripts/run_trend_agent.py -q "..." -q "..."    # your own focus queries
    python scripts/run_trend_agent.py --dry-run            # show what would be added, write nothing
    python scripts/run_trend_agent.py --from-grounded .trend_runs/grounded-....json   # reuse a saved search

Two Gemini calls:
  (a) grounded: Google Search tool on. Keeps the reply text, the grounding chunk URLs and titles,
      which sentences each chunk supports, and the searchEntryPoint HTML. Saved to .trend_runs/.
  (b) structuring: no tools. Turns the text from (a) into trend documents. It can cite only the
      numbered sources from (a), by id. The code maps ids back to URLs, so no URL can be invented.

Guards in code (not left to the model): a trend needs at least one source from (a), names are
deduped case-insensitively against existing trends and within the run, a lifespan number needs a
cited source, alternative links must come from a source, at most 5 new trends per run, origin "agent".
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from google import genai  # noqa: E402
from google.genai import errors as genai_errors  # noqa: E402
from google.genai import types  # noqa: E402
from pydantic import BaseModel, Field, ValidationError  # noqa: E402
from pymongo.errors import DuplicateKeyError  # noqa: E402

from backend.settings import ATTRIBUTES  # noqa: E402
from backend.trends import TrendDoc, name_key  # noqa: E402

MAX_NEW_PER_RUN = 5
RUNS_DIR = ROOT / ".trend_runs"
CALL_TIMEOUT_MS = 180_000
REDIRECT_HOST = "vertexaisearch.cloud.google.com"

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

Write a factual research report on 5 to 10 specific, currently popular apparel micro-trends (clothing,
shoes, or bags; not makeup, hair, or nails). For each trend give:
- its common name and what the piece looks like: garment type, cut or silhouette, color, fabric
- when and where it took off (platform, celebrity, or brand), with dates if a source gives them
- any lifespan evidence: a source predicting how long it will last, or saying it is already fading.
  Quote numbers exactly as the source gives them. If no source gives a lifespan, say so.
- longer-lasting classic pieces that give a similar look
Only report what your sources say. Prefer recent articles from fashion publications and trend reports."""

STRUCTURE_PROMPT = """Turn the research report below into trend records. Today is {today}.

The report marks claims with source ids like [S3]. The numbered sources are:
{sources}

Rules:
- One record per specific apparel micro-trend (clothing, shoes, bags). Skip vague themes ("comfort",
  "self-expression") and non-apparel trends. Up to {limit} records, strongest evidence first.
- name: short common name, for example "Barrel-leg jeans".
- category, cut, color, fabric: pick the nearest allowed value (silver or metallic -> gray, cream ->
  beige, barrel or baggy leg -> wide), or null if the report does not say.
- source_ids: ids from the list above whose text supports this trend. Only ids that appear in the
  report next to claims about this trend. Never invent ids.
- lifespan_weeks_est: a number of weeks ONLY if a source states or directly implies a duration
  (for example "one season" is about 12 weeks). Then lifespan_source_id must be that source's id.
  Otherwise null and lifespan_source_id null.
- lifespan_label: one short sentence with the lifespan evidence, naming the source, for example
  "Vogue calls it a one-season trend" or "No source gives a lifespan; Glamour says it is already fading".
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
    lifespan_weeks_est: float | None = None
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


def explain_api_error(e: genai_errors.APIError, step: str) -> SystemExit:
    if e.code == 429 and step == "grounded":
        return SystemExit(
            "Grounded call failed: 429 RESOURCE_EXHAUSTED. Plain Gemini calls can still work on this key, "
            "but Google Search grounding has no quota on it. Enable billing on the key's AI Studio / Google "
            "Cloud project, or use a key that has grounding quota, then run again.")
    return SystemExit(f"{step} call failed: HTTP {e.code} {e.status}: {str(e.message)[:300]}")


def resolve_url(client: httpx.Client, uri: str) -> str:
    """Grounding chunk URIs are Google redirect links that expire. Store the page they point to."""
    if urlparse(uri).hostname != REDIRECT_HOST:
        return uri
    try:
        r = client.get(uri, follow_redirects=False)
        location = r.headers.get("location")
        if r.is_redirect and location and location.startswith("http"):
            return location
    except httpx.HTTPError:
        pass
    return uri


def annotate(text: str, supports: list[dict]) -> str:
    """Insert [S1] style markers after each sentence that a grounding chunk supports.
    Gemini segment end indices are byte offsets into the UTF-8 text."""
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


# ---------- call (a): grounded search ----------

def grounded_call(client: genai.Client, model: str, queries: list[str]) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    prompt = GROUNDED_PROMPT.format(today=today, queries="\n".join(f"- {q}" for q in queries))
    try:
        response = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]))
    except genai_errors.APIError as e:
        raise explain_api_error(e, "grounded")

    candidate = response.candidates[0] if response.candidates else None
    gm = candidate.grounding_metadata if candidate else None
    chunks = [{"title": (c.web.title or c.web.domain or "").strip(), "uri": c.web.uri, "domain": c.web.domain}
              for c in (gm.grounding_chunks or [] if gm else []) if c.web and c.web.uri]
    supports = [{"end_index": s.segment.end_index, "chunk_indices": list(s.grounding_chunk_indices or [])}
                for s in (gm.grounding_supports or [] if gm else []) if s.segment]
    with httpx.Client(timeout=10) as http:
        for c in chunks:
            c["url"] = resolve_url(http, c["uri"])
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "queries": queries,
        "text": response.text or "",
        "web_search_queries": list(gm.web_search_queries or []) if gm else [],
        "chunks": chunks,
        "supports": supports,
        "search_entry_point_html": (gm.search_entry_point.rendered_content
                                    if gm and gm.search_entry_point else None),
    }


def save_run(run: dict) -> Path:
    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / f"grounded-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(run, indent=2))
    return path


# ---------- call (b): structuring ----------

def structuring_call(client: genai.Client, model: str, run: dict, limit: int) -> list[StructuredTrend]:
    today = datetime.now(timezone.utc).date().isoformat()
    sources = "\n".join(f"S{i + 1}: {c['title']} ({c.get('domain') or urlparse(c['url']).hostname})"
                        for i, c in enumerate(run["chunks"]))
    prompt = STRUCTURE_PROMPT.format(today=today, sources=sources, limit=limit,
                                     report=annotate(run["text"], run["supports"]))
    config = types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                         response_schema=StructuredOutput)
    try:
        response = client.models.generate_content(model=model, contents=prompt, config=config)
    except genai_errors.APIError as e:
        raise explain_api_error(e, "structuring")
    try:
        parsed = response.parsed if isinstance(response.parsed, StructuredOutput) \
            else StructuredOutput.model_validate_json(response.text or "")
    except (ValidationError, ValueError) as e:
        raise SystemExit(f"Structuring call returned a malformed reply: {str(e)[:300]}")
    return parsed.trends


# ---------- guards ----------

def source_for(sid: str | None, chunks: list[dict]) -> dict | None:
    if not sid:
        return None
    sid = sid.strip().strip("[]").upper()
    if not sid.startswith("S") or not sid[1:].isdigit():
        return None
    i = int(sid[1:]) - 1
    return {"title": chunks[i]["title"] or chunks[i]["url"], "url": chunks[i]["url"]} if 0 <= i < len(chunks) else None


def apply_guards(structured: list[StructuredTrend], run: dict, existing_keys: set[str],
                 max_new: int) -> tuple[list[TrendDoc], list[str]]:
    kept, notes, seen = [], [], set(existing_keys)
    first_seen = datetime.now(timezone.utc)
    for t in structured:
        sources = []
        for sid in t.source_ids:
            s = source_for(sid, run["chunks"])
            if s and s["url"] not in {x["url"] for x in sources}:
                sources.append(s)
        if not sources:
            notes.append(f"discarded '{t.name}': no source from the grounded search")
            continue
        key = name_key(t.name)
        if key in seen:
            notes.append(f"discarded '{t.name}': a trend with this name already exists")
            continue

        lifespan, lifespan_url = t.lifespan_weeks_est, None
        if lifespan is not None:
            lifespan_src = source_for(t.lifespan_source_id, run["chunks"])
            if lifespan_src is None or lifespan <= 0:
                notes.append(f"'{t.name}': lifespan number {lifespan} dropped, no cited source for it")
                lifespan = None
            else:
                lifespan_url = lifespan_src["url"]
                if lifespan_url not in {s["url"] for s in sources}:
                    sources.append(lifespan_src)

        alternatives = []
        for a in t.alternatives:
            alt_src = source_for(a.source_id, run["chunks"])
            if alt_src and alt_src["url"] not in {s["url"] for s in sources}:
                sources.append(alt_src)
            alternatives.append({"type": a.type, "why_it_lasts": a.why_it_lasts,
                                 "where_to_look": a.where_to_look, "url": alt_src["url"] if alt_src else None})
        try:
            doc = TrendDoc(
                name=t.name, category=t.category,
                features={"cut": t.cut, "color": t.color, "fabric": t.fabric},
                first_seen=first_seen, lifespan_weeks_est=lifespan, lifespan_label=t.lifespan_label,
                lifespan_source_url=lifespan_url, sources=sources, example_images=[],
                alternatives=alternatives, origin="agent",
                search_suggestions_html=run["search_entry_point_html"])
        except ValidationError as e:
            notes.append(f"discarded '{t.name}': {e.errors()[0]['msg']}")
            continue
        if len(kept) >= max_new:
            notes.append(f"not added '{t.name}': cap of {max_new} new trends per run reached")
            continue
        seen.add(key)
        kept.append(doc)
    return kept, notes


# ---------- main ----------

def print_trend(doc: TrendDoc):
    f = doc.features
    log(f"+ {doc.name}  [{doc.category or '?'}; cut={f.cut}, color={f.color}, fabric={f.fabric}]")
    weeks = f"{doc.lifespan_weeks_est:g} weeks" if doc.lifespan_weeks_est is not None else "no number"
    log(f"    lifespan: {weeks}. {doc.lifespan_label}")
    for s in doc.sources:
        log(f"    source: {s.title} - {s.url}")
    for a in doc.alternatives:
        log(f"    instead: {a.type} ({a.where_to_look}){' - ' + a.url if a.url else ''}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("-q", "--query", action="append", dest="queries",
                        help="focus query (repeat for several). Default: Gen Z and college apparel trends")
    parser.add_argument("--max-new", type=int, default=MAX_NEW_PER_RUN,
                        help=f"new trends to add this run, at most {MAX_NEW_PER_RUN}")
    parser.add_argument("--model", default=os.getenv("TREND_AGENT_MODEL") or "gemini-3.5-flash",
                        help="Gemini model for both calls (env TREND_AGENT_MODEL, default gemini-3.5-flash)")
    parser.add_argument("--dry-run", action="store_true", help="print the trends, write nothing to the database")
    parser.add_argument("--from-grounded", type=Path,
                        help="reuse a saved call (a) result from .trend_runs/ instead of searching again")
    args = parser.parse_args()
    max_new = max(1, min(args.max_new, MAX_NEW_PER_RUN))
    client = gemini_client()

    if args.from_grounded:
        run = json.loads(args.from_grounded.read_text())
        log(f"Using saved grounded result {args.from_grounded}")
    else:
        queries = args.queries or DEFAULT_QUERIES
        log(f"(a) Grounded search with {args.model}:")
        for q in queries:
            log(f"    - {q}")
        run = grounded_call(client, args.model, queries)
        log(f"    saved to {save_run(run).relative_to(ROOT)}")
    log(f"    {len(run['chunks'])} grounding chunks, {len(run['supports'])} supported segments, "
        f"searchEntryPoint {'present' if run['search_entry_point_html'] else 'MISSING'}")
    if run["web_search_queries"]:
        log(f"    Google searches run: {'; '.join(run['web_search_queries'])}")

    if not run["chunks"]:
        log("\nWARNING: the grounded call returned no grounding chunks, so there are no sources to cite.")
        log("Every trend would be discarded by the source guard. Nothing was added. Try other queries or a")
        log("different model with --model.")
        raise SystemExit(2)
    if not run["search_entry_point_html"]:
        log("WARNING: no searchEntryPoint HTML in the reply. Trends will be stored without search suggestions.")

    from backend.db import ensure_indexes, trends  # imported late so --help works without a database
    ensure_indexes()
    existing_keys = {name_key(d["name"]) for d in trends.find({}, {"name": 1})}

    log(f"\n(b) Structuring with {args.model}")
    structured = structuring_call(client, args.model, run, limit=max_new + 3)
    log(f"    model returned {len(structured)} trends")
    kept, notes = apply_guards(structured, run, existing_keys, max_new)
    for n in notes:
        log(f"    guard: {n}")

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
