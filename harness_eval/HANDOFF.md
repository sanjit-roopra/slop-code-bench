# Handoff: cut pi-dev-team wall time (2026-10-24)

The previous handoff (2026-10-21, file_backup, plan drift) is in git history: `git show 1236f88:harness_eval/HANDOFF.md`. `STATUS.md` and `README.md` still describe the overall plan.

## Your task

pi + pi-dev-team (pidt) now matches or beats plain pi on quality, but takes about 7x as long and costs about 23x as much. The user wants the time down. Two parts, in order:

1. **Is plain pi's quality its own, or is it the image?** Find out whether the `pi-stock` arm does any linting or ruff-style checking on its own, or whether its code is just good by default. Facts gathered so far (verify them, then go deeper):
   - `templates/pi-stock.j2` installs no ruff, no lizard, no mypy. Only `templates/pidt-tools.j2` adds ruff 0.17.1 and lizard 1.24.1.
   - In `results/20261024-pi-stock-smoke-r2`, the traces (`file_merger/checkpoint_*/agent/stdout.jsonl`) contain no `ruff`, `flake8`, `mypy`, `lizard` or `pytest`. They do contain `unittest` (46 / 75 / 75 / 174 hits per checkpoint) and `py_compile` (28 / 7 / 6 / 15).
   - The pi-stock prompt is SCB's built-in `configs/prompts/just-solve.jinja`. pidt gets `harness_eval/prompts/pidt.jinja` (`/plan --yes ...` then `/build --yes`).
   - Still to check: pi 1.3.0's default system prompt and built-in tools (does it tell the model to lint or test?), what the model actually runs per turn, and how many edit-test loops it does per checkpoint.
   - Also check whether the base image (`configs/environments/docker-python3.12-uv.yaml`) ships any linter that pi could call.
2. **Cut the engineering time.** Find ways to make pidt's `software-engineer` phase faster without losing the quality gains. Numbers and leads below.

Do the analysis yourself, one step at a time. **Do not start Workflows or several background sub-agents without asking the user**: it makes their MacBook lag. Ask before any benchmark run, and say how long it will take (a pidt file_merger 1–4 run takes about 4 hours).

## What changed since the last handoff

### pi-dev-team (`~/git/personal/pi-dev-team`, fork https://github.com/sanjit-roopra/pi-dev-team)

| Commit | Branch | State | What it does |
|---|---|---|---|
| `44b3826` | `request-as-spec` | merged into `main` (PR #54) | saves the `/plan` request word for word and makes it outrank the plan |
| `5076bf3` | `dispatch-effort-cap` | PR #55 open, **not merged** (the auto-mode classifier blocked `gh pr merge`; the user has to merge it) | a dispatch's `thinking` may lower an agent's `effort` but not raise it, so the lean profile's medium-effort lenses really run at medium |
| `af34311` | `lean-slim-panels` (based on `5076bf3`) | pushed, **no PR yet** | lean profile: only the `spec-compliance`, `correctness`, `structure` and `test` lenses until the last slice; the last slice runs every selected lens over every changed file; every lens gets the checkpoint diff as a patch file (lenses have no shell) |

`af34311` touched `extensions/dev-team/lean/build.md`, `lib/review-profile.ts`, `test/unit/review-profile.test.ts` and `PORTING.md`. Checks passed: `npm test` 1224 pass and 1 skipped, Python 182 OK, `npm run e2e` 39/39, `tsc` clean.

Repo rules: never hand-edit `agents/` or `skills/` (they are a port of upstream bdfinst/agentic-dev-team at `2bf3d985`; change them through `sync/sync_upstream.py` PATCHES / LEAN_PATCHES or `overrides/notes/<skill>.md`, then `npm run sync`). `extensions/` is port-owned and can be edited directly. Document each change in `PORTING.md`. Checks: `npm test`, `npm run e2e`, `npx -y -p typescript@5.9.3 tsc -p .`.

### slop-code-bench (this repo, branch `harness-eval`, pushed)

- `38ff74b` pointed the pidt arms at `5076bf3`; `dd55add` points them at `af34311` (`agents/pidt-{full,lean,lean-fast}.yaml`, `templates/pidt-tools.j2`). Change the SHA with `scripts/set_pdt_sha.sh <sha>`.
- New: `scripts/pidt_agent_cost.py` and `scripts/pidt_lens_findings.py` (see Tools).
- `results/` is git-ignored; the run dirs below exist only on this machine.

## Arms

| Arm | Harness | Model | Prompt | Image extras |
|---|---|---|---|---|
| `cc-clean` | Claude Code 2.1.304 | `claude-opus-5-5` | `just-solve` | none |
| `pi-stock` | pi 1.3.0, no extension | `github-copilot/gpt-6.1-sol`, thinking high | `just-solve` | jq only |
| `pidt-lean-fast` | pi 1.3.0 + pi-dev-team `af34311`, `DEV_TEAM_REVIEW=lean`, `DEV_TEAM_MAX_PARALLEL_AGENTS=12` | `github-copilot/gpt-6.1-sol`, thinking high | `prompts/pidt.jinja` | ruff 0.17.1, lizard 1.24.1 |

Run one: `SMOKE_PROBLEM=file_merger SMOKE_CHECKPOINTS=4 ./run.sh <arm> smoke <repeat>` from `harness_eval/`, with `DOCKER_HOST=unix:///Users/sanjit.roopra/.rd/docker.sock`. Output goes to `results/YYYYMMDD-<arm>-smoke-r<repeat>` (date of the start time).

## Results: file_merger, checkpoints 1–4

file_merger has 4 checkpoints (CSV/JSON/Parquet merge, then sharded and Hive-partitioned output, then nested types). Problem catalog: `~/.cache/scbench/problems/file_merger`.

| | Claude Code | pi-stock | pidt `5076bf3` | pidt `af34311` |
|---|---|---|---|---|
| Run dir | `20261023-cc-clean-smoke-r2` | `20261024-pi-stock-smoke-r2` | `20261024-pidt-lean-fast-smoke-r2` | `20261024-pidt-lean-fast-smoke-r3` |
| Tests, checkpoints 1 / 2 / 3 | 45/46, 83/86, 101/104 | same | same | same |
| Tests, checkpoint 4 | 138/147 | 139/147 | 137/147 | 138/147 |
| Total time | 24 min | 34 min | 229 min | 242 min (37 / 62 / 47 / 96) |
| Total cost (list prices) | $5.82 | $1.64 | $40.81 | $37.95 |
| Steps | 64 | 181 | 538 | 482 |
| Program lines at checkpoint 4 | 2,215 | 1,464 | 1,728 | 1,808 |
| Test lines at checkpoint 4 | none | about 1,090 (4 files) | 6,145 (6 files) | 7,144 (4 files) |
| SCB lint (`ruff --isolated --preview`, ~80 rule families) | 284 | 230 | 277 | **199** |
| Verbosity (scb-check 0.1.3) | 0.288 | 0.317 | **0.172** | 0.219 |
| Erosion | 0.617 | 0.703 | **0.000** | 0.038 |
| Clone lines | 30 | **0** | 15 | 6 |
| Max cyclomatic complexity (radon) | 40 | 29 | **10** | 14 |

Quality numbers are for program files only (`uv run --quiet python harness_eval/scripts/program_quality.py <run dir> --json-out <file>`, from the repo root).

Checkpoint 4 failures:
- 4 core failures shared by every arm are a benchmark bug: the test CSVs use `\"` escapes, the spec says RFC 4180 (`""`). Agents parse strictly and exit 5; the reference passes only because Python's csv module is lenient by default.
- 3 regression failures are shared by every arm.
- Real misses: `partition_by_map_value` and `map_lookup_no_quotes` (Claude Code, pidt `5076bf3`); `nested_type_alias_with_parquet` (pi, pidt `5076bf3`; alias type `array` unsupported).

Most of pidt's remaining SCB lint is TRY003 / EM101 / EM102 (160 of 277 in r2) and DOC* (49). pidt's own gate runs plain `ruff check` (default rules, 0 errors), so it never sees SCB's rule set.

## Where pidt's time goes

r2 (`5076bf3`), 229 min:

| Activity | Minutes | Share |
|---|---|---|
| `software-engineer`, 12 dispatches, strictly one after another | 111 | 48% |
| Main session: its own turns, bash, test and lint runs (includes checkpoint 1's 49 min, see below) | 86 | 37% |
| Review panels, 14 rounds, lenses in parallel | 20 | 9% |
| Other single agents (spec-compliance gates, plan critics, qa-engineer) | 13 | 6% |

`software-engineer` dispatches in r2 (turns, minutes, cost): checkpoint 2 (74, 14.3, $1.31), (41, 6.9, $0.61), (29, 6.5, $0.54), (22, 6.1, $0.47), (29, 8.4, $0.72); checkpoint 3 (21, 6.9, $0.51), (22, 5.9, $0.45), (27, 9.8, $0.70); checkpoint 4 (54, 14.1, $0.94), (40, 11.2, $0.82), (28, 6.8, $0.50), (72, 14.2, $1.08).

r2's checkpoint 1 trace holds only the last resume session (1.4 min, 12 turns), so its 49 min and $8.03 cannot be broken down. Cause unknown. r3's checkpoint 1 trace is complete.

### Effect of `af34311` (checkpoints 2–4 only, the span both traces cover)

| | r2 `5076bf3` | r3 `af34311` |
|---|---|---|
| Non-core lenses: calls / cost / agent-min | 93 / $10.79 / 37 | 25 / $7.67 / 17 |
| Core lenses: calls / cost / cost per call | 48 / $7.41 / $0.15 | 36 / $9.10 / $0.25 |
| `software-engineer`: dispatches / cost / minutes | 12 / $8.65 / 111 | 13 / $10.74 / 130 |
| All sub-agents | $29.03 | $29.80 |
| Lens findings that said they could not see the change | 9 | 0 |

Verdict: the panel trim works as designed and saves about $1.40 net per run (the core lenses got dearer per call: fewer, larger panels plus the diff file; not yet separated). It saves no wall time. `software-engineer` swings by ±20 min and ±$2 between runs, which hides any panel effect. One run per version: everything at run level is within noise until there are repeats.

## Leads for cutting engineering time (none tried yet)

1. **Test volume.** pidt writes 6,000–7,000 test lines for 1,700–1,800 program lines; pi writes about 1,090, Claude Code none, and all score within 2 tests of each other. Cap tests at the plan's Gherkin scenarios plus one test per stated rule, and forbid tests the hidden suite would never check (internal helpers, exhaustive permutations).
2. **TDD cadence.** The per-behaviour cycle (red, green, refactor, run tests, lint, type-check after each behaviour) explains the 21–74 turns per dispatch. The lean profile already limits per-step runs to the behaviour's tests. Options: one red/green per Gherkin scenario instead of per behaviour; refactor once per slice; lint once per slice.
3. **Fewer dispatches.** 12–15 sequential `software-engineer` dispatches per run: one per slice plus fix rounds. Fold the error-fix round into the next slice's dispatch, or plan fewer, larger slices per checkpoint (checkpoint 2 had 5 dispatches).
4. **Parallel slices.** Slices that touch disjoint files could run as parallel `software-engineer` dispatches (pi-dev-team has `plan-review-parallelization` and same-wave concurrency in the plan format; check whether `/build` in lean mode uses it).
5. **Main session overhead.** 36 min on checkpoints 2–4 outside sub-agents: its own long turns, full-suite runs, lizard, git. Measure which bash calls dominate (tool timestamps in `stdout.jsonl`).
6. **Plan phase.** Plan critics are cheap ($0.8–1.2) but sequential. Check how much wall time `/plan` takes per checkpoint before `/build` starts.
7. **Model effort.** `software-engineer` runs at thinking high. Try medium for trivial and standard steps (PR #55's mechanism caps a dispatch's thinking below the agent's effort).

Compare every idea against pi-stock (34 min, $1.64): the target is pidt's quality at well under 2 hours.

## Tools

- `python3 harness_eval/scripts/pidt_agent_cost.py <run dir> [checkpoints]`: cost, calls and agent-minutes per sub-agent, plus the main session.
- `python3 harness_eval/scripts/pidt_lens_findings.py <run dir> [checkpoints]`: per-lens runs, no-issue runs, severity, low-confidence and blind findings.
- `harness_eval/scripts/program_quality.py`: program-file-only quality (scb-check, radon, SCB ruff), run with `uv run` from the repo root.
- Trace format (`agent/stdout.jsonl`, pi JSON events): `message_end` with `message.role` `assistant` (main session, `usage.cost.total`) or `toolResult`. A `dev_team_subagent` toolResult has `details.results[]` with `agent`, `usage.cost`, `durationMs`, `turns`, `model`. Traces reach 65 MB: pre-filter with `grep` before parsing.
- SCB lint rules: `RUFF_SELECT_FLAGS` in `src/slop_code/metrics/languages/python/constants.py`.

## Safety

- Never print or commit secrets: the Claude Code token is in `~/.config/scb/cc_oauth_token`; Copilot auth only through the SCB file credential.
- Do not touch unused Docker volumes. Old pidt bench images may be deleted.
- Money is not a constraint for these runs, but ask before starting one.
- Commit and push only when the user asks. Report to the user in terse fragments and tables.
