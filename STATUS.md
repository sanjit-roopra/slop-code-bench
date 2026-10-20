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

## Next

1. Decide how to handle the pidt plan drift (prompt change in `prompts/pidt.jinja`, or a pi-dev-team change that makes plans quote exact output formats from the spec), then rerun `./run.sh pidt smoke`.
2. Cut pidt cost and time before step 6 (11 checkpoints): at about $7.58 and 79 min per checkpoint, `lite_under20` would be roughly $80+ notional and 14+ hours for one repeat.
3. Optional: arm C `pidt-opus` smoke, to separate the harness effect from the model.
4. Record outcomes here and in `README.md`, then step 6.
