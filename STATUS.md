# Status

Last updated: 2026-10-20. Plan overview: [`README.md`](README.md#plan).

## Steps 1-4

| # | Step | Status | Evidence |
|---|------|--------|----------|
| 1 | SCB fork + patches | Done in fork `harness-eval` | `67c6cf0` Copilot FILE credential -> only the `github-copilot` entry copied into the pi agent dir; `bb0e1f6` toolResult usage counted in cost/tokens; `71a2a11` `github-copilot` provider + `opus-5.5`, `gpt-6.1-sol`, `copilot-claude-opus-5.5` models; `5fa3795` tool-result cost fallback picks prompt tiers per subagent turn. `tests/agent_runner/agents/pi_agent_test.py`: 68 passed (48 on upstream `31ceea3` + 20 new). `tests/agent_runner` + `tests/common`: 516 passed, 2 failed (`resume_integration_test` needs a Docker daemon; fails on upstream too). Submodule pointer committed at `5fa3795`. **Not pushed**: a fresh clone cannot fetch the pinned SHA until `harness-eval` is pushed to the fork. |
| 2 | Repo skeleton | Done | `.gitignore` (results/, outputs/, auth files, tokens), `pyproject.toml` (pyyaml, pytest) |
| 3 | Configs, templates, prompts | Done | `agents/{cc-clean,pidt,pi-stock}.yaml`, `templates/{pidt,pi-stock}.j2`, `environments/docker-python3.12-uv-pidt.yaml`, `prompts/pidt.jinja`, `runs/<arm>_{lite,lite_under20}.yaml` |
| 4 | `run.sh`, manifest, clean check | Done | `run.sh --dry-run` resolves through SCB's loaders for 4 arms x 4 subsets (16/16, expected model printed for each); `uv run --with pytest pytest -q` 52 passed |

What step 4 delivers:

- `run.sh ARM SUBSET [REPEAT]`: maps arm to agent/model/prompt/environment, exports `TMPDIR=/tmp`, loads the Claude OAuth token from `~/.config/scb/cc_oauth_token` without printing it, checks the Copilot login exists, writes `manifest.json`, runs slop-code into `results/<date>-<arm>-<subset>-r<n>/`, then runs the clean check and records CLEAN/DIRTY in the manifest.
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

## Next: step 5, real smoke (1 problem, 2 checkpoints, per arm)

This step spends real (subscription) model calls and builds the SCB images.

1. Push the fork's `harness-eval` branch if the run machine is not this one (the submodule pointer is committed).
2. Install the catalog once (network, no model calls):
   `uv run --project vendor/slop-code-bench slop-code sync`
3. Make sure Docker is running and both logins exist (`~/.config/scb/cc_oauth_token`; `github-copilot` in `~/.pi/agent/auth.json`).
4. Dry-run each arm and check the output for `WARNING` lines and `"errors": []`:
   `./run.sh --dry-run cc-clean smoke`, then the same for `pidt`.
5. Run arm A, then arm B (C and pi-stock are optional):
   ```sh
   ./run.sh cc-clean smoke
   ./run.sh pidt smoke          # slow: about 23 min per toy checkpoint seen in the container smoke
   ./run.sh pidt-opus smoke     # optional
   ./run.sh pi-stock smoke      # optional
   ```
   The first run of each arm builds its image: `claude_code-2.1.304-python3.12`, `pi-1.3.0-pdt3c34bf5-python3.12`, `pi-1.3.0-stock-python3.12`.
6. Check each `results/<date>-<arm>-smoke-r1/`:
   - `manifest.json`: `status: finished`, `clean.status: CLEAN`, and the expected SHAs, versions and model.
   - Both checkpoints have `agent/stdout.jsonl` and evaluation results.
   - pidt cost: SCB's reported cost should now include the `dev_team_subagent` spend. In the container smoke, the main agent alone was $0.766 of $2.154 total (36%). If SCB shows only the main-agent share, patch 2 is not in effect.
   - pidt: `plans/` and `.claude/memory/` are in the checkpoint snapshot, and the agent made no `gh` / `/ship` / `/pr` calls.
7. Record the outcomes here, mark step 5 done in `README.md`, and move on to step 6 (`./run.sh <arm> lite_under20`).
