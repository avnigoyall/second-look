"""End-to-end check of the Radar decision path in test mode (PART G).

Seeds a throwaway local Mongo database (secondlook_radar_e2e) with three social_trends docs and their Radar
decisions, then drives the real code: micro_trend rules, radar_read, the timeline endpoint (FastAPI TestClient)
and verdict.decide with guardrails. Only the outside calls are replaced: Claude replies are scripted, and the
closet, attribute and image calls are stubbed. No network, no paid call.

Checks:
1. A known micro_trend style gives allowed micro_trend with Bluesky sample wording; a scripted "everyone" reply
   is replaced and logged as market_wide_claim.
2. A staple (established) style does not allow micro_trend and cites no sample numbers.
3. A nonsense query returns unknown: the timeline endpoint 404s, social context is None, and a stored style with
   too little data gets rule_label unknown.

SOCIAL_RADAR_ENABLED is set to 1 only inside this process. Exit code 0 when every check passes, 1 otherwise.

Run: .venv/bin/python scripts/radar_e2e_check.py   (needs local Mongo at mongodb://127.0.0.1:27017)
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from functools import partial
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOCAL_URI = "mongodb://127.0.0.1:27017"
os.environ["MONGODB_URI"] = LOCAL_URI  # never touch Atlas from the check
os.environ.setdefault("NESSIE_API_KEY", "")
os.environ["SECONDLOOK_WARMUP"] = "0"
os.environ["SOCIAL_RADAR_ENABLED"] = "1"  # this process only

from bson import ObjectId
from pymongo import MongoClient

E2E_DB = "secondlook_radar_e2e"
MICRO = "e2e micro style"
STAPLE = "e2e staple style"
THIN = "e2e thin style"
NONSENSE = "zqxv plorbnik 7731"
RUN_ID = "e2e-run"

USER = {"_id": ObjectId(), "email": "e2e@secondlook.test", "preference_note": None}
ITEM = {"name": "Wide-leg denim jeans", "image_url": "https://store.example/p/1.jpg", "price": 79.0}
ATTRS = {"status": "ok", "is_apparel": True, "category": "jeans", "color_family": "blue", "fabric": "denim",
         "fabric_confidence": "medium", "cut": "wide", "style": "basic", "description": "Blue wide-leg jeans.",
         "target_box_2d": None, "other_garments_visible": None, "target_crop": None}
SHORT_TREND = {"name": "Wide-leg jeans", "category": "jeans",
               "features": {"cut": "wide", "color": None, "fabric": "denim"}, "lifespan_bucket": "short",
               "lifespan_label": "In a Bluesky sample, active about 9 weeks",
               "source_titles": ["Bluesky search"]}
TREND_DOC = {"_id": ObjectId(), "name": "Wide-leg jeans", "category": "jeans", "origin": "agent",
             "lifespan_label": SHORT_TREND["lifespan_label"],
             "sources": [{"title": "Bluesky search", "url": "https://bsky.app/search?q=e2e+micro+style"}]}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}{(': ' + detail) if detail and not ok else ''}")
    if not ok:
        failures.append(name)


def social_doc(term: str, counts: list[int], corpus: int = 1000) -> dict:
    """social_trends shape: two partial edge windows around the metric weeks."""
    start = date(2025, 11, 10)
    full = [0, *counts, 0]
    weekly = [{"week": (start + timedelta(weeks=i)).isoformat(), "count": c, "corpus_count": corpus,
               "normalized": c / corpus, "saturated": False, "complete": 0 < i < len(full) - 1}
              for i, c in enumerate(full)]
    return {"term": term, "weekly_counts": weekly, "observed_weeks": 0, "post_count": sum(full),
            "weeks_to_breadth_3": 2, "concentration": 0.3, "breadth": 5, "momentum": "fading",
            "confidence": "medium"}


def series(at: int, values: list[int], n: int = 51) -> list[int]:
    out = [0] * n
    out[at:at + len(values)] = values
    return out


def decision(term: str, rule_label: str, agent_label: str) -> dict:
    return {"style": term, "run_id": RUN_ID, "rule_label": rule_label, "agent_label": agent_label,
            "final_label": agent_label, "decision_source": "agent", "agree": rule_label == agent_label,
            "confidence": "medium", "reasons": [f"e2e fixture decision for {term}"], "evidence_ids": ["e2e:1"],
            "metric_version": "e2e", "prompt_version": "e2e", "model_id": "e2e",
            "created_at": "2026-11-15T00:00:00Z"}


def reply(verdict: str, reason: str, trend: str | None = None) -> dict:
    return {"verdict": verdict, "reason": reason, "matched_closet_ids": [], "matched_trend_name": trend,
            "confidence": 0.8}


def seed(db) -> None:
    db.client.drop_database(E2E_DB)
    spike = [2, 10, 60, 80, 60, 20, 6, 2, 1]
    db.social_trends.insert_many([
        social_doc(MICRO, series(20, spike)),
        social_doc(STAPLE, [40] * 51),
        social_doc(THIN, series(20, [3, 4, 2])),
    ])
    db.radar_decisions.insert_many([decision(MICRO, "micro_trend", "micro_trend"),
                                    decision(STAPLE, "established", "established")])


def run_verdict(v, trends: list[dict], replies: list, term: str | None, sample_db):
    """verdict.decide with outside calls stubbed. Returns (response, record, claude_payloads)."""
    calls: list[dict] = []
    queue = list(replies)

    def fake_claude(payload, images, timeout_s):
        calls.append(payload)
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    ctx = None if term is None else {"term": term, "similarity": 0.8, "weekly_counts": [], "momentum": "fading",
                                     "confidence": "medium", "examples": [], "estimate": True}
    real_lookup = v.radar_read.sample_lifespan_for_term
    patches = [
        mock.patch.object(v.verdict_deps, "understand_for_verdict", return_value=dict(ATTRS)),
        mock.patch.object(v, "find_matches", return_value=[]),
        mock.patch.object(v, "get_trends_for_prompt", return_value=trends),
        mock.patch.object(v, "closet_size", return_value=20),
        mock.patch.object(v, "fetch_image", return_value=b"\xff\xd8jpeg"),
        mock.patch.object(v, "closet_docs", return_value={}),
        mock.patch.object(v, "find_trend_doc", side_effect=lambda n: TREND_DOC if n == TREND_DOC["name"] else None),
        mock.patch.object(v.verdict_deps, "alternatives_for_trend", return_value=[]),
        mock.patch.object(v, "call_claude", side_effect=fake_claude),
        mock.patch.object(v, "claude_model", return_value="claude-e2e"),
        mock.patch.object(v, "thresholds", return_value={"look_similarity": 0.80, "gray_margin": 0.04,
                                                          "require_color_match": True}),
        mock.patch.object(v.verdicts, "insert_one", side_effect=AssertionError("save=False must not write")),
        mock.patch.object(v, "social_context", return_value=ctx),
        # the real lookup, reading the seeded e2e database
        mock.patch.object(v.radar_read, "sample_lifespan_for_term", side_effect=partial(real_lookup, db=sample_db,
                                                                                       runs_dir=ROOT / ".nonexistent")),
    ]
    for p in patches:
        p.start()
    try:
        response, record = v.decide(USER, dict(ITEM), save=False)
    finally:
        for p in reversed(patches):
            p.stop()
    return response, record, calls


MARKET_WORDS = ("everyone", "all shoppers", "everybody", "the whole market")


def main() -> int:
    client = MongoClient(LOCAL_URI, serverSelectionTimeoutMS=3000)
    try:
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL local Mongo unreachable at {LOCAL_URI}: {type(exc).__name__}")
        return 1
    db = client[E2E_DB]
    seed(db)
    try:
        from fastapi.testclient import TestClient

        from backend.main import app
        from backend.services import micro_trend, social, social_ask
        from backend.services import verdict as v

        rule = {t: micro_trend.compute_rule_metrics(db.social_trends.find_one({"term": t}, {"_id": 0}))
                for t in (MICRO, STAPLE, THIN)}
        check("rules: seeded micro style is micro_trend", rule[MICRO]["rule_label"] == "micro_trend",
              rule[MICRO]["rule_label"])
        check("rules: seeded staple is established", rule[STAPLE]["rule_label"] == "established",
              rule[STAPLE]["rule_label"])
        check("rules: thin style is unknown", rule[THIN]["rule_label"] == "unknown", rule[THIN]["rule_label"])

        # 1. known micro_trend style
        good = ("These fit the wide-leg jeans trend. In a Bluesky sample, posts rose in 2 weeks and "
                "stayed active 6 weeks.")
        resp, rec, calls = run_verdict(v, [SHORT_TREND], [reply("micro_trend", good, SHORT_TREND["name"])], MICRO,
                                       db)
        sample = (calls[0] if calls else {}).get("social_sample_lifespan")
        check("micro: micro_trend is allowed", "micro_trend" in rec["signals"]["allowed"], str(rec["signals"]))
        check("micro: verdict is micro_trend", resp.verdict == "micro_trend", resp.verdict)
        check("micro: payload carries sample lifespan from the seeded decision",
              bool(sample) and sample.get("source") == "Bluesky sample", str(sample))
        check("micro: reason is sample-attributed", "Bluesky sample" in resp.reason, resp.reason)
        check("micro: no market-wide claim", not any(w in resp.reason.lower() for w in MARKET_WORDS), resp.reason)

        bad = "Everyone wore wide-leg jeans for 6 weeks."
        resp, rec, _ = run_verdict(v, [SHORT_TREND], [reply("micro_trend", bad, SHORT_TREND["name"])], MICRO, db)
        rules_hit = [o.get("rule") for o in rec.get("overrides", [])]
        check("micro: 'everyone' reply is replaced", "veryone" not in resp.reason and "Bluesky sample" in resp.reason,
              resp.reason)
        check("micro: override logged as market_wide_claim", "market_wide_claim" in rules_hit, str(rules_hit))

        # 2. staple: no short trend, established decision
        resp, rec, calls = run_verdict(v, [], [reply("micro_trend", good, "staple")], STAPLE, db)
        check("staple: micro_trend not allowed", "micro_trend" not in rec["signals"]["allowed"], str(rec["signals"]))
        check("staple: verdict is not micro_trend", resp.verdict != "micro_trend", resp.verdict)
        check("staple: no sample numbers in payload", not (calls and calls[0].get("social_sample_lifespan")))
        check("staple: no sample wording", "Bluesky sample" not in resp.reason, resp.reason)
        resp, rec, calls = run_verdict(v, [SHORT_TREND], [reply("good_buy", "A solid everyday pair.")], STAPLE, db)
        check("staple with short trend listed: no sample lifespan for an established style",
              not (calls and calls[0].get("social_sample_lifespan")), str(calls[0] if calls else None))

        # 3. nonsense query and thin data
        with mock.patch.object(social_ask, "database", return_value=db):
            tc = TestClient(app)
            r = tc.get(f"/social/trends/{NONSENSE}/timeline")
            check("nonsense: timeline returns 404", r.status_code == 404, str(r.status_code))
            r = tc.get(f"/social/trends/{THIN}/timeline")
            body = r.json() if r.status_code == 200 else {}
            check("thin: timeline rule_label unknown", body.get("rule_label") == "unknown", str(body)[:200])
            check("thin: no sample lifespan", body.get("sample_lifespan") is None)
            r = tc.get(f"/social/trends/{MICRO}/timeline")
            body = r.json() if r.status_code == 200 else {}
            check("micro: timeline shows agent decision", body.get("decision_source") == "agent"
                  and body.get("final_label") == "micro_trend", str(body)[:200])
        check("nonsense: social context is None", social.context_from_hits([]) is None)
        resp, rec, calls = run_verdict(v, [SHORT_TREND], [reply("good_buy", "A solid everyday pair.")], None, db)
        check("nonsense: no social sample in verdict", not (calls and calls[0].get("social_sample_lifespan"))
              and "Bluesky sample" not in resp.reason, resp.reason)
    finally:
        client.drop_database(E2E_DB)
        client.close()

    print(f"\n{'ALL PASS' if not failures else f'{len(failures)} FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
