"""Fiber composition from product-page text: "Shell: 60% Cotton, 40% Polyester. Lining: 100% Nylon".

parse_composition() returns [{fiber, pct}] for the main fabric (shell or body, not lining or trim), largest
first, with fiber names normalized (elastane -> spandex, viscose -> rayon). Fiber names are only those in
FIBERS, so marketing copy ("100% comfort") never becomes a fiber.
"""
import re

# Canonical fiber -> words a store may use for it.
FIBERS = {
    "cotton": ["cotton", "organic cotton", "pima cotton", "supima cotton", "bci cotton"],
    "polyester": ["polyester", "recycled polyester", "poly", "pes", "pet"],
    "nylon": ["nylon", "polyamide", "recycled nylon", "pa"],
    "spandex": ["spandex", "elastane", "lycra", "ea"],
    "rayon": ["rayon", "viscose", "lenzing ecovero", "ecovero"],
    "lyocell": ["lyocell", "tencel", "tencel lyocell"],
    "modal": ["modal", "micromodal", "tencel modal"],
    "acrylic": ["acrylic"],
    "acetate": ["acetate", "triacetate"],
    "wool": ["wool", "merino", "merino wool", "lambswool", "virgin wool", "recycled wool"],
    "cashmere": ["cashmere"],
    "alpaca": ["alpaca"],
    "mohair": ["mohair"],
    "silk": ["silk", "mulberry silk"],
    "linen": ["linen", "flax"],
    "hemp": ["hemp"],
    "leather": ["leather", "genuine leather", "cowhide", "suede"],
    "polyurethane": ["polyurethane", "pu", "faux leather", "vegan leather"],
}
_ALIASES = sorted(((alias, fiber) for fiber, aliases in FIBERS.items() for alias in aliases), key=lambda a: -len(a[0]))
_ALIAS_RE = "|".join(re.escape(a) for a, _ in _ALIASES)
_LOOKUP = dict(_ALIASES)

# "60% cotton" and "cotton 60%"
_PCT_FIRST = re.compile(rf"(\d{{1,3}}(?:[.,]\d+)?)\s*%\s*({_ALIAS_RE})\b", re.I)
_PCT_LAST = re.compile(rf"\b({_ALIAS_RE})\s*[:\-]?\s*(\d{{1,3}}(?:[.,]\d+)?)\s*%", re.I)
# Sections that are not the main fabric. The main fabric is the text before the first of these labels.
_SECONDARY = re.compile(r"\b(lining|linings|lined with|pocket(?:ing)?s?(?: (?:bags?|linings?))?|trim|rib(?:bing)?|"
                        r"contrast|filling|fill|padding|insole|outsole|sole|embroidery|applique|hood lining|"
                        r"waistband|cuffs?)\s*[:\-]", re.I)
_PRIMARY_LABEL = re.compile(r"\b(shell|body|main(?: fabric)?|outer(?: shell)?|fabric|upper|self)\s*[:\-]", re.I)


def _main_section(text: str) -> str:
    """The text that describes the main fabric: after a shell/body label if present, before lining/trim."""
    start = _PRIMARY_LABEL.search(text)
    if start:
        text = text[start.end():]
    stop = _SECONDARY.search(text)
    return text[:stop.start()] if stop else text


def _pairs(text: str) -> list[tuple[str, float]]:
    found = []
    for pct, name in _PCT_FIRST.findall(text):
        found.append((_LOOKUP[name.lower()], float(pct.replace(",", "."))))
    if not found:  # only fall back to "cotton 60%" when no "60% cotton" exists, to avoid double counting
        for name, pct in _PCT_LAST.findall(text):
            found.append((_LOOKUP[name.lower()], float(pct.replace(",", "."))))
    return found


def parse_composition(text: str | None) -> list[dict]:
    """[{fiber, pct}] for the main fabric, largest first. Empty when no "N% fiber" pair is found."""
    if not text:
        return []
    text = re.sub(r"\s+", " ", text)
    pairs = _pairs(_main_section(text)) or _pairs(text)
    # Keep the first complete fabric in reading order: once the pairs reach 100%, later ones belong to another
    # part or variant ("60% cotton, 40% polyester / pocket bag: 100% cotton" keeps 60/40, not 100% cotton).
    totals: dict[str, float] = {}
    running = 0.0
    for fiber, pct in pairs:
        if not 0 < pct <= 100:
            continue
        if running >= 100:
            break
        pct = min(pct, 100 - running)
        running += pct
        totals[fiber] = totals.get(fiber, 0) + pct
    return sorted(({"fiber": f, "pct": round(p, 1)} for f, p in totals.items()), key=lambda f: -f["pct"])


def fibers_from_fabric(fabric: str | None) -> list[dict]:
    """Fallback from the Part 2 fabric attribute when the page has no composition. pct is unknown (None)."""
    if not fabric or fabric in ("unknown", "blend"):
        return []
    fiber = "cotton" if fabric == "denim" else fabric
    return [{"fiber": fiber, "pct": None}] if fiber in FIBERS else []
