# Radar deep agent (scripts/radar_agent.py)

The agent decides one label per style from Bluesky sample chatter: `micro_trend`, `established`,
`still_active` or `unknown`, with a confidence, short reasons and the evidence_ids it relied on.

## Installed API

deepagents 0.7.37, langchain-anthropic 1.7.10. `inspect.signature(create_deep_agent)` (printed by `--dry-run`):

```
create_deep_agent(model=None, tools=None, *, system_prompt=None, middleware=(), subagents=None,
                  skills=None, memory=None, permissions=None, backend=None, interrupt_on=None,
                  response_format=None, state_schema=None, context_schema=None, checkpointer=None,
                  store=None, debug=False, name=None, cache=None)
```

Argument notes (checked against the installed source in `.venv`):

- All arguments after `tools` are keyword-only. The names the plan used (`model`, `tools`, `skills`, `backend`,
  `subagents`, `system_prompt`) match.
- `skills` paths are read through the backend, not the host disk. The backend is
  `FilesystemBackend(root_dir=<repo>, virtual_mode=True)`, so the path is the virtual `"/radar_skills/"`, not
  `"radar_skills/"`. With the default `StateBackend` the skills would load silently as empty.
- Each skill directory name must equal its frontmatter `name`. Only name and description go into the system
  prompt; the agent reads the body with `read_file`.
- Custom subagents do not inherit skills. The `precision-auditor` subagent has its own `skills` list. The
  auto-added general-purpose subagent inherits the main agent's skills and tools.
- `system_prompt` is always passed. deepagents 0.7.x has no built-in harness prompt for Claude 5.x model ids.
- `permissions` (FilesystemPermission, first match wins) limit the agent to reading `/radar_skills/**` and
  `/config/**`. All writes are denied, so the agent cannot edit config, `.env` or any file.

## Model id

`RADAR_AGENT_MODEL`, in `provider:model` form (for example `anthropic:claude-sonnet-5-5`). The model is built
with `langchain.chat_models.init_chat_model(model_id, max_tokens=4096)`. The full string is stored as
`model_id` on every decision. The spend ledger gets provider `claude` and the model name after the colon, which
must have a price in `config/radar_prices.json` (otherwise the ledger refuses the call).

## Tools, budget and spend

- Tools: `TOOLS` from `scripts/radar_agent_tools.py`. `write_decision` is wrapped so code fills in `model_id`
  and `prompt_version`; the model never types them.
- Tool budget: 12 tool calls per style, counted across the main agent and its subagents. The 12th slot is kept
  for the final write, so a decision is always possible. `radar_agent_tools` enforces 12 as well.
- A runaway guard ends the main agent after 25 model calls for one style.
- Spend: a LangChain callback runs `radar_spend` `check_budget("claude")` before every chat model call
  (subagents included) and `check_and_record(...)` after it, with tokens from `usage_metadata`. Per-style cost
  is the sum of the ledger's `estimated_cost_usd` for that style. If the ledger refuses (cap reached, unpriced
  model, unreadable ledger), the run stops cleanly, prints the projection and the resume command, and exits 3.
  No ledger module means no paid call.

## CLI

```
.venv/bin/python scripts/radar_agent.py --dry-run --random 3      # signature, skills, tools, plan; no model call
.venv/bin/python scripts/radar_agent.py --styles "a,b,c"          # pilot: prints cost per style and a projection
.venv/bin/python scripts/radar_agent.py --random 5 [--seed N]
.venv/bin/python scripts/radar_agent.py --all [--cap-usd X] [--pilot 3]
.venv/bin/python scripts/radar_agent.py --resume --run-id ID      # same as --all --run-id ID
.venv/bin/python scripts/radar_agent.py --disagreements [--run-id ID] [--styles "a,b"]
```

Common flags: `--redecide` (ignore earlier decisions), `--run-id ID` (default: a new id).

Projection gate: after `--pilot` styles (default 3) are measured in this process, before each further style the
script computes mean cost per style x styles left. If that exceeds the budget left (`--cap-usd` minus spend in
this run, or by default the ledger's remaining Claude cap at start), it stops and prints the resume command.
The pilot (`--styles` or `--random`) prints the same projection line, including the cost for all agent styles.

## What is stored

`radar_decisions`, unique on `(style, run_id)`. `write_decision` (tools module) writes the agent's document;
the script then `$set`s the final fields on the same document and appends the merged record to
`scripts/out/radar_final_decisions.jsonl` (fallback when Mongo is down).

- `decision_source`: `agent` when write_decision stored a label; `rule` when the agent failed, was refused or
  ended without a stored decision (final_label is then the rule label); `manual` for the styles in
  `config/manual_labels.json` (written by `write_manual_decisions`, agent skipped).
- `agree`: `rule_label == agent_label`, only when both are non-null and the style is not manual; else null.
- Also: `final_label`, `confidence`, `reasons`, `evidence_ids`, `metric_version`, `config_hash`,
  `prompt_version`, `model_id`, `usage` (model calls, tokens, `cost_usd`, seconds), `tool_calls`,
  `agent_error`.

## Resume semantics

A style is skipped when `radar_decisions` (or the JSONL fallback) already holds a document for it with
`decision_source == "agent"` and the same `metric_version`, `prompt_version` (`PROMPT_VERSION` in the script)
and `model_id`, from any run. `--redecide` turns this off. Rule fallbacks are retried. Changing the rule config
version, the prompt version or the model therefore re-decides every style.

## Disagreement mode

`--disagreements` takes the latest agent decision per style with `agree == false` (filtered to `--run-id` when
given) and runs the disagreement-analyst agent (skills: disagreement-analyst, analyst-note-writer; data tools
without `write_decision`; plus `write_disagreement`). `write_disagreement` refuses unless every hypothesis has a
statement, evidence_ids (issued in this run or cited by the stored decision) and a confidence, at least one
hypothesis has a `tool_check`, and at least one data tool ran. It stores ranked hypotheses, each marked
`is_hypothesis: true`, with `favors` (`rule`, `agent`, `unclear`) and an overall confidence, in
`radar_disagreements` (unique on style and run_id) and `scripts/out/radar_disagreements.jsonl`. It never
writes config, trend documents or decisions. Styles that already have hypotheses with the same prompt_version
and model_id are skipped unless `--redecide`.
