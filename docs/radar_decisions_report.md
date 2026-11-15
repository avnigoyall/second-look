# Radar decisions report (agent vs rule), Nov 15

Everything here describes Bluesky sample chatter (first-page search samples, 12-month window), not the market.
The agent's labels and the analyst's hypotheses are judgments on that sample. Counts below are measured from
the stored run documents. Costs are the spend ledger's estimates, not the provider's invoice.

## Runs

| run_id | what | styles | model_id | prompt_version |
|---|---|---|---|---|
| `radar-main-1` | decisions for all styles (pilot of 3, then the rest), then the disagreement analyst | 42 (35 agent, 7 manual) | `anthropic:claude-opus-5-5` | `radar_prompt_v1` |
| `radar-var-a` | variation test, `--random 5 --seed 7 --redecide` | 5 | same | same |
| `radar-var-b` | variation test, same 5 styles, `--redecide` | 5 | same | same |

Rule labels: metric_version `micro_trend_rules_v1`, config hash `81c057ac90b06418` (docs/micro_trend_rules.md).
Stored in Atlas: `radar_decisions` 52 documents (42 + 5 + 5), `radar_disagreements` 14 documents. JSONL copies:
`scripts/out/radar_final_decisions.jsonl`, `scripts/out/radar_disagreements.jsonl`, and
`.social_runs/radar_runs/<run_id>/` (evidence, decisions). All 35 agent styles ended with
`decision_source: "agent"`: no agent errors and no rule fallbacks.

Commands used:

```
export RADAR_AGENT_MODEL=anthropic:claude-opus-5-5
.venv/bin/python scripts/radar_agent.py --dry-run --random 3 --seed 1
.venv/bin/python scripts/radar_agent.py --styles "barrel jeans,cargo pants,bubble skirt" --run-id radar-main-1
.venv/bin/python scripts/radar_agent.py --all --run-id radar-main-1
.venv/bin/python scripts/radar_agent.py --disagreements --run-id radar-main-1
.venv/bin/python scripts/radar_agent.py --random 5 --seed 7 --redecide --run-id radar-var-a
.venv/bin/python scripts/radar_agent.py --random 5 --seed 7 --redecide --run-id radar-var-b
.venv/bin/python scripts/radar_to_trends.py --run-id radar-main-1
```

## Cost

Pilot (3 styles, measured): barrel jeans 0.1397 USD, cargo pants 0.1577, bubble skirt 0.1538. Mean 0.1504 USD per
style. Projection for all 35 agent styles: 35 x 0.1504 = 5.26 USD, far under the 400 USD Claude cap, so the full run
went ahead.

| item | measured |
|---|---|
| decisions, 35 agent styles (`radar-main-1`) | 4.3988 USD, mean 0.1257 per style, min 0.0990, max 0.1590 |
| disagreement analyst, 14 styles (`radar-main-1`) | 1.9330 USD, mean 0.1381 per style |
| variation run a, 5 styles | 0.6840 USD |
| variation run b, 5 styles | 0.6718 USD |
| ledger total, Claude (`.social_runs/spend_ledger.json`, 346 entries) | 7.6877 USD of the 400 USD cap |
| Vultr | no Vultr call in these runs |

Tool calls per style: mean 5.7, max 7 (budget 12). Wall time per style: mean 24.2 s. Most input tokens are prompt
cache reads, so the uncached input token counts printed per style are small (10 to 16).

## Agreement (35 non-manual styles)

Agreement: 21 of 35 styles (60 percent). Both labels exist for all 35.

Confusion matrix, rows rule_label, columns agent_label:

| rule \ agent | micro_trend | established | still_active | unknown | total |
|---|---|---|---|---|---|
| micro_trend | 0 | 0 | 0 | 0 | 0 |
| established | 0 | 20 | 0 | 4 | 24 |
| still_active | 0 | 1 | 1 | 2 | 4 |
| unknown | 0 | 7 | 0 | 0 | 7 |
| total | 0 | 28 | 1 | 6 | 35 |

Agent labels: established 28, unknown 6, still_active 1, micro_trend 0. Agent confidence: low 18, medium 17, high 0.
Final labels (all from the agent): same as the agent labels.

Neither the rule nor the agent labeled any non-manual style micro_trend. So `scripts/radar_to_trends.py` wrote no
trend documents (see Trend documents below).

## Disagreements (14 styles)

The disagreement analyst stored ranked hypotheses for all 14. Each hypothesis is marked `is_hypothesis: true`, and
each style has at least one tool check (`tool_checks_run` lists them; 3 to 6 data tool calls per style). The analyst
favored the agent label for 12 styles, the rule label for 1 (poplin shirt) and was unclear for 1 (bubble skirt).
Overall confidence: medium 12, high 2 (cherry red, chinos).

| style | rule | agent | top hypothesis (rank 1), short | favors |
|---|---|---|---|---|
| Levi's 501 | still_active | unknown | resale and deal bots dominate, late spike is listing volume | agent |
| block heels | unknown | established | test 1 left-censored, rule has no settled label for a full-window run | agent |
| bubble skirt | unknown | established | test 2 fails (49 active weeks) but the rule has no outcome for it | unclear |
| butter yellow | established | unknown | low precision: colour word used for sky, flowers, paint | agent |
| capri pants | unknown | established | test 1 left-censored, full-window run | agent |
| cargo pants | still_active | established | late peak starts the week the corpus jumps (ingest artifact) | agent |
| cherry red | still_active | unknown | low precision: Cherry Red record label, cars, guitars | agent |
| chinos | established | unknown | low precision: Spanish "chinos" (Chinese) news | agent |
| chunky sneakers | unknown | established | test 1 left-censored, full-window run | agent |
| high heels | established | unknown | low precision: art commissions, adult spam, lyrics | agent |
| poplin shirt | unknown | established | test 1 left-censored; but posts are mostly shop and catalog ads | rule |
| sambas | established | unknown | low precision: samba music and carnival, a place name | agent |
| slingback heels | unknown | established | test 1 left-censored; the one peak week is an off-topic spike | agent |
| straight leg jeans | unknown | established | test 1 left-censored, rule design rather than data | agent |

### Most common disagreement reasons (counts of styles, from the stored hypotheses)

Rank-1 hypothesis, grouped:

| reason | styles | which |
|---|---|---|
| rule has no settled label for a run that covers the whole window (test 1 left-censored, or test 2 fails without another path); agent calls it established | 7 | block heels, bubble skirt, capri pants, chunky sneakers, poplin shirt, slingback heels, straight leg jeans |
| low precision: the term mostly matches off-topic posts, so the agent refuses a label (unknown) | 6 | Levi's 501, butter yellow, cherry red, chinos, high heels, sambas |
| corpus or ingest step change creates the rule's peak | 1 | cargo pants |

Any rank (a style can count in several rows):

| reason | styles |
|---|---|
| low precision or off-topic clusters shape counts or the peak | 13 |
| corpus_count jumps about 2.5 to 3 times from week 2026-08-31, so share falls while raw counts hold | 11 |
| saturation at the 20-post cap censors the peak or flattens the series | 8 |
| spread is concentrated in one community or slow (test 4 inputs) | 7 |
| one year observed, so seasonality is untested | 3 (block heels, capri pants, sambas) |

Threshold suggestion, as a hypothesis only (no config was edited): the rule's outcome order sends a fully
observed-length run that touches the left edge to unknown. For 6 styles the analyst read this as rule design, not a
data limit. An extra outcome such as "established when the active run covers every metric week and test 2 fails"
would move those styles to the agent's label. This would also hide styles whose rise began just before the window
opened, so it needs a check on press dates before anyone adopts it.

## Things the agent noticed that the rules did not

Each item is tied to the stored reasons or hypotheses for the named styles.

1. **Polysemous terms.** The rules count every match. The agent sampled posts and found most were not about
   clothing: cherry red (record label, football sponsorship, cars; 0 of 12 and then no clear garment post in a
   second sample of 20 plus 12), chinos (Spanish word for Chinese; about 1 of 12), sambas (samba music, carnival;
   0 of 12 in the peak week), butter yellow (general colour word; about 4 to 5 of 20), high heels (art commissions,
   adult spam; about 5 of 20). The rule labels for these rest mostly on off-topic chatter.
2. **Bot and listing volume.** Levi's 501: about 8 of 10 posts in the peak week (2026-10-26) were price or discount
   listings; the late spike that makes the rule say still_active looks like listing volume. Poplin shirt: most
   garment posts are shop ads and vintage catalog posts. Chunky sneakers: at least 5 of 10 posts in week 2026-07-20
   were near-identical AI image prompt posts.
3. **A corpus step change on 2026-08-31.** corpus_count rises from about 450 to 490 to about 1100 to 1400 per week
   from that week, in the series of at least 11 styles (block heels, bubble skirt, butter yellow, capri pants, cargo
   pants, cherry red, chunky sneakers, high heels, poplin shirt, slingback heels, straight leg jeans). Share falls
   while raw counts hold, so share-based decay after that date may be a denominator artifact. For cargo pants and
   cherry red, raw counts also jump with it and the rule peak falls in the following weeks. Hypothesis: a change in
   collection (for example deeper pages or new terms added in that period), not a change in chatter. This is not
   verified against the ingest logs.
4. **Single off-topic spike sets the peak.** Slingback heels: the only spike (2026-08-17, saturated) had 0 of 10
   sampled posts about the shoe (reshares of one political link). The rule's fraction-of-peak tests are anchored on
   that week.
5. **Overlapping terms.** Slingback heels overlaps kitten heels; high heels overlaps the wider heels family (shared
   largest community). The rules treat each term as independent.

## Variation test (same 5 styles, three runs)

Styles (`--random 5 --seed 7`, manual styles excluded): balloon pants, barrel jeans, capri pants, kitten heels,
oxford shirt.

| style | radar-main-1 | radar-var-a | radar-var-b |
|---|---|---|---|
| balloon pants | established, medium | established, low | established, medium |
| barrel jeans | established, medium | established, medium | established, medium |
| capri pants | established, low | established, low | established, medium |
| kitten heels | established, medium | established, medium | established, medium |
| oxford shirt | established, medium | established, medium | established, medium |

Label changes between var-a and var-b: 0 of 5 (also 0 of 5 against radar-main-1). Confidence changed for 2 of 5
styles across the three runs (balloon pants, capri pants). Limit: all 5 sampled styles are established, the most
common label, so this sample does not test the unstable cases (the low-precision unknowns and the edge-censored
styles). 5 styles is too few to bound the variation rate.

## Manual styles (excluded from agreement and from trend documents)

Stored with `decision_source: "manual"`, `excluded_from_agreement: true`, agent skipped. Editorial labels (team
judgment, not Radar data). All 7 have rule_label unknown (data-short).

| style | final_label (manual) | rule_label |
|---|---|---|
| balloon jeans | micro_trend | unknown |
| mesh flats | micro_trend | unknown |
| micro bag | micro_trend | unknown |
| mid rise jeans | established | unknown |
| plain black tee | established | unknown |
| slim sneakers | still_active | unknown |
| soft suiting | established | unknown |

## Trend documents (scripts/radar_to_trends.py)

Run on `radar-main-1`, twice (idempotence check on Atlas). Candidates with final_label micro_trend: 3, all manual
(balloon jeans, mesh flats, micro bag), all skipped as "manual label: never a trend document". Trend documents
written: 0. The `trends` collection still holds 3 documents, 0 with origin agent. Skips are recorded in
`.social_runs/radar_runs/radar-main-1/trend_skips.jsonl`.

Mapping coverage (checked for all 35 non-manual terms, so a later micro_trend decision is ready): 31 map to a
category; 4 are unmappable and would be skipped: butter yellow, cherry red, leopard print (colour or print, no
garment), crochet top ("top" is not a listed category). Explicit overrides in the script: sambas -> shoes,
Levi's 501 -> jeans, quarter zip -> sweater, leg warmers -> accessory, flats and mesh flats -> shoes.

A written trend would have: origin `agent`, one source `{"title": "Bluesky search: <term>", "url":
"https://bsky.app/search?q=<term>"}`, lifespan_source_url equal to that URL, lifespan_weeks_est from
`micro_trend.measured_active_run` (the rail 3 measurement), bucket derived by `TrendDoc`, or null lifespan with a
words-only label when no run is measurable. A name held by a seed trend, or a category plus features signature held
by another trend, is skipped and never overwritten.

## Limits

- Sampled counts: first-page Bluesky search samples, capped at 20 posts per term per week window; 15 styles have
  more than 25 percent saturated weeks. Counts are lower bounds and shares are estimates, not platform totals.
- Provisional thresholds: `config/social_micro_trend.json` values are a first version, not tuned on labeled data.
  Agreement with the rule is not accuracy; there is no ground truth for the 35 styles.
- Window: 12 months; most styles were already active when it opened (left censoring), and seasonality is untested.
- Precision: the agent's precision estimates come from samples of 10 to 20 posts per style, read by the model.
- Run-to-run variation: measured on only 5 styles, all established (0 label changes). Variation on the harder
  styles is not measured.
- Agent prompts and skills (`radar_prompt_v1`) were not tuned between runs. The disagreement analyst is the same
  model as the judge, so its hypotheses may favor the agent; it favored the agent in 12 of 14.
- Costs are ledger estimates from `config/radar_prices.json`; the Anthropic usage page is the true number.
