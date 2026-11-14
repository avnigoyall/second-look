"""Part 5 links to Part 2 (Fix A) and the trend alternatives (Fix B).

backend/services/verdict.py calls only these two functions, so the engine has one place where it
depends on item understanding and on alternative links.
"""
from backend import models as m
from backend.alternatives import alternatives_for_trend as _mapped_alternatives
from backend.services.item_understanding import understand_target_item


def understand_for_verdict(item: dict) -> dict:
    """Part 2 attributes for the item being added, labeling only that garment (Fix A).

    Returns category, color_family, fabric, fabric_confidence, cut, style, description, is_apparel,
    status, attribute_string, target_box_2d, other_garments_visible, category_check and target_crop
    (JPEG data URL or None).
    """
    return understand_target_item(item.get("name") or "", item.get("image_url"),
                                  item.get("description"), item.get("breadcrumb"))


def alternatives_for_trend(trend: dict) -> list[m.Alternative]:
    """Verdict alternatives for a trend document, each with a url (Fix B): product links found at
    seed time, or a store search link when none survived."""
    return [m.Alternative(**a) for a in _mapped_alternatives(trend)]
