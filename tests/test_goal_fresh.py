"""Offline tests for fresh savings goals (backend/services/goal.py): a new goal after a reached one counts only
savings from its start, edits keep the start, and goals stored before start_cents existed count everything.

Run from the project root:  python -m unittest tests.test_goal_fresh
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId  # noqa: E402

from backend.services import goal as goal_service  # noqa: E402


class FakeUsers:
    def __init__(self):
        self.updates = []

    def update_one(self, query, update):
        self.updates.append((query, update))


NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


class FreshGoalTest(unittest.TestCase):
    def setUp(self):
        self.users = FakeUsers()
        self.saved = 8790  # $87.90 already in Savings
        patches = [
            mock.patch("backend.db.users", self.users),
            mock.patch.object(goal_service, "saved_cents", lambda user, budget_s=None: (self.saved, "nessie")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.user = {"_id": ObjectId()}

    def test_fresh_goal_starts_at_zero(self):
        user = goal_service.set_goal(self.user, "coat", 10000, NOW, fresh=True)
        self.assertEqual(user["goal"]["start_cents"], 8790)
        self.assertEqual(goal_service.goal_out(user)["goal"]["saved_cents"], 0)
        self.saved = 8790 + 2500  # one more $25 skip
        out = goal_service.goal_out(user)["goal"]
        self.assertEqual((out["saved_cents"], out["percent"], out["reached"]), (2500, 25, False))

    def test_edit_keeps_the_start(self):
        user = goal_service.set_goal(self.user, "coat", 10000, NOW, fresh=True)
        self.saved = 12000
        edited = goal_service.set_goal(user, "winter coat", 3000, NOW)  # rename and lower the target
        self.assertEqual(edited["goal"]["start_cents"], 8790)
        out = goal_service.goal_out(edited)["goal"]
        self.assertEqual((out["name"], out["saved_cents"], out["reached"]), ("winter coat", 3210, True))

    def test_goal_without_start_counts_all_savings(self):
        user = {**self.user, "goal": {"name": "trip", "target_cents": 20000, "created_at": NOW}}
        self.assertEqual(goal_service.goal_out(user)["goal"]["saved_cents"], 8790)

    def test_first_goal_without_fresh_keeps_old_behavior(self):
        user = goal_service.set_goal(self.user, "trip", 20000, NOW)  # not fresh: start 0, counts everything
        self.assertEqual(goal_service.goal_out(user)["goal"]["saved_cents"], 8790)

    def test_never_negative(self):
        user = goal_service.set_goal(self.user, "coat", 10000, NOW, fresh=True)
        self.saved = 5000  # an undo moved money back below the start
        self.assertEqual(goal_service.goal_out(user)["goal"]["saved_cents"], 0)


if __name__ == "__main__":
    unittest.main()
