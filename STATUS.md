# Status

Last updated: 2026-10-20. Plan overview: [`README.md`](README.md#plan).

## Steps 1-4

| # | Step | Status | Evidence |
|---|------|--------|----------|
| 1 | SCB fork + patches | Done in fork `harness-eval` | `67c6cf0` Copilot FILE credential -> only the `github-copilot` entry copied into the pi agent dir; `bb0e1f6` toolResult usage counted in cost/tokens; `71a2a11` `github-copilot` provider + `opus-5.5`, `gpt-6.1-sol`, `copilot-claude-opus-5.5` models; `5fa3795` tool-result cost fallback picks prompt tiers per subagent turn. `tests/agent_runner/agents/pi_agent_test.py`: 68 passed (48 on upstream `31ceea3` + 20 new). `tests/agent_runner` + `tests/common`: 516 passed, 2 failed (`resume_integration_test` needs a Docker daemon; fails on upstream too). Submodule pointer committed at `5fa3795`. Pushed to the fork. |
| 2 | Repo skeleton | Done | `.gitignore` (results/, outputs/, auth files, tokens), `pyproject.toml` (pyyaml, pytest) |
| 3 | Configs, templates, prompts | Done | `agents/{cc-clean,pidt,pi-stock}.yaml`, `templates/{pidt,pi-stock}.j2`, `environments/docker-python3.12-uv-pidt.yaml`, `prompts/pidt.jinja`, `runs/<arm>_{lite,lite_under20}.yaml` |
| 4 | `run.sh`, manifest, clean check | Done | `run.sh --dry-run` resolves through SCB's loaders for 4 arms x 4 subsets (16/16, expected model printed for each); `uv run --with pytest pytest -q` 52 passed |

What step 4 delivers:

- `run.sh ARM SUBSET [REPEAT]`: maps arm to agent/model/prompt/environment, exports `TMPDIR=$HOME/.cache/scb-tmp` and `DOCKER_HOST`, loads the Claude OAuth token from `~/.config/scb/cc_oauth_token` without printing it, checks the Copilot login exists, writes `manifest.json`, runs slop-code into `results/<date>-<arm>-<subset>-r<n>/`, then runs the clean check and records CLEAN/DIRTY in the manifest.
- `--dry-run` prints the full command and resolves it through SCB (config, environment, model catalog, agent schema, image tag) with no credentials read, no docker and no model calls.
- `smoke` = `file_backup` cut to its first 2 checkpoints through `SCBENCH_PROBLEMS_PATH`, since slop-code has no checkpoint-limit flag. Verified: SCB's `problems ls` sees the truncated problem with 2 checkpoints.

Known gaps and caveats:

- Arm asymmetries (timeout 10800 s for pidt* vs 7200 s; pi-arm snapshots keep `.claude/memory/` + `plans/`, drop `.git/`) are recorded in `README.md#arms`; count timeouts per arm in step 7.
- `--dry-run --resume` is wired to slop-code's resume preview but has only been exercised against a dir without SCB's `config.yaml` (slop-code reports it is not a run dir). Check it on the first real run dir.
- The pi skill-leak check uses pi 1.3.0's two real formats (read from its `dist/core` source); the `<location>` listing test uses a system message rendered by pi's own `buildSystemPromptSections`, not a captured pi-stock run.
- The pi clean check is a strict allowlist built from one pidt trace. pi-stock has no observed trace yet, so its first smoke may flag a system-prompt section that is actually stock. If that happens, read `clean_check.json` and extend `PI_CORE_SECTIONS` in `scripts/check_clean.py` only after confirming the section comes from pi itself.
- The fallback check "the pi agent dir holds only auth.json/models.json" is not possible after the fact, because SCB does not keep the pi agent dir. The trace-based check covers that case instead: an extra extension shows up as extra tools or sections.
- The Claude Code allowlist is tied to 2.1.304. Bumping the version means taking a fresh stock init event and updating `CC_STOCK_SKILLS` / `CC_STOCK_AGENTS` and the fixture.
- The SCB problem catalog is not installed on this machine yet (`~/.cache/scbench` is missing).

## Step 5: real smoke (1 problem, 2 checkpoints, per arm)

Smoke problem: `file_backup` cut to its first 2 checkpoints. Checkpoint 1 asks for a CLI scheduler that reads a YAML schedule and prints JSONL events for the jobs that are due; checkpoint 2 extends the same code with backup strategies (full, tar archive, verify, dry-run). The agent starts each checkpoint with a fresh context but the same workspace.

Fixes needed before the runs worked on this Mac (Rancher Desktop), all committed:

- `run.sh` resolves `DOCKER_HOST` from the active docker context, because the Python docker SDK only looks at `/var/run/docker.sock`.
- `run.sh` sets `TMPDIR=$HOME/.cache/scb-tmp`, because the Rancher VM shares only `$HOME`: bind mounts from `/tmp` arrived empty, so the workspace was empty and the grader could not find its entry script.
- Fork `0a5f32c` fixes the container-side agent HOME at `/tmp/agent_home` instead of deriving it from the host temp dir (pushed).
- `templates/{pidt,pi-stock}.j2` re-add the npm-global bin dir for login shells, because SCB runs the agent with `bash -l` and Debian's `/etc/profile` resets PATH.

Results:

| | A `cc-clean` (Claude Code 2.1.304, `claude-opus-5-5`) | B `pidt` (pi 1.3.0 + pi-dev-team `3c34bf5`, `gpt-6.1-sol`) |
|---|---|---|
| Run dir | `results/20261020-cc-clean-smoke-r1` | `results/20261020-pidt-smoke-r1` |
| Status | finished, CLEAN | aborted by hand during checkpoint 2 (after about 2 h 31 min) |
| Checkpoint 1 wall time | 124 s | 4740 s |
| Checkpoint 1 tokens (cache read / cache write / output) | 186k / 18k / 12k | 13.6M / 1.76M / 182k |
| Checkpoint 1 notional cost | $0.44 | $7.58 (main agent $1.11 + 33 sub-agents in 14 dispatches $6.47) |
| Checkpoint 1 tests (core / functionality / error) | 1/1, 23/27, 2/4 | 0/1, 4/27, 4/4 |
| Checkpoint 2 | 61 s, $0.37; 1/1, 12/17, regression 26/32 | not graded |

Costs are notional list prices: both arms run on subscriptions (Claude OAuth, Copilot). The pidt figure was checked by hand against the trace: it equals the Sol prompt-tier rates (`configs/models/gpt-6.1-sol.yaml`) applied to the summed token counts, and every sub-agent dispatch carries its own usage, so patch 2 is in effect.

Why pidt failed checkpoint 1:

- 20 of 24 failing tests are one formatting bug. The spec examples print `"now_local":"2025-09-10T03:30:00Z"`; pidt prints `+00:00`. The plan it wrote (`snapshot/plans/backup-scheduler.md`) relaxed the spec to "UTC timestamps can end with Z or +00:00" and used `+00:00` in its own example and Gherkin, and none of the plan reviewers, spec-compliance reviewers or the QA agent flagged the drift.
- The remaining 4 (`glob_case_sensitive`, `glob_character_class`, `glob_doublestar_crosses_dirs`, `glob_question_mark`) fail for Claude Code too, with `error: --mount mount: expected an existing directory`; look at those cases before treating them as an agent failure.
- `plans/` was kept in the snapshot, as intended.

### pidt-next: pi-dev-team candidate `feb5afd` (branch `request-as-spec`, not pushed)

The candidate makes `/plan` save the user's request word for word under `.claude/memory/requests/` and tells the plan, the build and spec-compliance-review that this file outranks the plan. Run dir `results/20261020-pidt-next-smoke-r1`: finished, CLEAN (2 traces, all calls `github-copilot/gpt-6.1-sol`).

| | Checkpoint 1 | Checkpoint 2 |
|---|---|---|
| Wall time | 5484 s | 4142 s |
| Tokens (cache read / cache write / output) | 26.1M / 2.79M / 248k | 16.2M / 2.03M / 167k |
| Notional cost (incl. sub-agents) | $12.07 | $8.37 |
| Tests | core 1/1, functionality 16/27, error 4/4 | core 1/1, functionality 9/17, regression 21/32 |
| Claude Code, same checkpoint | 1/1, 23/27, 2/4; $0.44 | 1/1, 12/17, 26/32; $0.37 |

The format drift is gone: every `now_local` ends in `Z` and core passes. The plan carries a `**Source request**` line and spec-compliance dispatches name the request file. What still fails:

- Checkpoint 1, 7 tests: the window semantics. The spec says a job is due when its trigger falls between `--now` and `--now + --duration`; the build reports the last trigger at or before `--now` (e.g. `02:00` instead of `03:30`, `2025-09-08T15:31` instead of `2025-09-10T15:30`), and emits one run per job where the window holds several triggers (15 events instead of 43 and 29).
- Checkpoint 1, 4 glob tests: the same `--mount mount` error Claude Code hits, so likely a test-harness case, not an agent failure.
- Checkpoint 2, 8 tests: `JOB_COMPLETED.total_size` for verify and dry-run jobs (the spec example shows `0`; the build sums file sizes), and pack boundaries that close a pack one file early, which shifts the later `PACK_CREATED` sizes and checksums.

So the fix worked for the failure it targeted. The remaining gaps are misreadings of spec prose and examples that none of the reviewers caught, at about 30 to 50 times Claude Code's cost and wall time.

Spend on pi-dev-team runs on 2026-10-20: $7.58 + about $7 (aborted pidt checkpoint 2) + $20.44 = about $35 notional, within the $100 cap.

### pidt-opus: the same candidate on Claude Opus 5.5 (checkpoint 1 only)

`SMOKE_CHECKPOINTS=1 ./run.sh pidt-opus smoke`, run dir `results/20261021-pidt-opus-smoke-r1`: finished, CLEAN (all calls `github-copilot/claude-opus-5.5`). Same model as the Claude Code arm, same pi-dev-team image as pidt-next, so only the harness differs from arm A.

| | Claude Code, Opus 5.5 | pi-dev-team, Sol | pi-dev-team, Opus 5.5 |
|---|---|---|---|
| Tests (core / functionality / error) | 1/1, 23/27, 2/4 | 1/1, 16/27, 4/4 | 1/1, 16/27, 4/4 |
| Wall time | 124 s | 5484 s | 2815 s |
| Tokens (cache read / cache write / output) | 186k / 18k / 12k | 26.1M / 2.79M / 248k | 16.7M / 1.83M / 411k |
| Notional cost | $0.44 | $12.07 | $20.69 |

pi-dev-team on Opus fails the same 11 tests as on Sol, with the same wrong values, so the gap to Claude Code comes from the harness, not the model. Both plans turned the same two spec ambiguities into recorded plan decisions, and every later step honoured them:

- "Each due job runs exactly once per invocation, even when it triggers more than once in the window." The tests expect one run per trigger.
- "`JOB_ELIGIBLE.now_local` is `floor(--now)` ... (Plan decision; the request examples use `--now` equal to the trigger.)" The tests expect the trigger time.

Claude Code, given the same spec, passed both groups of tests. The 4 glob tests fail in all three runs (for pi-dev-team on Opus with 4 events instead of 6 to 8, not with the mount error).

Spend on pi-dev-team runs 2026-10-20/21: about $35 + $20.69 = about $56 notional.

## Next

1. Decide whether to push pi-dev-team `request-as-spec` (`feb5afd`) and make it the pidt arm; the plan drift it targets is fixed (see pidt-next above).
2. Cut pidt cost and time before step 6 (11 checkpoints): at about $7.58 and 79 min per checkpoint, `lite_under20` would be roughly $80+ notional and 14+ hours for one repeat.
3. Make /plan raise an ambiguity as an open question (or pick the reading the examples imply) instead of recording a decision that later reviews must honour; arm C showed this, not the model, is the gap.
4. Record outcomes here and in `README.md`, then step 6.

### pidt-next @ 0958eac: open questions instead of plan decisions (checkpoint 1 only)

`SMOKE_CHECKPOINTS=1 ./run.sh pidt-next smoke`, run dir `results/20261021-pidt-next-smoke-r1`: finished, CLEAN (all calls `github-copilot/gpt-6.1-sol`). pi-dev-team `0958eac` (on top of `feb5afd`): unclear request points go under `## Risks & Open Questions` instead of "Plan decision"; reviewers change the reading only on explicit evidence.

| | pidt-next @ feb5afd | pidt-next @ 0958eac |
|---|---|---|
| Tests (core / functionality / error) | 1/1, 16/27, 4/4 | 1/1, 16/27, 4/4 |
| Wall time | 5484 s | 4082 s |
| Tokens (cache read / cache write / output) | 26.1M / 2.79M / 248k | 14.2M / 1.94M / 183k |
| Notional cost | $12.07 | $8.09 |

The mechanism worked as written: the plan lists Q1 (one run per job or per trigger) and Q2 (`now_local` = `--now` or trigger time) as unresolved, says the examples do not decide them, and marks no "Plan decision". But the provisional reading it planned is the same wrong one for both, nobody resolves an open question in a non-interactive run, and the same 11 tests fail with the same values (`daily_multi_trigger` 15 != 43 events; `once_due` `now_local` 03:30 != 04:00). Score unchanged; the lower time and cost are within run-to-run variation until repeated.

Spend on pi-dev-team runs 2026-10-20/21: about $56 + $8.09 = about $64 notional.

### pi-stock on Sol (checkpoint 1, 3 repeats) and the Sol experiments

Program-only quality from `scripts/program_quality.py` (tests, `.claude/` and `plans/` excluded).

| Run | Tests | Cost | Time | Lines | Verbosity | Erosion | Clones | Lint/line | cc_max |
|---|---|---|---|---|---|---|---|---|---|
| `20261021-pi-stock-smoke-r1` | 28/32 | $0.19 | 276 s | 298 | 0.32 | 0.74 | 0% | 0.22 | 26 |
| `20261021-pi-stock-smoke-r2` | 28/32 | $0.20 | 293 s | 323 | 0.29 | 0.71 | 0% | 0.18 | 20 |
| `20261021-pi-stock-smoke-r3` | 28/32 | $0.17 | 276 s | 303 | 0.28 | 0.76 | 0% | 0.22 | 27 |
| cc-clean (Opus 5.5) | 26/32 | $0.44 | 124 s | 400 | 0.34 | 0.56 | 1.9% | 0.10 | 19 |
| pidt-next @ 0958eac (Sol) | 21/32 | $8.09 | 4082 s | 318 | 0.35 | 0.71 | 0% | 0.28 | 31 |

All three pi-stock runs are CLEAN and pass every non-glob test (28/32 is the ceiling; the 4 glob tests fail for every arm). Plain pi on Sol beats both Claude Code on Opus and pi-dev-team on Sol on tests, at about 1/40 of pi-dev-team's cost; its code quality is no better (erosion 0.71-0.76, one large function).

Supporting experiments (scratchpad `sol/`, not in the repo):

- E1, spec probe: Sol with no harness, given the checkpoint 1 spec and the hidden cases' inputs, predicts the right reading of both ambiguous points in 10/10 samples ($0.27 total). pi-dev-team's plan and gates flip a reading the model gets right on its own.
- E0, hand refactor ($0): splitting Sol's `load_schedule` (radon cc 31) to cc <= 10 with one `require()` validation helper keeps 21/32 and moves erosion 0.71 -> 0.00, verbosity 0.35 -> 0.13, lint/line 0.28 -> 0.07. ruff C901 alone undercounts (20 vs radon 31).

Spend 2026-10-21 after these: about $65.6 notional.

### Next experiment: pidt-full vs pidt-lean on the heldout subset (set up, not run)

Two Sol arms that differ only in pi-dev-team's review depth, to pick between the normal and the lean workflow. `agents/pidt-full.yaml` and `agents/pidt-lean.yaml` are identical except `env.DEV_TEAM_REVIEW` (`full` or `lean`); both use `templates/pidt-tools.j2`, the same `version` (so one shared image), `github-copilot/gpt-6.1-sol`, `prompts/pidt.jinja` and the pi environment. `run.sh`, `scripts/check_clean.py` and `runs/pidt-{full,lean}_{lite,lite_under20}.yaml` know both arms.

The image adds ruff 0.17.1 and lizard 1.24.1 (exact pins, installed with `uv tool install` into `/opt/uv-tools`, entry points in `/usr/local/bin`). The SCB base image has neither, so in every earlier pi-dev-team run the static-analysis pre-pass logged "ruff not installed" and lizard never ran. A throwaway build from `0958eac` (`slop-code:pi-1.3.0-pdt0958eac-toolstest-python3.12`) showed both on PATH for a non-agent uid under `bash -l`. `scripts/build_pidt_local.sh` now copies the template's tools block into local builds, so a locally built image matches the template.

The pi-dev-team SHA is still a placeholder (`TODO_PDT_SHA`, version `1.3.0-pdtpending-tools`) until the `DEV_TEAM_REVIEW=lean` commit lands on `request-as-spec`. `scripts/set_pdt_sha.sh <sha>` sets the template SHA and both arm versions in one step; then `scripts/build_pidt_local.sh agents/pidt-full.yaml` builds the shared image. `run.sh` warns on a dry run and refuses a real run while the placeholder is in place.

New subset `heldout`: `file_merger`, `layered_config_synthesizer` and `dynamic_buffer`, each cut to checkpoints 1-2 (`HELDOUT_CHECKPOINTS`, default 2) through the same truncated-catalog mechanism as `smoke`. These problems were never used while tuning pi-dev-team (`file_backup` was, so it is left out). `smoke` now also takes a comma list in `SMOKE_PROBLEM`. `scripts/cost_guard.sh` accepts the whole run dir as `RUN` and sums the cost over all problems; because the two arms share an image, run them one after the other while a guard is active.

## file_merger checkpoint 1: four arms on the same task (2026-10-21)

All four runs are CLEAN. pi-dev-team is at `3b80fff` (DEV_TEAM_REVIEW switch); both pi-dev-team images now carry ruff 0.17.1 and lizard 1.24.1. The pi-dev-team runs were started on the `heldout` subset and stopped by hand after `file_merger` checkpoint 1, on the user's call (lean at the start of checkpoint 2, full as checkpoint 2 began). Plain pi and Claude Code ran `SMOKE_PROBLEM=file_merger SMOKE_CHECKPOINTS=1`.

Program files only (`scripts/program_quality.py`):

| Arm | Run dir | Tests | Cost | Time | Lines | Verbosity | Erosion | Clones | Lint/line | cc max |
|---|---|---|---|---|---|---|---|---|---|---|
| cc-clean (Opus 5.5) | `results/20261021-cc-clean-smoke-r1` | 44/46 | $0.90 | 212 s | 623 | 0.293 | 0.653 | 2.0% | 0.141 | 31 |
| pi-stock (Sol) | `results/20261021-pi-stock-smoke-r4` | 45/46 | $0.25 | 427 s | 495 | 0.186 | 0.396 | 0% | 0.156 | 17 |
| pidt-full (Sol) | `results/20261021-pidt-full-heldout-r1` | 45/46 | $18.73 | 7909 s | 544 | 0.116 | 0.440 | 0% | 0.125 | 17 |
| pidt-lean (Sol) | `results/20261021-pidt-lean-heldout-r1` | 45/46 | $9.22 | 3909 s | 502 | 0.146 | 0.000 | 0% | 0.098 | 8 |

Every arm fails `test_error_cases[spec_errors/bool_nonstandard_strict]`; Claude Code also fails `test_spec_correct_cases[spec_correct/cross_family_coercion]`. On this task the extra pi-dev-team time buys no tests. Lean is half the cost and time of full and gives the best code-quality numbers; it treats a lizard threshold breach as an error at every checkpoint, while full leaves lizard to the final review.
