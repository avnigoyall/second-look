---
name: disagreement-analyst
description: Explain why the rule label and the agent label differ for one style, as ranked hypotheses with evidence and at least one tool check. Use only in disagreement mode.
---

# Disagreement analyst

Input: one style where `rule_label` differs from `agent_label`, the stored
decision (reasons and evidence_ids), and the rule metrics.

Everything you write is a hypothesis about Bluesky sample chatter, not a
finding about the market. Mark it that way.

## Steps

1. Read the stored decision and the rule inputs (`run_rule_metrics`). Note which
   rule threshold drove the rule label. Look the threshold name up in
   `/config/social_micro_trend.json`; do not copy numbers into your text.
2. Form 1 to 4 candidate hypotheses. Common ones:
   - The rule threshold is near the boundary for this style.
   - Low precision: off-topic posts inflate or shape the counts.
   - Seasonality: a repeating yearly peak read as a single wave.
   - Overlap with another style splits or doubles the signal.
   - Saturation censoring distorts the peak or decay.
   - Right censoring: the run had not ended in the last observed week.
   - The agent over-weighted press or a small cluster.
3. Run at least one tool check that could falsify the top hypothesis (for
   example `get_weekly_series`, `sample_posts`, `get_connectivity`). Stay within
   the per-style budget of 12 tool calls.
4. Rank the hypotheses by how well the evidence supports them. For each give:
   the statement, the evidence_ids, the tool check you ran (if any) and what it
   showed, and a confidence (`low`, `medium`, `high`).
5. Call `write_disagreement` once with the ranked list, an overall confidence,
   and which label the evidence favors (`rule`, `agent`, or `unclear`).

## Rules

- Never edit config files, trend documents, or decisions. You only write
  through `write_disagreement`.
- Never propose a new threshold value. You may say a threshold "looks too
  strict or too loose for this style" as a hypothesis.
- If no check can separate the hypotheses, say `unclear`.
