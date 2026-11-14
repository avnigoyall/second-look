"""Alternative links: retailers, store search fallbacks, link checks, and the trend to verdict mapping.

Rule: every alternative the card shows carries a link. A trend alternative either has a checked
product page (link_kind "product") or a store search URL built from its type (link_kind "search").

This module makes no Claude calls. The product link finder (Claude web search, seed and agent time
only) lives in backend/alternative_finder.py.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote_plus, urlsplit, urlunsplit

import httpx

# ---------- retailers ----------

# Reputable basics retailers. Product links are accepted only from these domains (subdomains count,
# for example oldnavy.gap.com). The web search is also restricted to them.
RETAILERS = {
    "uniqlo.com": "Uniqlo",
    "gap.com": "Gap",
    "oldnavy.gap.com": "Old Navy",
    "bananarepublic.gap.com": "Banana Republic",
    "jcrew.com": "J.Crew",
    "madewell.com": "Madewell",
    "everlane.com": "Everlane",
    "levi.com": "Levi's",
    "cos.com": "COS",
    "llbean.com": "L.L.Bean",
    "landsend.com": "Lands' End",
    "quince.com": "Quince",
    "abercrombie.com": "Abercrombie & Fitch",
    "target.com": "Target",
}

# Store search URLs for the fallback. Only stores whose search page answered 200 to a plain
# HTTP request (checked Nov 14, 2026) are listed, so a fallback link is never a blocked page.
SEARCH_TEMPLATES = {
    "Gap": "https://www.gap.com/browse/search.do?searchText={q}",
    "Old Navy": "https://oldnavy.gap.com/browse/search.do?searchText={q}",
    "Banana Republic": "https://bananarepublic.gap.com/browse/search.do?searchText={q}",
    "L.L.Bean": "https://www.llbean.com/llb/search/?freeText={q}",
    "Quince": "https://www.quince.com/search?q={q}",
    "Abercrombie & Fitch": "https://www.abercrombie.com/shop/us/search?searchTerm={q}",
    "Target": "https://www.target.com/s?searchTerm={q}",
}
DEFAULT_SEARCH_STORE = "Gap"
# Categories Gap covers poorly get a store that carries them.
SEARCH_STORE_BY_CATEGORY = {"coat": "L.L.Bean", "shoes": "L.L.Bean", "bag": "L.L.Bean"}

# ---------- URL helpers ----------

_TWO_LEVEL_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.jp", "co.kr", "com.br",
                       "com.mx", "co.nz", "co.in", "com.sg", "com.hk", "com.tw", "com.cn"}


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def registrable_domain(host_or_url: str) -> str:
    """eTLD+1 for the hosts we deal with: www2.hm.com -> hm.com, shop.example.co.uk -> example.co.uk."""
    host = host_of(host_or_url) if "/" in host_or_url else host_or_url.lower().rstrip(".")
    labels = [x for x in host.split(".") if x]
    if len(labels) <= 2:
        return ".".join(labels)
    n = 3 if ".".join(labels[-2:]) in _TWO_LEVEL_SUFFIXES else 2
    return ".".join(labels[-n:])


def retailer_for(url: str) -> str | None:
    """Display name of the retailer that owns this URL, the most specific domain first."""
    host = host_of(url)
    for domain in sorted(RETAILERS, key=len, reverse=True):
        if host == domain or host.endswith("." + domain):
            return RETAILERS[domain]
    return None


def normalize_url(url: str) -> str:
    """Form used to compare URLs: lowercase scheme and host, no www, no fragment, no trailing slash."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, parts.query, ""))


_LOCALE_SEGMENT = re.compile(r"^(?:[a-z]{2}(?:[-_][a-z]{2})?|shop|home|index\.html?)$", re.I)
_SEARCH_SEGMENT = re.compile(r"search|^s$|^find$", re.I)
_SEARCH_PARAMS = {"q", "query", "searchtext", "searchterm", "ntrm", "freetext", "keyword", "keywords", "k", "search"}
_ERROR_SEGMENT = re.compile(r"(?:^|[-_/])(?:404|not-?found|page-?not-?found|error)(?:$|[-_./])", re.I)


def _segments(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


def is_home_page(url: str) -> bool:
    """A site root or a locale root such as /us/en/ or /en_us/."""
    return all(_LOCALE_SEGMENT.match(s) for s in _segments(url))


def is_search_page(url: str) -> bool:
    parts = urlsplit(url)
    if any(_SEARCH_SEGMENT.search(s) for s in _segments(url)):
        return True
    return any(k.lower() in _SEARCH_PARAMS for k, _ in parse_qsl(parts.query, keep_blank_values=True))


def is_error_page(url: str) -> bool:
    return bool(_ERROR_SEGMENT.search(urlsplit(url).path))


def is_product_like(url: str) -> bool:
    return url.startswith(("http://", "https://")) and not (
        is_home_page(url) or is_search_page(url) or is_error_page(url))


# ---------- search fallback ----------

def search_store_for(category: str | None = None, preferred: str | None = None) -> str:
    if preferred in SEARCH_TEMPLATES:
        return preferred
    return SEARCH_STORE_BY_CATEGORY.get(category or "", DEFAULT_SEARCH_STORE)


def clean_query(text: str) -> str:
    """Search words from an alternative type: 'Straight-leg, dark wash jeans.' -> 'straight-leg dark wash jeans'."""
    words = re.sub(r"[^\w\s'-]", " ", text or "").split()
    return " ".join(words[:8]).lower() or "basics"


def search_link(query: str, category: str | None = None, preferred_store: str | None = None) -> dict:
    """{store, url, query} for a store search page."""
    store = search_store_for(category, preferred_store)
    q = clean_query(query)
    return {"store": store, "url": SEARCH_TEMPLATES[store].format(q=quote_plus(q)), "query": q}


# ---------- link check ----------

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
BROWSER_HEADERS = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                   "Accept-Language": "en-US,en;q=0.9"}
CHECK_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
# Bot protection answers these. A blocked link is kept only when it came straight from a search result.
BLOCKED_STATUSES = {403, 429}
MAX_HTML_BYTES = 1_500_000


@dataclass
class LinkCheck:
    ok: bool                 # keep the link
    verified: bool           # loaded as a real product page (sets verified_at); False when blocked
    status: int | None
    final_url: str | None
    reason: str
    page_price: float | None = None
    page_title: str | None = None


def classify(url: str, status: int | None, final_url: str | None, from_search_result: bool) -> LinkCheck:
    """Pure decision for one checked URL. No network."""
    if not is_product_like(url):
        return LinkCheck(False, False, status, final_url, "not a product page (home, search, or error URL)")
    if status is None:
        return LinkCheck(False, False, None, final_url, "unreachable")
    if status in BLOCKED_STATUSES:
        if from_search_result:
            return LinkCheck(True, False, status, final_url, f"blocked by the store ({status}), kept: from a search result")
        return LinkCheck(False, False, status, final_url, f"blocked by the store ({status}) and not from a search result")
    if status != 200:
        return LinkCheck(False, False, status, final_url, f"HTTP {status}")
    final_url = final_url or url
    if registrable_domain(final_url) != registrable_domain(url):
        return LinkCheck(False, False, status, final_url, f"redirected to another site ({host_of(final_url)})")
    if is_home_page(final_url):
        return LinkCheck(False, False, status, final_url, "redirected to the home page")
    if is_search_page(final_url):
        return LinkCheck(False, False, status, final_url, "redirected to a search page")
    if is_error_page(final_url):
        return LinkCheck(False, False, status, final_url, "redirected to an error page")
    return LinkCheck(True, True, status, final_url, "ok")


_JSONLD_PRICE = re.compile(r'"(?:price|lowPrice)"\s*:\s*"?(\d{1,5}(?:\.\d{1,2})?)"?')
_META_PRICE = re.compile(r'<meta[^>]+(?:property|name|itemprop)=["\'](?:product:price:amount|og:price:amount|price)["\'][^>]*'
                         r'content=["\']\$?(\d{1,5}(?:\.\d{1,2})?)["\']', re.I)
_OG_TITLE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']{3,200})["\']', re.I)
_TITLE = re.compile(r"<title[^>]*>([^<]{3,200})</title>", re.I)


def page_price(html: str) -> float | None:
    for pattern in (_META_PRICE, _JSONLD_PRICE):
        m = pattern.search(html)
        if m:
            value = float(m.group(1))
            if 0 < value < 5000:
                return value
    return None


def clean_title(title: str | None) -> str | None:
    """'Men's Straight Jeans | UNIQLO US' -> "Men's Straight Jeans"."""
    if not title:
        return None
    title = re.sub(r"&amp;", "&", title)
    title = re.sub(r"&#39;|&#x27;|&apos;", "'", title)
    title = re.split(r"\s+[|\u2013\u2014-]\s+", title.strip())[0].strip()
    return title[:100] or None


def page_title(html: str) -> str | None:
    m = _OG_TITLE.search(html) or _TITLE.search(html)
    return clean_title(m.group(1)) if m else None


def check_url(url: str, from_search_result: bool, client: httpx.Client | None = None) -> LinkCheck:
    """GET the URL with a browser-like User-Agent, follow redirects, and classify the result."""
    own = client is None
    client = client or httpx.Client(headers=BROWSER_HEADERS, timeout=CHECK_TIMEOUT, follow_redirects=True)
    try:
        with client.stream("GET", url) as r:
            html = ""
            if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_HTML_BYTES:
                        break
                html = body.decode(r.encoding or "utf-8", errors="replace")
            result = classify(url, r.status_code, str(r.url), from_search_result)
    except httpx.HTTPError as e:
        return classify(url, None, None, from_search_result) if is_product_like(url) else \
            LinkCheck(False, False, None, None, f"unreachable ({type(e).__name__})")
    finally:
        if own:
            client.close()
    if result.ok and html:
        result.page_price, result.page_title = page_price(html), page_title(html)
    return result


def check_client() -> httpx.Client:
    return httpx.Client(headers=BROWSER_HEADERS, timeout=CHECK_TIMEOUT, follow_redirects=True)


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- trend alternative helpers ----------

def with_search_fallback(alt: dict, category: str | None = None) -> dict:
    """Return the alternative with a link: its product link when it has one, else a store search link."""
    if alt.get("product_url"):
        return {**alt, "link_kind": alt.get("link_kind") or "product"}
    link = search_link(alt.get("type") or alt.get("product_name") or "basics", category, alt.get("store"))
    return {**alt, "product_name": None, "product_url": link["url"], "price": None, "store": link["store"],
            "verified_at": None, "link_kind": "search"}


# ---------- trend -> verdict mapping (Part 5 calls this) ----------

def _get(obj, key):
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def trend_alternative_to_verdict(alt, category: str | None = None) -> dict:
    """One trend alternative (dict or TrendAlternative) as a verdict Alternative dict. url is always set."""
    data = alt if isinstance(alt, dict) else alt.model_dump()
    data = with_search_fallback(data, category)
    if data["link_kind"] == "search":
        name = data.get("type") or "Classic basics"
    else:
        name = data.get("product_name") or data.get("type") or "Classic basics"
    return {"name": name, "url": data["product_url"], "price": data.get("price"),
            "reason": data.get("why_it_lasts"), "store": data.get("store") or retailer_for(data["product_url"]),
            "link_kind": data["link_kind"]}


def alternatives_for_trend(trend, limit: int = 2, fallback_query: str | None = None) -> list[dict]:
    """Verdict alternatives for a matched trend (stored dict or Trend model). Every one has a url.

    When the trend has no alternatives, one store search for a classic piece in its category (or
    fallback_query when it has no category) is returned, so a micro_trend card never shows an
    alternative without a link."""
    if trend is None:
        return []
    category = _get(trend, "category")
    alts = list(_get(trend, "alternatives") or [])[:limit]
    if alts:
        return [trend_alternative_to_verdict(a, category) for a in alts]
    query = f"classic {category}" if category else (fallback_query or "classic basics")
    link = search_link(query, category)
    return [{"name": query[:1].upper() + query[1:], "url": link["url"], "price": None,
             "reason": "A classic version outlasts the trend.", "store": link["store"], "link_kind": "search"}]


def stored_verdict_alternative(raw: dict) -> dict:
    """Read path for verdict docs. Older docs stored url null; give those a store search link."""
    raw = dict(raw or {})
    name = raw.get("name") or "Classic basics"
    if raw.get("url"):
        return {**raw, "name": name, "link_kind": raw.get("link_kind") or "product",
                "store": raw.get("store") or retailer_for(raw["url"])}
    link = search_link(name)
    return {**raw, "name": name, "url": link["url"], "store": link["store"], "link_kind": "search"}


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, default=str)
