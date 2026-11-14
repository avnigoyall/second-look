"""Savings goal endpoints (CONTRACT.md section 15). Logic lives in backend/services/goal.py."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from backend import models as m
from backend.auth import current_user
from backend.services import goal as goal_service

router = APIRouter(tags=["goal"])


@router.get("/goal", response_model=m.GoalResponse)
def get_goal(user=Depends(current_user)):
    """{goal: null} or the goal with saved_cents, percent, reached and source (nessie or local)."""
    return goal_service.goal_out(user)


@router.put("/goal", response_model=m.GoalResponse)
def put_goal(body: m.GoalRequest, user=Depends(current_user)):
    """Create or update the one goal. name trimmed, 1 to 40 characters. target_cents 500 to 10000000. Else 422."""
    user = goal_service.set_goal(user, body.name, body.target_cents, datetime.now(timezone.utc))
    return goal_service.goal_out(user)


@router.delete("/goal", response_model=m.GoalResponse)
def delete_goal(user=Depends(current_user)):
    """Removes only the goal. Skips, verdicts and Savings are untouched. Idempotent."""
    goal_service.delete_goal(user)
    return {"goal": None}


@router.get("/goal/items", response_model=m.GoalItemsResponse)
def goal_items(user=Depends(current_user)):
    """Skipped items, newest first, at most 100. Works with or without a goal."""
    return {"items": goal_service.skipped_items(user["_id"])}
