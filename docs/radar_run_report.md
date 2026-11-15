# Radar decision pipeline: run report (Nov 15, 2026)

Branch `feat/radar-decisions`, created from `feat/micro-trend`, which was created from `main` at 0ecf8c0.
`feat/social-radar` did not exist, and commit 934c245 was already on main. Nothing is pushed.
`SOCIAL_RADAR_ENABLED` stays off: it is not set in `.env`, and the default is off.
Every number below was measured in this run. The orchestrator re-ran or re-queried each one, except where a line says otherwise.

## Phases

| phase | result | verified by the orchestrator |
|---|---|---|
| 0 setup | passed | branch, `.social_runs/` already ignored |
| PART 0 seed | skipped | `seed/trends_seed_press.json` does not exist |
| 1 R rules (PART A, manual labels) | passed | 20 tests; recomputed `rule_label` for all 42 Atlas styles, matches the stored values |
| 1 T tools, spend (SPEND, PART B, B2) | passed | 35 tools and spend tests, 20 materials tests |
| 1 S skills, agent (PART C scaffold) | passed | 22 tests; 4 SKILL.md files with valid frontmatter |
| 2 X run (PART C run, D, E) | passed | decisions, agreement, confusion matrix, disagreements, variation and trend count re-queried from Atlas |
| 2 W surface (PART F, G) | passed | covered by the full suite and `scripts/radar_e2e_check.py` |
| 3 end-to-end check | passed | `scripts/radar_e2e_check.py`: 21/21 checks, exit 0 |
| 3 full suite | passed | 572 tests OK (baseline before this run: 460) |
| 3 node harnesses | passed | card 97/97, popup all pass, social 39/39 (Playwright 1.61.1) |

## Labels by decision_source (run `radar-main-1`, 42 styles)

| decision_source | established | still_active | unknown | micro_trend | total |
|---|---|---|---|---|---|
| agent | 28 | 1 | 6 | 0 | 35 |
| rule | 0 | 0 | 0 | 0 | 0 |
| manual | 3 | 1 | 0 | 3 | 7 |

- Rule labels for all 42 styles (config hash 81c057ac90b06418): 24 established, 14 unknown, 4 still_active, 0 micro_trend.
- No style that the agent or the rule decided ended up micro_trend. So `scripts/radar_to_trends.py` wrote 0 trend documents (Atlas `trends` with origin agent: 0).
- The agent model was `anthropic:claude-opus-5-5`, with prompt `radar_prompt_v1`. There were no agent errors and no fallbacks to the rule label.

## Agreement (35 styles with both labels; manual styles are excluded)

The agent agreed with the rule on 21 of 35 styles (60 percent). Rows are rule_label, columns are agent_label:

| rule \ agent | established | still_active | unknown |
|---|---|---|---|
| established | 20 | 0 | 4 |
| still_active | 1 | 1 | 2 |
| unknown | 7 | 0 | 0 |

The disagreement analyst stored hypotheses for all 14 disagreements (Atlas `radar_disagreements`, 14 documents). Its main themes, per X's report:
- 7 styles: active across the whole window. The rule leaves them unknown because the run touches a window edge. The agent says established.
- 6 styles: the sampled posts are mostly off-topic. The agent says unknown.
- 1 style: a step change in corpus size creates the rule's peak.

X also reports that corpus_count rises about 3x from the week of 2026-08-31, in 11 styles. This is a hypothesis; nobody checked it against the ingest logs. All details are in `docs/radar_decisions_report.md`.

Run-to-run variation: the same 5 random styles were decided in 3 runs (radar-main-1, radar-var-a, radar-var-b). 0 of 5 labels changed; all 5 were established in every run. X reports that confidence changed for 2 of the 5.

## Manual labels (config/manual_labels.json)

These are editorial labels. They are not Radar data. They are excluded from agreement statistics, never set a lifespan, never create trend documents, and never unlock the micro_trend verdict. The orchestrator verified that no manual decision in Atlas has lifespan fields.

| style | label | confidence | source |
|---|---|---|---|
| plain black tee | established | medium | Claude judgment, unverified |
| mid rise jeans | established | medium | Claude judgment, unverified |
| mesh flats | micro_trend | low | Claude judgment, unverified |
| micro bag | micro_trend | low | Claude judgment, unverified; probably past peak |
| balloon jeans | micro_trend | low | Claude judgment, unverified; probably past peak |
| slim sneakers | still_active | low | Team judgment (Hiresh) |
| soft suiting | established | low | Team judgment (Hiresh) |

## Materials photo estimate (PART B2)

From `docs/eval_materials.md`: accuracy is 17/30 = 56.7 percent (n = 30; an unknown answer counts as wrong). Always guessing the most common material scores 16.7 percent on the same sample, so the estimate beats that. The sample was stratified, which is why that baseline is low.

The test set was Shopify retailer items, not Uniqlo. The output line therefore says "30-item Shopify retailer test".

The `feat/materials` merge was a no-op: it was already merged into main (0ecf8c0), and `../phacks-materials` does not exist. All 30 stored item_materials documents state a composition, so the photo tool never runs on them. `MATERIALS_PHOTO_ENABLED` is not set in `.env`.

## Spend (ledger `.social_runs/spend_ledger.json`)

| provider | ledger total | cap | share |
|---|---|---|---|
| Claude | 7.6877 USD (346 entries) | 400 USD | 1.9 percent |
| Vultr | 0 USD in this run (no Vultr calls) | 100 USD | 0 percent |

Measured per style: the pilot averaged 0.1504 USD over 3 styles, and the full run averaged 0.1257 USD over 35 styles.

The ledger counts only the pipeline's own API calls, and its totals are the code's estimates. The provider usage pages hold the true numbers.

Vultr publishes no embedding price, so the ledger charges qwen3-embedding-4b at the published output rate of 2.75 USD per million tokens, as an upper bound. This is an assumption. T reports that the earlier embedding jobs are logged at about 6.58 USD in a separate Vultr log, and that it counts this against the Vultr cap. The orchestrator did not check that figure.

## Not done, and limits

- PART 0 press seed was skipped because the file is missing. No seeded trend has sources, so `get_press_sources` returns nothing, and no lifespan can be set: rail 3 needs a lifespan_source_url that is one of the trend's sources.
- PART G prewarm: `config/demo_products.json` does not exist. `scripts/prewarm_social.py` prints "skipped" and exits 0. The CLI also cannot fill a running server's in-memory caches; that needs a hook in `backend/warmup.py`.
- The interaction-only version of test 4 is "not computed", because there is no per-term interaction-only data.
- No Radar style is micro_trend. A live micro_trend verdict from Radar data is therefore not possible today. The end-to-end check proves the path with seeded test data only.
- Counts are first-page Bluesky search samples. 7 peak weeks are censored at the weekly cap, and 15 styles have more than 25 percent saturated weeks. The thresholds are provisional.
- Lint: the new Radar files pass ruff. About 389 older ruff findings remain in `scripts/`, `backend/` and `tests/`. The global ruff config flags them, and the project has no ruff config of its own. They were not fixed in this run.
- CONTRACT.md was not edited. This report and `docs/radar_*.md` describe the new collections and endpoint.

## Resume commands

```
cd ~/LocalFiles/phacks
export RADAR_AGENT_MODEL=anthropic:claude-opus-5-5
.venv/bin/python scripts/radar_agent.py --resume --run-id radar-main-1          # skips styles already decided
.venv/bin/python scripts/radar_agent.py --all --redecide --run-id <new-run-id>  # full re-decision
.venv/bin/python scripts/radar_agent.py --disagreements --run-id radar-main-1
.venv/bin/python scripts/radar_to_trends.py --run-id radar-main-1               # idempotent
.venv/bin/python -m backend.services.micro_trend --write                        # recompute rule labels
.venv/bin/python scripts/radar_e2e_check.py                                     # needs local Mongo
MONGODB_URI=mongodb://127.0.0.1:27017 NESSIE_API_KEY='' SECONDLOOK_WARMUP=0 SOCIAL_RADAR_ENABLED=0 \
  .venv/bin/python -m unittest discover tests
```
