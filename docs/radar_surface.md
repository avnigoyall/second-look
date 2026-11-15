# Radar surface: verdict wording, timeline, dashboard, prewarm

Files: `backend/services/radar_read.py` (new, read only), `backend/routers/social.py` (timeline endpoint),
`backend/services/verdict.py` (prompt wording, wording guardrail, sample numbers in the payload),
`dashboard/trends.html` (Radar panel, additive), `scripts/prewarm_social.py`. Tests:
`tests/test_radar_verdict_wording.py` (16, Claude mocked), `tests/test_radar_timeline.py` (12, local Mongo and
mocks). No test makes a network or paid call.

## PART F: verdict wording

Old prompt line: "Do not infer a trend's lifespan from sampled chatter alone." New wording:

- "Sampled chatter alone never makes an item a micro_trend."
- Optional `social_sample_lifespan` holds stored Bluesky sample numbers (`weeks_to_rise`, `weeks_active`) for a
  short-lived trend. The reason may describe that trend only as Bluesky sample evidence, with those exact numbers
  and the words "in a Bluesky sample". Never as market-wide fact. Never "everyone", "everybody", "all shoppers",
  "nationwide", "worldwide" or similar.
- The number rule now allows a week count from `social_sample_lifespan` when the reason says "in a Bluesky
  sample", in addition to a `lifespan_label` number with its source.

Code still decides:

- `allowed_verdicts` is unchanged. micro_trend is allowed only when a listed trend (`trends` collection) has
  `lifespan_bucket` "short". Radar data never adds micro_trend.
- `social_sample_lifespan` is fetched only when micro_trend is already allowed and a social context matched
  (so only with `SOCIAL_RADAR_ENABLED=1`). `radar_read.sample_lifespan_for_term(term)` returns numbers only when
  the Radar final label is micro_trend (latest agent or rule decision, else the rule label), the style is not
  manual, and the measured sample run is fully observed and 16 weeks or less. `weeks_to_rise` is the test 1 ramp
  (null when the rise was not observed). Any failure returns None and the verdict runs as before.
- New guardrail `wording_problem`, after the existing reason checks. Each hit is an override
  `{rule, code_said, claude_said}` and the reason is replaced by the template:
  - `market_wide_claim`: everyone, everybody, every shopper, all shoppers, all consumers, all buyers, most
    shoppers, the whole or entire market, market-wide, across the market, industry or country, nationwide,
    worldwide, the whole internet, all over the internet or social media, the whole world.
  - `sample_not_attributed`: a week count equal to a stored sample number without "Bluesky sample".
  - `unsourced_number`: a week count that is neither a stored sample number (micro_trend only) nor a number in a
    listed short trend's `lifespan_label`.
- Template for micro_trend with sample numbers (also used by the code-only fallback):
  "This fits the {trend} trend. In a Bluesky sample, posts rose in N weeks and stayed active M weeks. A classic
  version may get more wear." (the last sentence is dropped if the reason would pass 30 words). Without sample
  numbers the old hedged template is used.

## PART G: timeline endpoint

`GET /social/trends/{style}/timeline`, `style` is the URL-encoded `social_trends.term` (exact match, then a
case-insensitive match). 404 when no doc, 503 when Mongo fails. Response:

| Field | Meaning |
|---|---|
| `series` | every weekly window: week, count, corpus_count, share, smoothed_share (3-week, metric weeks only, else null), partial_edge, saturated |
| `markers` | `peak` (with `censored`), and `active_start` / `active_end` when a sample run is measured |
| `rule_label`, `rule_reasons`, `rule_confidence` | `micro_trend.compute_rule_metrics`, computed live |
| `agent_label`, `final_label`, `decision_source`, `reasons`, `confidence`, `agree` | latest decision. No decision yet: agent_label null, final_label = rule_label, decision_source "rule", reasons [] |
| `run_id`, `decided_at`, `model_id`, `prompt_version`, `evidence_count` | from the latest decision |
| `sample_run` | measured Bluesky sample run (start, end, weeks_active, weeks_to_rise), else null |
| `sample_lifespan` | the numbers the verdict may cite (see PART F), else null |
| `editorial` | manual styles only: `{display: "Editorial label (team judgment, not Radar data)", source_text}` |

Latest decision: `radar_read.latest_decision` takes the newest `created_at` across `radar_decisions` and every
`.social_runs/radar_runs/*/decisions.jsonl` line for the style (ties go to the database copy). Manual styles
always show the editorial label and source text, `decision_source` "manual", and never `sample_run`,
`sample_lifespan` or active-run markers.

Measured on Atlas (Nov 14, 17 decisions stored at the time): barrel jeans established / agent established;
cargo pants rule still_active / agent established; butter yellow rule established / agent unknown; mesh flats
manual micro_trend (editorial); a nonsense style returns 404. No style has a sample_lifespan, because no
non-manual style currently has a final micro_trend label with a fully observed short run.

## Dashboard

`/trends-ui` has a third panel, "Radar label and timeline", with its own style select (`?term=` preselects it).
Chart: weekly share bars, 3-week smoothed line, shaded measured sample run with its week count, peak dot,
saturated-week ticks, a hover crosshair with the week's counts. Labels panel: final, decided by, rule, agent,
confidence, the agent's reasons (evidence ids stripped, count shown below), the Bluesky sample sentence when
`sample_lifespan` exists, and for manual styles the "Editorial label (team judgment, not Radar data)" box with
the source text and no lifespan. Checked with Playwright 1.61.1 at 1280 and 390 px, light and dark, on real
data (barrel jeans, mesh flats) and a mocked micro_trend response for the band: no console errors, no
horizontal overflow. The dark-mode bar color was raised for contrast during that check.

## Prewarm

`scripts/prewarm_social.py` reads `config/demo_products.json` (a list, or `{"products": [...]}`, of /analyze
items with an optional `expected_term`). The file does not exist, so the script prints
`skipped: config/demo_products.json not found` and exits 0. With a file, per product it runs `check_budget("vultr")`
then the runtime lookup (`social._lookup`, without the sub-second deadline), prints the matched term and the
Radar label, and exits 1 if any `expected_term` differs. Caches are per process, so the CLI warms Vultr and
Atlas only. To fill a server's caches, call `prewarm_social.warm(products)` inside the server (for example from
`backend/warmup.py`, not done here) with the flag on. Measured with a temporary 2-product file: "Barrel leg jeans"
matched barrel jeans (similarity 0.882), "Mesh ballet flats" matched ballet flats (0.917).

## Driving the end-to-end check (scripts/radar_e2e_check.py)

Keep `SOCIAL_RADAR_ENABLED` off by default; set it only in the check's own process. Use `verdict.decide` with
the outside calls patched, as `tests/test_radar_verdict_wording.py` `EngineWordingTests` does (it reuses
`tests/test_verdict.py` `EngineTest`, which patches attributes, closet, trends and `call_claude`):

1. Known micro_trend style: trends = a short trend; patch `verdict.social_context` to return the style's context
   (or let it run with the flag on), and either patch `verdict.radar_read.sample_lifespan_for_term` or seed a
   social_trends doc plus a micro_trend decision. Expect `signals.allowed` to contain micro_trend,
   `payload["social_sample_lifespan"]` set, and a reason containing "In a Bluesky sample". A mocked Claude reply
   with "everyone" must come back replaced, with override `market_wide_claim`.
2. Staple: no short trend. Expect allowed `["good_buy"]`, no `social_sample_lifespan`, no sample wording.
3. Nonsense query: `GET /social/trends/<nonsense>/timeline` returns 404, and `social.context_from_hits` returns
   None (no social context), so the verdict path is unchanged. For a stored style with too little data the
   timeline shows `rule_label` "unknown".
