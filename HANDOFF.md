# Handoff: harness eval and pi-dev-team fixes (2026-10-21)

Read `STATUS.md` (full results tables) and `README.md` (the 9-step plan) first. This file is the short version plus the open pi-dev-team work.

## Goal

Compare clean Claude Code (arm A) with pi + pi-dev-team (https://github.com/sanjit-roopra/pi-dev-team) on SlopCodeBench, with real costs including sub-agents and `plans/` kept between checkpoints. Later do the same for the "software factory". We are at step 5 (smoke); step 6 is `lite_under20`.

## Terms

- **Problem**: one SCB task, e.g. `file_backup`.
- **Checkpoint**: one stage of a problem's spec, graded by hidden pytest. Each checkpoint starts the agent with a fresh context in the same workspace.
- **Smoke**: `file_backup` cut to its first N checkpoints (`SMOKE_CHECKPOINTS`, default 2). Checkpoint 1 is a CLI scheduler that reads a YAML schedule and prints JSONL events; checkpoint 2 adds backup strategies.
- **Arms** (`run.sh ARM SUBSET`):

| Arm | Harness | Model | Agent yaml |
|---|---|---|---|
| `cc-clean` | Claude Code 2.1.304 | `claude-opus-5-5` (Claude OAuth) | `agents/cc-clean.yaml` |
| `pidt` | pi 1.3.0 + pi-dev-team `3c34bf5` (main) | `github-copilot/gpt-6.1-sol` | `agents/pidt.yaml` |
| `pidt-next` | pi 1.3.0 + pi-dev-team `feb5afd` (candidate) | `github-copilot/gpt-6.1-sol` | `agents/pidt-next.yaml` |
| `pidt-opus` | pi 1.3.0 + pi-dev-team `feb5afd` | `github-copilot/claude-opus-5.5` | `agents/pidt-opus.yaml` |

## Results so far (file_backup)

| Run dir | Checkpoint 1 (core / functionality / error) | Checkpoint 2 (core / functionality / regression) | Cost (notional, incl. sub-agents) | Time |
|---|---|---|---|---|
| `results/20261020-cc-clean-smoke-r1` | 1/1, 23/27, 2/4 | 1/1, 12/17, 26/32 | $0.44 + $0.37 | 124 s + 61 s |
| `results/20261020-pidt-smoke-r1` | 0/1, 4/27, 4/4 | aborted | $7.58 + ~$7 | 4740 s + aborted |
| `results/20261020-pidt-next-smoke-r1` | 1/1, 16/27, 4/4 | 1/1, 9/17, 21/32 | $12.07 + $8.37 | 5484 s + 4142 s |
| `results/20261021-pidt-opus-smoke-r1` (checkpoint 1 only) | 1/1, 16/27, 4/4 | not run | $20.69 | 2815 s |

Costs are list prices applied to the trace's token counts (checked by hand against the Sol and Opus rates); both sides actually run on subscriptions.

**Main finding:** pi-dev-team on Opus 5.5 fails the same 11 checkpoint 1 tests as on Sol, with the same wrong values, while Claude Code on Opus 5.5 passes 7 of them. The gap is the harness, not the model.

## Why pi-dev-team loses points

1. **Fixed already (candidate `feb5afd`): plan drift from the spec.** With the spec inline and no `docs/specs` artifact, upstream `/plan` made the plan the only spec, the model rewrote `Z` timestamps as `+00:00`, and every reviewer checked against the plan. `feb5afd` saves the typed `/plan` request word for word to `.claude/memory/requests/plan-<ts>.md` and makes that file outrank the plan. Verified in both runs: the plan has a `**Source request**:` line, all timestamps end in `Z`, and core passes.
2. **Open: ambiguity locked in as "Plan decisions".** Both final plans (Sol and Opus) hold two unclear spec points settled the same wrong way as acceptance criteria. Sol planned them that way and every reviewer honoured them. Opus first planned the right per-trigger reading with the doubt under Risks; `spec-compliance-review` at the build's acceptance-criteria gate ("request outranks plan") read "Sort due jobs by `id`" and "For each due job" as once per invocation, and the build switched (plan Risks line "Revised at the build's acceptance-criteria gate"):
   - "Each due job runs exactly once per invocation, even when it triggers more than once in the window." The tests want one run per trigger in the `[--now, --now + --duration]` window (43 events expected, 15 printed).
   - "`JOB_ELIGIBLE.now_local` is `floor(--now)` ... (Plan decision; the request examples use `--now` equal to the trigger.)" The tests want the trigger time. The Opus plan saw the doubt and decided anyway.
   - Evidence: `results/20261021-pidt-opus-smoke-r1/file_backup/checkpoint_1/snapshot/plans/backup-scheduler.md` lines 21-23; the Sol plan in `results/20261020-pidt-next-smoke-r1/.../plans/backup-scheduler.md` line 26.
3. **Not agent drift: checkpoint 2 test/spec mismatch** (Sol run). Hidden tests omit `JOB_COMPLETED.total_size` for verify and dry-run jobs, while spec Example 1 shows `"total_size":0` and the text says verify output is identical except kind/event. Hidden tests also close a pack only after the next `FILE_SELECTED`, while Example 2 shows `PACK_CREATED` pack-3 straight after `FILE_PACKED` file4. Following the examples causes these failures; count them with the glob tests.
4. **Not an agent failure:** the 4 `glob_*` hidden tests fail in all runs, Claude Code included (`--mount mount` path error, or for pidt-opus 4 events instead of 6-8). Check the test cases before counting them.
5. **Cost and time:** about 30+ sub-agent calls per checkpoint (in the first pidt run the code-review swarm was $3.35 of $7.58), 26M cache-read tokens against Claude Code's 0.2M, 45-90 minutes per checkpoint. Step 6 (`lite_under20`, about 11 checkpoints) would be about $110+ and 15 hours per repeat at this rate.

## Possible pi-dev-team fixes (none built yet beyond `feb5afd`)

Repo: `/Users/sanjit.roopra/git/personal/pi-dev-team`, branch `request-as-spec` at `feb5afd`, committed, **not pushed** (the user decides on pushing or a PR). Base: main `3c34bf5`.

Repo rules (from `PORTING.md` and the earlier session): the tree is a port of upstream bdfinst/agentic-dev-team pinned at `2bf3d985`. Never hand-edit `agents/` or `skills/`; change them through `sync/sync_upstream.py` (`PATCHES` / `LEAN_PATCHES`, literal replacements that must each hit at least once) or `overrides/notes/<skill>.md` (inserted at the `<!-- pi-port-notes -->` marker), then run `npm run sync` with the upstream checkout at `2bf3d985` (restore it to its branch afterwards). Python stays byte-identical to upstream. Sub-agents see only their task string (`extensions/dev-team/subagent.ts:307,423`). Document each change in `PORTING.md` §5. Checks: `npm test` (node + Python), `npm run e2e`, `npx -y -p typescript@5.9.3 tsc -p .`.

Candidates, in order of expected value:

1. **Ambiguity is not a decision** (`overrides/notes/plan.md`). When the source request is unclear on a behaviour, the plan must (a) pick the reading its output examples imply and say so, and (b) list the other reading under `## Risks & Open Questions` as unresolved, not as an acceptance criterion marked "Plan decision". In non-interactive runs this keeps the choice visible to reviewers instead of protected. Add the matching line to `overrides/notes/build.md`: an open question from the plan is re-checked against the source request at spec-compliance, never treated as settled.
2. **Reviewers may challenge plan decisions** (a `PATCHES` entry on `agents/spec-compliance-review.md`, next to the `feb5afd` patch). Today the orchestrator's dispatch can say "Do not revisit explicitly recorded contract choices" (seen in the first pidt trace). The patch: a plan decision that narrows or reinterprets the source request's wording is itself a finding to report.
3. **One scenario per stated rule** (`overrides/notes/plan.md`; feb5afd's 4.9 diff already covers the literal examples). Each stated observable rule gets a Gherkin scenario; rules that depend on an open question are tested only where all readings agree. It does not target the checkpoint 2 failures (see item 3 above).
4. **Save the request for `/ship` and `/specs` too**, not only `/plan` (`SAVE_REQUEST_SKILLS` in `extensions/dev-team/lib/skills.ts`). Consider whether `.claude/memory/requests/` should be git-ignored in user repos.
5. **Cut cost** before step 6: fewer code-review rounds (3 rounds seen), a lighter reviewer set for single-file CLI work (the plan tier table in `skills/plan/SKILL.md` §5a already scales plan reviewers; the build-time review swarm does not scale the same way), and no QA agent when the plan's tests already ran.

Before claiming a fix works, prove it on checkpoint 1 only (`SMOKE_CHECKPOINTS=1`) on Sol (cheaper per token: about $12 against about $21 on Opus). Watch the 7 window tests (`daily_due_within_duration_no_select`, `daily_multi_trigger`, `exact_duration_boundary`, `once_due`, `weekly_multi_trigger`, `weekly_multiple_days`, `weekly_wrong_day`) and keep the `Z` format passing. Be careful that "examples outrank prose" style rules can be benchmark-shaped; keep them general.

## How to run a candidate

1. Commit the fix in pi-dev-team.
2. In `templates/pidt-next.j2` set `PDT_SHA` to the new full SHA, and set `version: 1.3.0-pdt<sha7>` in `agents/pidt-next.yaml` and `agents/pidt-opus.yaml` (the version is SCB's image cache key).
3. `scripts/build_pidt_local.sh` builds `slop-code:pi-<version>-python3.12` from the local checkout (needed while the commit is not on GitHub).
4. `SMOKE_CHECKPOINTS=1 ./run.sh pidt-next smoke` in the background, then once the container is up start `scripts/cost_guard.sh` (see its header) with a cost and time cap.
5. Results land in `results/<date>-<arm>-smoke-r<n>/`: `file_backup/checkpoint_N/{evaluation.json, evaluation/stdout.txt, inference_result.json, agent/stdout.jsonl, snapshot/plans/}`, plus `clean_check.json` (must say CLEAN and name only the expected model).

## Budget and safety

- The user's cap: **at most $100 list price of Copilot spend per night** on pi-dev-team runs; Copilot may throttle otherwise. Spent 2026-10-20/21: about $56. Only start runs whose spend you can bound (guard + checkpoint 1 only).
- Copilot bills by premium requests with per-model multipliers, not tokens; the dollar figures here are notional.
- Never print or commit secrets. The Claude Code token is read only into the environment at run time from `~/.config/scb/cc_oauth_token`. Copilot auth reaches the container only through the SCB file credential (`configs/providers.yaml`, fork patch `67c6cf0` copies just the `github-copilot` entry of `~/.pi/agent/auth.json`).
- A filtered copy of the Copilot `auth.json` is in the old session's scratchpad (`/private/tmp/claude-501/-Users-sanjit-roopra-git-dedalus-eval-coding/bcd571d8-cc6a-4b98-bafc-ee9dfad7d374/scratchpad/smoke1/agent/auth.json`); delete it.
- Pushing is outward-facing: the fork `vendor/slop-code-bench` (branch `harness-eval`) is pushed up to `0a5f32c`; pi-dev-team `request-as-spec` is not pushed.

## Reporting to the user

Telegraphic bullets and tables. Always name the harness and model of a run, explain jargon such as "checkpoint", and give verified tokens and full cost including sub-agents.
