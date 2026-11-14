#!/usr/bin/env python3
"""Precompute sampled social metrics. No Nessie calls.

  python scripts/social_metrics.py
  python scripts/social_metrics.py --dry-run
  python scripts/social_metrics.py --calibrate

Calibration tunes only a numeric fading threshold on labeled terms with enough
posts and observed weeks. It is an in-sample diagnostic, not held-out accuracy.
The classifier never receives a term label. Small samples remain uncalibrated.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE = ROOT / ".social_runs" / "ingest_state.json"
sys.path.insert(0, str(ROOT))
from backend.services.social import (CONFIG_PATH, compute_trends, momentum_config, preview_recent_momentum,
                                     query_window_coverage, query_window_visited, task_saturated,
                                     task_saturation_known, utc)  # noqa: E402


def coverage_audit(state):
    """Read local cursor metadata without a database, text, accounts or credentials."""
    tasks = state.get("tasks", [])
    rows = []
    for term in sorted({t["term"] for t in tasks}):
        group = [t for t in tasks if t["term"] == term]
        rows.append({"term": term, "total_windows": len(group),
                     "completed_windows": sum(query_window_coverage([t], utc(t["since"]), utc(t["until"])) == "complete"
                                              for t in group),
                     "visited_windows": sum(query_window_visited([t], utc(t["since"]), utc(t["until"])) for t in group),
                     "saturated_windows": sum(task_saturated(t) for t in group),
                     "saturation_metadata_windows": sum(task_saturation_known(t) for t in group),
                     "saturation_unknown_windows": sum(not task_saturation_known(t) for t in group),
                     "legacy_boundary_risk_windows": sum(bool(t.get("equal_timestamp_boundary_risk")) for t in group)})
    return {"window_start": state.get("window_start"), "window_end": state.get("window_end"),
            "terms": rows, "writes": False,
            "strict_policy_note": "Any incomplete complete-calendar term or corpus window keeps published momentum "
                                  "unknown. A single unresolved corpus term can therefore affect every term."}


def load_labels(path=ROOT / "config" / "social_terms.json") -> dict[str, str]:
    value = json.loads(Path(path).read_text())
    if isinstance(value, dict):
        rows = value.get("terms", [])
    else:
        rows = value
    return {r.get("term", r.get("query", r.get("phrase", ""))): r.get("label", "unknown")
            for r in rows if isinstance(r, dict)}


def separation_report(metrics: list[dict], labels: dict[str, str], cfg=None) -> dict:
    """Label comparison is evaluation only, never a term-level classification input."""
    cfg = cfg or momentum_config()
    eligible = [m for m in metrics if labels.get(m["term"]) in ("micro", "staple")
                and m.get("post_count", 0) >= cfg["medium_confidence_posts"]
                and m.get("observed_weeks", 0) >= cfg["minimum_observed_weeks"]
                and m.get("decay_ratio") is not None and m.get("query_coverage_complete", True)]
    groups = {}
    for label in ("micro", "staple"):
        rows = [m for m in eligible if labels[m["term"]] == label]
        groups[label] = {"terms": len(rows), "posts": sum(m["post_count"] for m in rows),
                         "median_decay_ratio": statistics.median(m["decay_ratio"] for m in rows) if rows else None,
                         "median_breadth": statistics.median(m["breadth"] for m in rows) if rows else None,
                         "momentum_counts": {s: sum(m["momentum"] == s for m in rows)
                                             for s in ("rising", "peaking", "fading", "steady", "unknown")}}
    confusion = {"micro_predicted_micro": 0, "micro_predicted_staple": 0,
                 "staple_predicted_micro": 0, "staple_predicted_staple": 0}
    for row in eligible:
        predicted = "micro" if row["decay_ratio"] < cfg["fading_decay_ratio"] else "staple"
        confusion[f"{labels[row['term']]}_predicted_{predicted}"] += 1
    recalls = []
    for label in ("micro", "staple"):
        total = groups[label]["terms"]
        if total:
            recalls.append(confusion[f"{label}_predicted_{label}"] / total)
    balanced = sum(recalls) / 2 if len(recalls) == 2 else None
    enough = all(groups[label]["terms"] >= 3 for label in groups)
    separates = enough and balanced is not None and balanced >= 0.7
    return {"groups": groups, "confusion": confusion, "balanced_accuracy": balanced,
            "eligible_terms": len(eligible),
            "excluded_for_query_coverage": sum(not m.get("query_coverage_complete", True) for m in metrics
                                               if labels.get(m["term"]) in ("micro", "staple")),
            "sufficient_terms": enough, "separates": separates,
            "interpretation": ("In-sample fading ratios separate these annotated terms; this is not held-out accuracy."
                               if separates else "Staples and micro-trends do not separate reliably in this sample."),
            "limits": "Only terms with at least 100 posts, enough observed weeks and exhausted term/corpus "
                      "query windows are scored. Unvisited or truncated searches are not absence. Labels are noisy; "
                      "a micro-trend can still be rising. A fading rule is not a micro-trend identity classifier."}


def calibrate(metrics, labels, cfg):
    baseline = separation_report(metrics, labels, cfg)
    if not baseline["sufficient_terms"]:
        return cfg, {**baseline, "calibration_applied": False,
                     "calibration_reason": "Need at least three eligible micro and three eligible staple terms."}
    best = cfg
    best_score = baseline["balanced_accuracy"]
    for cutoff in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        candidate = {**cfg, "fading_decay_ratio": cutoff}
        score = separation_report(metrics, labels, candidate)["balanced_accuracy"]
        if score is not None and score > best_score:
            best, best_score = candidate, score
    best = {**best, "calibration": {"status": "in_sample", "method": "Grid search fading ratio only",
                                    "balanced_accuracy": best_score,
                                    "note": "No held-out term labels. Do not interpret as generalization accuracy."}}
    return best, {**separation_report(metrics, labels, best), "calibration_applied": True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="UTC ISO observation-window start")
    parser.add_argument("--end", help="UTC ISO exclusive observation-window end")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE,
                        help="Ingest cursor state for exact observation window and term/week search coverage")
    parser.add_argument("--dry-run", action="store_true", help="Compute and report without database or config writes")
    parser.add_argument("--calibrate", action="store_true", help="Evaluate labels and tune a numeric cutoff if eligible")
    parser.add_argument("--preview-momentum", action="store_true",
                        help="Read-only recent-eight-week proposal; no DB/config writes or published policy change")
    parser.add_argument("--coverage-audit", action="store_true", help="Audit local cursor state only, with no DB access")
    args = parser.parse_args(argv)
    if args.preview_momentum and args.calibrate:
        parser.error("--preview-momentum cannot be combined with --calibrate")
    if args.preview_momentum:
        args.dry_run = True
    try:
        state = json.loads(args.state.read_text()) if args.state.exists() else {}
        if args.coverage_audit:
            print(json.dumps(coverage_audit(state), indent=2))
            return 0
        from backend.db import db
        end = utc(args.end or state.get("window_end") or datetime.now(timezone.utc))
        start = utc(args.start or state.get("window_start") or end - timedelta(days=365))
        # Inspect/read existing social collections only. Other collections are untouched.
        posts = list(db["social_posts"].find({}, {"_id": 0, "uri": 1, "author_hash": 1,
                                                  "created_at": 1, "term": 1, "terms": 1, "source": 1}))
        communities = list(db["social_communities"].find({}, {"_id": 0, "author_hash": 1, "community_id": 1}))
        labels = load_labels()
        cfg = momentum_config()
        metrics = compute_trends(posts, communities, start=start, end=end, terms=labels, config=cfg, coverage=state)
        evaluation = separation_report(metrics, labels, cfg)
        if args.calibrate:
            cfg, evaluation = calibrate(metrics, labels, cfg)
            if evaluation.get("calibration_applied"):
                metrics = compute_trends(posts, communities, start=start, end=end, terms=labels, config=cfg, coverage=state)
                evaluation = {**separation_report(metrics, labels, cfg), "calibration_applied": True}
                if not args.dry_run:
                    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
        if not args.dry_run:
            db["social_trends"].create_index("term", unique=True)
            for row in metrics:
                db["social_trends"].update_one({"term": row["term"]}, {"$set": row}, upsert=True)
        print(json.dumps({"terms": len(metrics), "corpus_posts": len(posts), "dry_run": args.dry_run,
                          "coverage_state_available": bool(state.get("tasks")),
                          "terms_with_complete_query_coverage": sum(m["query_coverage_complete"] for m in metrics),
                          "non_unknown_momentum": sum(m["momentum"] != "unknown" for m in metrics),
                          "momentum_preview": preview_recent_momentum(metrics, cfg) if args.preview_momentum else None,
                          "evaluation": evaluation,
                          "metrics": [{k: v for k, v in m.items() if k != "weekly_counts"} for m in metrics]}, indent=2))
        return 0
    except Exception as exc:
        # Connection exception text can contain URLs and credentials. Report type only.
        print(json.dumps({"status": "unavailable", "error_type": type(exc).__name__,
                          "note": "Check local configuration and database availability; no metrics were fabricated."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
