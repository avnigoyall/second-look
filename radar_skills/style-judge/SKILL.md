---
name: style-judge
description: Decide whether one fashion style in the Bluesky sample was a micro_trend, established, still_active, or unknown. Use for every per-style decision before calling write_decision.
---

# Style judge

You judge one style at a time from Bluesky sample chatter. You do not judge the
real-world market. Every claim you write must say "in the Bluesky sample" or
equivalent, and must cite evidence_ids returned by tools.

## Thresholds live in config, not here

- Rule thresholds and their meaning: `/config/social_micro_trend.json`.
  Read it with `read_file` once per run if you need a number. Never invent or
  remember a threshold. If a value is missing from the config, say so in your
  reasons instead of guessing.
- Manual styles: `/config/manual_labels.json`. These are decided by hand. If
  the style is listed there, stop and do not call `write_decision`.

## Budget

Hard cap of 12 tool calls per style (skill and config reads are not data tool
calls, but keep them few). Plan before calling. A typical path:

1. `get_style_stats` (volume, observed weeks, peak, decay).
2. `run_rule_metrics` (the rule label and its inputs).
3. `get_weekly_series` only if the shape is unclear from the stats.
4. `sample_posts` to check precision (see the precision-auditor skill).
5. `get_connectivity` for community spread, `get_press_sources` for press.
6. `write_decision`.

Skip tools whose answer cannot change the outcome.

## Outcomes

- `micro_trend`: a fast rise, a short active run, and a clear decay inside the
  observed window, with spread beyond one small cluster.
- `established`: long steady presence, no single sharp peak, or a long active
  run relative to the config limit.
- `still_active`: the active run has not ended by the last observed week, so
  lifespan is right-censored. Do not call this a micro trend yet.
- `unknown`: data rails fail or evidence conflicts. Prefer unknown to a guess.

## How to weigh each signal

- **Rise speed**: weeks from first sustained activity to peak. Fast rise is
  necessary for micro_trend but not sufficient.
- **Lifespan**: use only the active run and lifespan computed by tools
  (`run_rule_metrics`, `write_decision`). Never compute or state your own
  lifespan number. The rule's active-run limits are the `test2_*` keys.
- **Decay**: compare recent activity to peak (`half_fraction_of_peak`,
  `still_active_min_fraction_of_peak` in the config). A rise with no decay is
  still_active, not micro_trend.
- **Breadth**: distinct authors relative to posts (`test4_*` keys). Many posts
  from few authors is one voice, not a trend.
- **Community spread**: from `get_connectivity`. Activity inside one tight
  cluster is weaker evidence than activity across communities.
- **Interaction share**: likes, reposts and replies per post. High volume with
  near-zero interaction suggests bots, shops, or spam.
- **Precision of sampled posts**: if the sampled posts are mostly about
  something else (a band, a recipe, a meme), discount the counts. Low precision
  can force unknown.
- **Press corroboration**: press mentions from `get_press_sources` support a
  decision but never replace Bluesky evidence. No press is not evidence against.
- **Seasonality**: styles such as leg warmers, capri pants and crochet top
  return every year. A peak that repeats at the same season is seasonal
  interest, usually established, not a micro trend. The config lists
  `seasonal_categories`; the rule output says `seasonal` and `category`. If
  fewer than two seasons are observed, say the seasonality is untested and
  lower confidence.
- **Overlap between styles**: the heels family (kitten heels, mary jane heels
  and similar) and silhouettes such as balloon and barrel pants share posts. A
  post can match more than one term. Do not count the same wave twice as
  evidence for two independent micro trends; mention the overlap.
- **Saturation censoring**: if the ingest hit the weekly cap
  (`saturation_cap_posts_per_week`) in peak weeks, the true peak is higher than
  measured and the decay may be an artifact. The rule output reports
  `saturated_weeks`, `saturated_share` and `peak_censored`. Treat measured decay
  after a capped week with caution.

## When to say unknown

Say unknown when any of these hold:

- Posts or weeks with posts are below the data rails (`min_posts`,
  `min_weeks_with_posts` in `/config/social_micro_trend.json`).
  `write_decision` refuses any other label in that case.
- Sampled post precision is too low to trust the counts.
- Rise, decay and spread point to different outcomes and no tool call can
  resolve it inside the budget.

## Confidence

Use `low`, `medium` or `high`. High needs posts and weeks with posts at the
`high_confidence_*` levels in the config, good precision, a small
`saturated_share`, and agreement between shape and spread. Otherwise medium or
low.

## Writing the decision

Call `write_decision` once with: `agent_label` (the outcome), `confidence`,
2 to 5 short `reasons` (each tied to evidence), and the `evidence_ids` you
relied on. If it refuses (for example thin data or an unknown id), read the
error, fix the call, and try once more. Follow the
analyst-note-writer skill for wording. Never edit files.
