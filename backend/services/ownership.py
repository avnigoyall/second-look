"""Part 3 ownership question: for a near-empty closet, guess what the user might already own that resembles an item."""
from backend.services.closet_ingest import ATTRIBUTE_RULES
from backend.services.gemini_json import generate_json
from backend.services.item_understanding import FABRIC_CONFIDENCE, _enum_schema, attribute_string
from backend.settings import ATTRIBUTES

SMALL_CLOSET = 10  # the question shows only while the closet has fewer items than this
TIMEOUT_MS = 20_000
IDEA_PREFIX = "Idea: "

OPTION_ATTRS = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ATTRIBUTES["category"]},
        **{f: _enum_schema(ATTRIBUTES[f]) for f in ("color_family", "fabric", "cut", "style")},
        "fabric_confidence": {"type": "string", "enum": FABRIC_CONFIDENCE},
    },
    "required": ["category", "color_family", "fabric", "cut", "style", "fabric_confidence"],
}

SCHEMA = {
    "type": "object",
    "properties": {
        "options": {"type": "array", "minItems": 3, "maxItems": 3, "items": {
            "type": "object",
            "properties": {"label": {"type": "string"}, "attributes": OPTION_ATTRS},
            "required": ["label", "attributes"],
        }},
        "staple_idea": {"type": "string"},
    },
    "required": ["options", "staple_idea"],
}

PROMPT = """A shopper is about to buy this item, and we know almost nothing about their closet yet.
Item: {name}
Details: {details}

Write 3 options for the question "Do you already own something like this?". Each option is a common
item many people own that could stand in for this one: the same category or a close substitute.
- label: at most 5 words, plain, like "Plain white tee" or "Black straight jeans". No brands.
- attributes: the labels for that owned item.
- The 3 options must differ from each other (for example color, cut, or category).
{rules}

staple_idea: one sentence suggesting a versatile staple that would pair with this item. It is shown as
an idea, so do not state it as fact."""


def ownership_options(name: str, attrs: dict) -> dict:
    details = attribute_string(attrs) or "no details"
    reply = generate_json([PROMPT.format(name=name, details=details, rules=ATTRIBUTE_RULES)], SCHEMA, TIMEOUT_MS,
                          "ownership-options")
    options = []
    for opt in reply.get("options", [])[:3]:
        raw = opt.get("attributes") or {}
        if raw.get("category") not in ATTRIBUTES["category"]:
            continue
        clean = {f: raw.get(f) if raw.get(f) in ATTRIBUTES[f] else None for f in ATTRIBUTES}
        clean["fabric_confidence"] = raw.get("fabric_confidence") if raw.get("fabric_confidence") in FABRIC_CONFIDENCE else "low"
        options.append({"label": " ".join(str(opt.get("label", "")).split()[:6]) or clean["category"],
                        "attributes": clean})
    idea = " ".join(str(reply.get("staple_idea") or "").split())
    return {"options": options, "staple_idea": (IDEA_PREFIX + idea) if idea else None}
