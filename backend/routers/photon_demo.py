"""Demo only: the color of the top in a photo, for the iMessage demo replay (imessage/src/photon_demo_replay.mjs).

POST /imessage/demo-color asks Claude (CLAUDE_MODEL) one short question about the photo and returns
{color_family}. Read-only: no verdict, closet item, Radar data or account is created or changed.
Agent secret required. Any failure answers color_family null, so the replay falls back to the live path.
"""
import base64
import binascii
import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from backend.routers.imessage import require_agent
from backend.services.item_understanding import resize_image
from backend.services.verdict import claude_client, claude_model

log = logging.getLogger("secondlook.photon_demo")
router = APIRouter(prefix="/imessage", tags=["imessage"])

MAX_IMAGE_CHARS = 15_000_000
TIMEOUT_S = 8.0
COLORS = ["black", "blue", "white", "gray", "beige", "brown", "red", "pink", "green", "yellow", "purple", "multi",
          "other"]
COLOR_TOOL = {
    "name": "top_color",
    "description": "Report the main color of the top (shirt, tank, cami, blouse) the person is wearing.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["color"],
        "properties": {"color": {"type": "string", "enum": COLORS}},
    },
}
PROMPT = ("What is the main color of the top the person is wearing? Navy and dark blue count as blue. "
          "A pattern on a blue base (dots, stripes) counts as blue. Ignore the background, pants and bags.")


class DemoColorRequest(BaseModel):
    image_base64: str = Field(max_length=MAX_IMAGE_CHARS)


class DemoColorResponse(BaseModel):
    color_family: str | None = None


@router.post("/demo-color", response_model=DemoColorResponse)
def demo_color(body: DemoColorRequest, x_agent_secret: str | None = Header(None)):
    require_agent(x_agent_secret)
    data = body.image_base64.split(",", 1)[-1].strip()
    try:
        jpeg = resize_image(base64.b64decode(data, validate=True))
    except (binascii.Error, ValueError, OSError):
        raise HTTPException(422, "image_base64 is not a valid image")
    try:
        response = claude_client().with_options(timeout=TIMEOUT_S).messages.create(
            model=claude_model(),
            max_tokens=64,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(jpeg).decode()}},
                {"type": "text", "text": PROMPT},
            ]}],
            tools=[COLOR_TOOL],
            tool_choice={"type": "tool", "name": "top_color"},
            thinking={"type": "disabled"},
        )
        color = next((b.input.get("color") for b in response.content if b.type == "tool_use"), None)
    except Exception as e:  # demo helper: never fail loudly, the replay falls back to the live path
        log.warning("demo-color failed: %s: %s", type(e).__name__, str(e)[:200])
        color = None
    return {"color_family": color if color in COLORS else None}
