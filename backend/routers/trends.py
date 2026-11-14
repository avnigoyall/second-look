"""Part 4 trend endpoints. Trends are written offline by scripts/seed_trends.py and scripts/run_trend_agent.py."""
from fastapi import APIRouter, Depends

from backend import models as m
from backend.auth import current_user
from backend.db import trends

router = APIRouter(tags=["trends"])


@router.get("/trends", response_model=m.TrendsResponse)
def list_trends(user=Depends(current_user)):
    """All trends, seed and agent, sorted by name. Example image data is not included."""
    pipeline = [
        {"$addFields": {"example_image_count": {"$size": {"$ifNull": ["$example_images", []]}}}},
        {"$project": {"example_images": 0}},
        {"$sort": {"name": 1}},
    ]
    docs = trends.aggregate(pipeline)
    return {"trends": [m.TrendSummary(id=str(d.pop("_id")), **d) for d in docs]}
