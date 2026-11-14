"""Part 5 integration points with two parallel branches. Each function here is a temporary bridge.

INTEGRATE: understand_for_verdict is replaced by the Fix A call (target item only, plus target_crop).
INTEGRATE: alternatives_for_trend is replaced by the Fix B mapping (trend alternatives with real links).
Keep the signatures: backend/services/verdict.py calls only these two functions.
"""
import logging

from backend import models as m
from backend.services.item_understanding import understand_item

log = logging.getLogger("secondlook.verdict_deps")

_warned_alternatives = False


def understand_for_verdict(item: dict) -> dict:
    """Part 2 attributes for the item being added, plus the target crop.

    Returns understand_item()'s dict (category, color_family, fabric, fabric_confidence, cut, style,
    description, is_apparel, status) plus target_box_2d, other_garments_visible and target_crop
    (JPEG data URL or None).
    """
    # INTEGRATE: replaced by Fix A (call its target-item function, which returns target_crop).
    attrs = understand_item(item.get("name") or "", item.get("image_url"),
                            item.get("description"), item.get("breadcrumb"))
    return {**attrs, "target_box_2d": None, "other_garments_visible": None, "target_crop": None}


def alternatives_for_trend(trend: dict) -> list[m.Alternative]:
    """Verdict alternatives (url required) for a full trend document from the trends collection."""
    # INTEGRATE: replaced by Fix B. Planned body:
    #   from backend.alternatives import alternatives_for_trend as mapped
    #   return [m.Alternative(**a) for a in mapped(trend)]
    global _warned_alternatives
    if not _warned_alternatives:
        log.info("alternatives_for_trend: Fix B mapping not merged yet, returning no alternatives")
        _warned_alternatives = True
    return []
