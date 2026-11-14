"""Part 2 item understanding endpoints."""
from fastapi import APIRouter, Depends

from backend import models as m
from backend.auth import current_user
from backend.services.item_understanding import attribute_string, understand_item
from backend.settings import ATTRIBUTES

router = APIRouter(tags=["attributes"])


@router.post("/attributes", response_model=m.ItemAttributesResponse)
def attributes(body: m.AttributesRequest, user=Depends(current_user)):
    """Gemini reads the item (image if it loads, plus text) into attributes from attributes.json."""
    attrs = understand_item(body.name, body.image_url, body.description, body.breadcrumb, body.text)
    return {**attrs, "attribute_string": attribute_string(attrs)}


@router.get("/config/attributes")
def config_attributes() -> dict[str, list[str]]:
    """The allowed attribute values, straight from backend/config/attributes.json."""
    return ATTRIBUTES
