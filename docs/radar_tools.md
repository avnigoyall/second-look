# Radar agent tools and spend ledger

Files: `scripts/radar_agent_tools.py` (tools), `scripts/radar_spend.py` (ledger), `config/radar_spend_caps.json`, `config/radar_prices.json`. Tests: `tests/test_radar_agent_tools.py` (local Mongo fixtures, mocked model and image fetch), `tests/test_radar_spend.py`. No test makes a paid call.

## Using the tools

```python
import radar_agent_tools as rt
rt.start_run(run_id)            # optional db=<pymongo Database>; default backend.db.db (Atlas "secondlook")
agent_tools = rt.TOOLS          # plain functions with typed args and docstrings
rt.write_manual_decisions()     # not an agent tool: stores the 7 editorial labels for this run
```

Every tool takes `style` (the `social_trends.term`) first. It returns a JSON-safe dict with `ok`, `evidence_id`, `tool_calls_used` and `tool_calls_left`. A refused call returns `ok: false` and `error`. It does not raise, so one bad call does not stop the agent loop.

| Tool | Returns | Limits in code |
|---|---|---|
| `get_style_stats` | post_count, authors, metric_weeks, weeks_with_posts, metric window, first_seen_in_window, peak week, saturation, coverage note, manual flag, rail limits | never returns evaluation labels, press_status or observed_weeks |
| `get_weekly_series` | per week: count, corpus_count, share, S_w, markers (`partial_edge`, `saturated`, `peak`, `active_start`, `active_end`) | at most 60 weeks. S_w is the share smoothed with the rule window (3 weeks, centered), over metric weeks only |
| `get_connectivity` | stored breadth, concentration, weeks_to_breadth_3, interaction share (replies, quotes, mentions), community count, communities with 3+ posts, top 5 communities | post counts here include thread-enrichment posts |
| `sample_posts` | deterministic sample per run and style, optional `week` | at most 20 posts. Snippets sanitized, at most 160 characters |
| `get_press_sources` | stored trend documents whose name contains every word of the style, with their sources | may be empty. All 3 stored trends are placeholders with no sources (Nov 14) |
| `run_rule_metrics` | `micro_trend.compute_rule_metrics` output | labeled as evidence, not an instruction |
| `get_item_materials` | `item_materials` documents whose title contains every word of the style | at most 10, may be empty |
| `estimate_material_from_photo` | at most one labeled line, the estimate and the measured accuracy | see below |
| `write_decision` | the stored decision | rails below |
| `record_event` | stores a note in `radar_events` and `events.jsonl` | 100-character event, 1000-character detail |

Per-style budget: 12 tool calls per style per run, counted for all ten tools. The 13th call is refused. Plan one call for `write_decision`. A new `start_run` resets the counters.

## Evidence ids

`ev:<tool>:<style-slug>:<call number, 2 digits>:<first 10 hex of sha256 of the payload>`, for example `ev:get_style_stats:barrel-jeans:01:eed390a5ab`. An id is valid only in the run and for the style that issued it. Each evidence record (id, run, style, tool, payload) is appended to `.social_runs/radar_runs/<run_id>/evidence.jsonl` (git-ignored).

## Rails in write_decision

These are data limits, not judgment.

1. A label other than `unknown` is refused when the style has fewer than 100 posts or fewer than 8 weeks with posts. Weeks with posts are counted inside the metric weeks with `micro_trend.weeks_with_posts`, never from the stored `observed_weeks`. Both thresholds are read from `config/social_micro_trend.json`.
2. A decision with no evidence ids, or with any id not issued in this run for this style, is refused.
3. The agent cannot type a lifespan; `write_decision` has no lifespan-weeks argument. When the agent passes a `lifespan_source_url`, the URL must be one of the matching trend's `sources[].url`, or the decision is refused. The code then calls `micro_trend.measured_active_run`. If it returns a run, `lifespan_weeks_est` is its `weeks_active` and `lifespan_bucket` is short (16 weeks or less), medium (52 or less) or long. If no run can be measured, no lifespan is set.
4. The seven manual styles in `config/manual_labels.json` refuse agent decisions. `write_manual_decisions` stores their labels with `decision_source: "manual"`, null lifespan fields and `excluded_from_agreement: true`.

Labels: `micro_trend`, `established`, `still_active`, `unknown` (from `micro_trend.LABELS`). Confidence: `low`, `medium`, `high`.

Stored fields (`radar_decisions`, unique index `style_run_unique` on style and run_id, replaced on rewrite): style, run_id, rule_label, agent_label, final_label (equal to agent_label for agent decisions), decision_source, agree (agent_label equals rule_label), confidence, reasons (at most 8, 500 characters each), evidence_ids, lifespan_weeks_est, lifespan_bucket, lifespan_source_url, active_run, lifespan_note, metric_version, config_hash, prompt_version, model_id (argument, else `RADAR_AGENT_MODEL`, else `CLAUDE_MODEL`), excluded_from_agreement, created_at. Each decision is also appended to `.social_runs/radar_runs/<run_id>/decisions.jsonl` with `stored: "db"`, or with `stored: "jsonl_fallback"` when the Mongo write failed.

## Photo material estimate (estimate_material_from_photo)

- Runs only when `MATERIALS_PHOTO_ENABLED` is on and the item has no stated composition. Items that state a composition return `skipped_stated_composition` and the model is not called. All 30 stored `item_materials` documents state a composition, so for them the tool always skips.
- Takes an `item_key` (its stored `image_url` is used) or an `image_url`. Image fetch: http(s) only, 10 s timeout, image content type, 8 MB maximum.
- Output is one line or none. It never changes a label, a lifespan or a verdict (`affects_label: false`). No decision field is derived from it.
- A result is stored in `item_materials` (source `radar_agent`, no image bytes) only when `MATERIALS_STORE_USER_ITEMS` is on. The default is off.
- The accuracy text is read from `docs/eval_materials.md` at run time. If accuracy is not above the majority-class baseline, or the doc has no accuracy, the line says "low confidence".

Measured (docs/eval_materials.md): n = 30 Shopify retailer items (Uniqlo and Zara blocked the fetch), accuracy 17/30 = 56.7 percent with unknown counted as wrong, majority-class baseline 16.7 percent. Accuracy is above the baseline, but the sample is small and stratified, so that baseline is low by design. Example line:

> Estimated from photo: likely wool or knit, low confidence, about 57 percent accurate on a 30-item Shopify retailer test

The confidence word comes from `materials_photo.photo_line`: "medium" when the score and margin pass the medium thresholds, else "low".

The feat/materials merge is a no-op. Its tip `3a6bdd5` and merge commit `0ecf8c0` are ancestors of feat/micro-trend. `../phacks-materials` does not exist.

## Spend ledger

```python
import radar_spend
radar_spend.check_budget("claude", run_id=run_id)          # before each call; raises BudgetExceeded
msg = llm.invoke(...)
radar_spend.check_and_record("claude", model, **radar_spend.usage_from_langchain(msg.usage_metadata),
                             run_id=run_id, style=style)
# on BudgetExceeded: radar_spend.stop_cleanly(exc)  prints the reason and resume command, exits 3
```

- Caps (`config/radar_spend_caps.json`): Claude 400 USD, Vultr 100 USD for the whole run. A warning is logged at 80 percent. Calls are refused at 100 percent. A call that has already been made is always recorded, even past the cap.
- Ledger: `.social_runs/spend_ledger.json`, one entry per call (provider, model, input, output and cache tokens, estimated cost, running total, run_id, style). Several processes can write it safely because each write holds an exclusive `fcntl` lock. A corrupt ledger blocks calls and is never reset.
- The Vultr check also counts the existing embed guard ledger `.social_runs/vultr_usage.json` (read-only). Measured Nov 14: 6.58 USD estimated, so the Vultr total starts at 6.6 percent of the cap.
- A model with no price, or a missing price for a token kind, raises `UnpricedModel` and the call must not be made.
- `usage_from_langchain` subtracts cache tokens from `input_tokens` (langchain-anthropic adds them in). `usage_from_anthropic` reads an SDK `Usage` object.
- The resume command printed is `.venv/bin/python scripts/radar_agent.py --resume --run-id <run_id>`. The agent script can pass its own `resume=` text.
- These totals are the code's estimates. The provider usage pages are the true numbers.

## Price sources (fetched 2026-11-14)

| Provider / model | Price per MTok (input / output) | Source |
|---|---|---|
| Claude Haiku 5.5 (`claude-haiku-5-5`, the `CLAUDE_MODEL` in .env) | 0.10 / 0.50 for prompts up to 100,000 tokens; 0.50 / 2.50 above. Cache: 5m write 0.125, 1h write 0.20, read 0.01 (0.625, 1.00, 0.05 above 100k) | https://platform.claude.com/docs/en/about-claude/pricing |
| Claude Sonnet 5.5 (`claude-sonnet-5-5`) | 2 / 10. Cache: 2.50, 4, 0.10 | same |
| Claude Opus 5.5 (`claude-opus-5-5`) | 4 / 20. Cache: 5, 8, 0.20 | same |
| Vultr `qwen3-embedding-4b` | 2.75 per token, an upper bound | https://docs.vultr.com/support/products/serverless/how-do-i-monitor-the-usage-and-cost-of-my-vultr-serverless-inference-subscription |

Model ids are from https://platform.claude.com/docs/en/about-claude/models/overview. The Claude prices are standard global prices: no Batch, no fast mode, no US-only 1.1x multiplier. The Vultr page (updated 2026-10-26) gives 0.55 USD per 1M input tokens and 2.75 USD per 1M output tokens for Serverless Inference, with no embedding-specific line. The ledger charges embedding tokens at 2.75, the same conservative rate the existing embed guard uses. www.vultr.com/pricing returned HTTP 403 to the fetch.
