# eval_coding

A/B evaluation of coding-agent harnesses on [SlopCodeBench](https://github.com/gabeorlanski/slop-code-bench) (SCB).

The goal is a regression signal for harness changes: when the pi-dev-team
extension (or, later, the software factory) changes, run the same SCB subset
against a fixed baseline and see whether pass rates, cost and tokens moved.
The baseline is stock Claude Code with nothing installed, so any difference
comes from the harness and not from leaked user configuration.

SCB is vendored as a submodule at `vendor/slop-code-bench`, pointing at the
fork `sanjit-roopra/slop-code-bench`, branch `harness-eval` (upstream
`31ceea3` plus patches for Copilot credentials, tool-result cost accounting
and the extra models/providers).

## Arms

| Arm | Harness | Model (`--model`) | Agent config | Prompt | Environment |
|-----|---------|-------------------|--------------|--------|-------------|
| `cc-clean` (A) | Claude Code 2.1.304, stock, no plugins/MCP/user skills | `claude_code_oauth/opus-5.5` (`claude-opus-5-5`, OAuth subscription) | `agents/cc-clean.yaml` | SCB `just-solve` | SCB `docker-python3.12-uv` |
| `pidt` (B) | pi 1.3.0 + pi-dev-team @ `3c34bf5` (`-e /opt/pi-dev-team`) | `github-copilot/gpt-6.1-sol` | `agents/pidt.yaml` + `templates/pidt.j2` | `prompts/pidt.jinja` (`/plan --yes` then `/build --yes`) | `environments/docker-python3.12-uv-pidt.yaml` |
| `pidt-opus` (C, optional) | same as B | `github-copilot/copilot-claude-opus-5.5` | `agents/pidt.yaml` | `prompts/pidt.jinja` | same as B |
| `pi-stock` (optional) | pi 1.3.0, no extension | `github-copilot/gpt-6.1-sol` | `agents/pi-stock.yaml` + `templates/pi-stock.j2` | SCB `just-solve` | same as B |

All arms run with `thinking=high`. C uses the same model family as A, so A vs C
isolates the harness; B vs pi-stock isolates the extension.

Costs under the Claude and Copilot subscriptions are notional list prices from
the SCB model catalog, not money spent.

## Layout

| Path | What |
|------|------|
| `run.sh` | Entry point: one arm x one subset x one repeat |
| `scripts/manifest.py` | Writes `manifest.json` (SHAs, versions, models, prompt hash, host, command) before a run; finalizes it after |
| `scripts/check_clean.py` | Verifies every checkpoint trace ran on a clean harness; writes `clean_check.json`, sets `manifest.clean.status` |
| `scripts/resolve_config.py` | Resolves a run through SCB's own loaders without running (used by `--dry-run`) |
| `scripts/make_smoke_problem.py` | Builds a one-problem catalog truncated to N checkpoints for `smoke` |
| `agents/`, `templates/`, `environments/`, `prompts/`, `runs/` | Arm configs, image templates, environment, prompts, per-arm run configs |
| `tests/` | pytest suite (`tests/fixtures/` holds trimmed, secret-free real traces) |
| `results/` | Run outputs (git-ignored) |

## Running

Prerequisites: Docker running, `uv`, `jq`, the submodule checked out
(`git submodule update --init`), and the SCB problem catalog installed once:

```sh
uv run --project vendor/slop-code-bench slop-code sync
```

Credentials (never printed, never committed):

- Claude arms: OAuth token in `~/.config/scb/cc_oauth_token` (or `CLAUDE_CODE_OAUTH_TOKEN` already exported). `run.sh` reads it into the environment at run time only.
- Copilot arms: a `github-copilot` login in `~/.pi/agent/auth.json` (`pi`, then `/login github-copilot`). SCB copies only that entry into the container.

```sh
./run.sh [--dry-run] [--workers N] [--no-evaluate] [--resume DIR] ARM SUBSET [REPEAT]

./run.sh --dry-run pidt smoke      # print + resolve the slop-code command, run nothing
./run.sh cc-clean smoke            # 1 problem (file_backup), first 2 checkpoints
./run.sh pidt lite_under20 2       # second repeat of the under-$20 subset
```

| Subset | Problems |
|--------|----------|
| `smoke` | `file_backup` truncated to 2 checkpoints (`SMOKE_PROBLEM`, `SMOKE_CHECKPOINTS` override). slop-code has no checkpoint-limit flag, so `run.sh` builds a truncated catalog under `results/.smoke-catalog/` and sets `SCBENCH_PROBLEMS_PATH` |
| `lite_under20` | `mvvault`, `xjq` (`runs/<arm>_lite_under20.yaml`) |
| `lite` | `code_search`, `xjq`, `file_backup`, `etl_pipeline`, `datagate` (`runs/<arm>_lite.yaml`) |
| `full` | every problem in the installed catalog (expensive: ~$425+ notional) |

Each run writes `results/<YYYYMMDD>-<arm>-<subset>-r<REPEAT>/` containing SCB's
normal output plus:

- `manifest.json`: arm, subset, repeat, eval_coding and SCB fork SHAs (and dirty flags), pi-dev-team SHA, Claude Code / pi versions, model ids and catalog pricing, prompt path + sha256, agent config + template sha256, host, timestamps, exit code, the exact command, and `clean.status`.
- `clean_check.json`: per-trace verdicts and reasons.

`run.sh` sets `TMPDIR=/tmp` (SCB puts the agent HOME under `tempfile.gettempdir()`,
and macOS' default TMPDIR breaks the container mounts). Exit codes: `0` clean
run, `2` usage/preflight error, `3` DIRTY harness, `4` clean check UNKNOWN,
otherwise slop-code's own exit code.

### Clean check

SCB stores each checkpoint's agent stdout at `<run>/<problem>/<checkpoint>/agent/stdout.jsonl`
(or inside `agent.tar.gz`). `check_clean.py` reads every one:

- Claude Code: each `system`/`init` event must list only `builtin` plugins, no MCP servers, and skills/agents from the stock 2.1.304 allowlist (19 skills; `claude`, `Explore`, `general-purpose`, `Plan`, `statusline-setup`). Any namespaced `plugin:item` skill, agent or slash command, or a different `claude_code_version`, makes the run DIRTY.
- pi: the system message may add only pi's builtin tools plus, for `pidt*`, the pi-dev-team tools; system-prompt sections are limited to pi's core ones plus `dev_team`; skills may load only from `/opt/pi-dev-team`; custom messages/entries must come from pi-dev-team. `pidt*` arms also fail if pi-dev-team did not load. `pi-stock` allows no extension at all.

A DIRTY run must be excluded from comparisons.

### Tests

```sh
uv run --with pytest pytest -q
```

## Plan

| # | Step | Status |
|---|------|--------|
| 1 | Fork SCB (`harness-eval`) + patches: Copilot file-credential passthrough, count `toolResult` usage (dev_team_subagent) in cost/tokens, Copilot provider + Opus 5.5 / GPT-6.1 Sol models | Done in fork (`67c6cf0`, `bb0e1f6`, `71a2a11`); submodule pointer update pending |
| 2 | eval_coding skeleton: repo, submodule, `.gitignore`, `pyproject.toml`, layout | Done |
| 3 | Arm configs, image templates, environment, prompts, per-arm run configs | Done (written, dry-run resolves for all arms) |
| 4 | `run.sh` + manifest + clean check (+ smoke catalog, dry-run resolver, tests) | Done |
| 5 | Real slop-code smoke: 1 problem, 2 checkpoints, per arm | Next (see `STATUS.md`) |
| 6 | `lite_under20` x1 per arm | Todo |
| 7 | `analysis/compare.py`: paired per-checkpoint deltas, real $ and tokens | Todo |
| 8 | `lite_under20` x3 per arm | Todo |
| 9 | Regression loop: pidt @old vs @new SHA on the same model | Todo |

Current detail and next-step instructions: [`STATUS.md`](STATUS.md).
