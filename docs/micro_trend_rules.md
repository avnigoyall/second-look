# Micro-trend rule baseline

Code: `backend/services/micro_trend.py`. Thresholds: `config/social_micro_trend.json` (no thresholds in code). Manual labels: `config/manual_labels.json`. Tests: `tests/test_micro_trend.py` (20 tests, synthetic docs, no network).

Run: `.venv/bin/python -m backend.services.micro_trend [--write] [--json]` (reads MONGODB_URI from `.env`, db `secondlook`).

## PART 0 seed

Skipped. `seed/trends_seed_press.json` does not exist, so no press trends were loaded.

## Definitions

- **Corpus per week.** `weekly_counts[].corpus_count`, written by `backend.services.social.compute_trends`. It is the number of unique collected search posts (all terms, deduplicated by URI, thread-enrichment posts excluded) whose `created_at` falls in that UTC Monday week. It is a sample count, not a Bluesky total, and it is affected by the 20-post first-page cap of every term.
- **share_w** = `count / corpus_count` for the week, or 0 when `corpus_count` is 0.
- **metric_weeks.** Weekly entries from the first to the last entry with `complete=true` (the two partial edge windows are excluded). On the 42 Atlas docs this is 51 weeks for every term. The stored `observed_weeks` field (0 everywhere) is not used.
- **weeks_with_posts.** Metric weeks with `count >= 1`.
- **posts.** The doc `post_count` (whole observation window).
- **S_w.** 3-week centered mean of share_w (at the two ends of metric_weeks, the mean of the 2 available weeks). **P** = max S, **w\*** = first week reaching P.
- **Active run.** Active week: `S_w >= 0.10 P`. Grown outward from w\*, bridging up to 2 consecutive inactive weeks. weeks_active = end - start + 1 (bridged gaps included).
- **Test 1 fast rise.** ramp = (first run week with S >= 0.50 P) - (run start). Pass if ramp <= 4. Unknown if the run starts at the first metric week (rise not observed) or the peak is censored.
- **Test 2 short life.** Pass if weeks_active <= 16 with the run touching neither edge, OR S falls to <= 0.50 P within 8 weeks after w\* and does not rise above 0.50 P in the 8 weeks after the fall. Fail only if both paths fail. Otherwise unknown ("run touches window edge unresolved").
- **Test 3 not a staple.** Mean S over the first 8 metric weeks < 0.50 P, and category not seasonal.
- **Seasonal category.** Token match of the term against `category_tokens` (coats, boots, sandals, shorts, swimwear, knit sweaters), plus explicit `term_categories` overrides (empty). "bootcut" does not match "boots". No current term is seasonal. "quarter zip" is not mapped to knit sweaters; this is a judgment call.
- **Test 4 spreads fast.** `weeks_to_breadth_3 <= 4` and `concentration <= 0.5` from existing social_trends fields. Unknown if either is null. The interaction_only variant is "not computed": no per-term interaction-only breadth or concentration exists in Atlas.
- **Saturation.** saturated_share = saturated weeks / all weekly windows (the Contract basis, partial edges included). Above 0.25 caps confidence at medium (also capped when saturation metadata is missing). If week w\* is saturated, peak_censored = true and tests 1 and 2 are unknown with cause "censored at cap".
- **Indicators only.** span_75 = smallest contiguous run of metric weeks holding at least 75 percent of metric-week posts (raw counts). share_first_2_weeks = posts in the first two metric weeks starting at the first week with posts, divided by metric-week posts.

## Outcome order (rule_label = tests_1_3)

1. unknown: posts < 100, weeks_with_posts < 8, seasonal category, or no posts.
2. established: test 3 fails (left-censored: early mean >= half of peak).
3. still_active: run touches the right edge and the last S >= 0.50 P (right-censored).
4. micro_trend: tests 1, 2 and 3 all pass.
5. unknown otherwise, with reason codes per non-passing test: `testN_fail`, `testN_censored_at_cap`, `testN_edge_unresolved`.

tests_1_4 equals tests_1_3, except a micro_trend needs test 4 = pass (else unknown).

Confidence: low when data-short; high when posts >= 500 and weeks_with_posts >= 16; otherwise medium; caps above apply.

`measured_active_run(style)` returns `{start_week, end_week, weeks_active, peak_week}` or None for manual styles, data-short or seasonal styles, a censored peak, or a run touching either edge.

## Manual labels

Seven hard-coded entries (team or Claude judgment, not Radar data). `manual_decision(term)` returns `{final_label, decision_source: "manual", confidence, source_text, display: "Editorial label (team judgment, not Radar data)", excluded_from_agreement: true}` and never any lifespan field. `rule_label` is still computed by the rules. `measured_active_run` returns None for them.

## Measured results on the 42 Atlas social_trends docs (Nov 14, metric_version micro_trend_rules_v1, config hash 81c057ac90b06418)

Persisted to Atlas: `rule_label`, `rule_metrics`, `metric_version`, `rule_config_hash` on all 42 docs. No existing field changed.

| rule_label (tests 1-3) | count |
|---|---|
| established | 24 |
| unknown | 14 |
| still_active | 4 |
| micro_trend | 0 |

tests_1_4 gives the same counts (no style reached micro_trend, so test 4 changes nothing).

| reason code | docs |
|---|---|
| left_censored_staple | 24 |
| posts_below_min | 7 |
| test1_edge_unresolved | 6 |
| test2_fail | 5 |
| right_censored_active | 4 |
| weeks_with_posts_below_min | 1 |

A doc can carry several reasons. The 14 unknowns: 7 data-short (balloon jeans, mesh flats, micro bag, mid rise jeans, plain black tee, slim sneakers, soft suiting; all seven are also the manual-label styles), and 7 with an active run starting at the window edge or a long life (block heels, bubble skirt, capri pants, chunky sneakers, poplin shirt, slingback heels, straight leg jeans).

still_active: Levi's 501, cargo pants, cherry red, quarter zip.

Per test:

| test | pass | fail | unknown (cause) |
|---|---|---|---|
| 1 fast rise | 6 | 2 | 27 left-censored, 7 censored at cap |
| 2 short life | 8 | 24 | 3 edge unresolved, 7 censored at cap |
| 3 not a staple | 18 | 24 | 0 |
| 4 spreads fast | 13 | 27 | 2 field missing |

Peak censored at the weekly cap (7): Levi's 501, barrel jeans, butter yellow, high heels, platform heels, quarter zip, sambas.
Saturated share above 0.25 (15, confidence capped at medium): baggy jeans, butter yellow, cargo pants, cherry red, chinos, graphic tee, high heels, leg warmers, leopard print, low rise jeans, platform heels, quarter zip, sambas, skinny jeans, white t-shirt.
Confidence: 5 high, 30 medium, 7 low.

Reading: the 12-month window opens after most styles were already active, so the left-censoring rule (test 3) dominates. The rule produces no micro_trend on this corpus. That is a measured result, not evidence that no micro trends exist.
