#!/usr/bin/env bash
# Run one arm of the harness eval on a SlopCodeBench subset.
#
#   ./run.sh [options] ARM SUBSET [REPEAT]
#
#   ARM     cc-clean | pidt | pidt-next | pidt-opus | pidt-full | pidt-lean | pidt-lean-fast | pi-stock
#   SUBSET  smoke | heldout | lite_under20 | lite | full
#           smoke   = SMOKE_PROBLEM (comma list allowed), first SMOKE_CHECKPOINTS checkpoints
#           heldout = file_merger, layered_config_synthesizer, dynamic_buffer,
#                     first HELDOUT_CHECKPOINTS checkpoints (default 2)
#   REPEAT  repeat index, default 1 (output dir suffix -r<REPEAT>)
#
# Options:
#   --dry-run        resolve and print everything (incl. the slop-code command); run nothing
#   --workers N      parallel problems (slop-code --num-workers), default 1
#   --no-evaluate    skip SCB evaluation (agent only)
#   --resume DIR     resume an existing run dir (slop-code --resume); ARM/SUBSET must
#                    match the dir's manifest.json. With --dry-run, shows slop-code's resume preview
#   -h | --help      this help
#
# Env overrides: CC_MODEL, PIDT_MODEL, PIDT_OPUS_MODEL, PI_STOCK_MODEL,
#   SMOKE_PROBLEM (default file_backup; comma list allowed), SMOKE_CHECKPOINTS (default 2),
#   HELDOUT_CHECKPOINTS (default 2),
#   SCBENCH_PROBLEMS_SRC (installed catalog, default ~/.cache/scbench/problems),
#   CC_TOKEN_FILE (default ~/.config/scb/cc_oauth_token), PI_AUTH_FILE (default ~/.pi/agent/auth.json).
#
# Output: results/<YYYYMMDD>-<arm>-<subset>-r<n>/ with manifest.json (written
# before the run) and clean_check.json (after). Secrets are never printed.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCB="$(cd "$ROOT/.." && pwd)"  # this fork; the eval lives in its harness_eval/
cd "$ROOT"

# SCB bind-mounts host temp dirs (workspace, agent config, entry scripts) into
# the containers. VM-backed Docker on macOS (Rancher Desktop, Colima) shares
# only $HOME with the VM, so /tmp and /var/folders mounts arrive empty. Keep
# SCB's temp dirs under $HOME. The container-side HOME is fixed at
# /tmp/agent_home in the fork (agents/utils.py), independent of TMPDIR.
SCB_TMPDIR="${SCB_TMPDIR:-$HOME/.cache/scb-tmp}"
mkdir -p "$SCB_TMPDIR"
export TMPDIR="$SCB_TMPDIR"

# The Python docker SDK ignores CLI contexts (Rancher Desktop, Colima, OrbStack
# put the socket outside /var/run/docker.sock), so resolve it from the active one.
if [[ -z "${DOCKER_HOST:-}" ]] && command -v docker >/dev/null 2>&1; then
  DOCKER_HOST="$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)"
  [[ -n "$DOCKER_HOST" ]] && export DOCKER_HOST || unset DOCKER_HOST
fi

CC_VERSION="2.1.304"
CC_TOKEN_FILE="${CC_TOKEN_FILE:-$HOME/.config/scb/cc_oauth_token}"
PI_AUTH_FILE="${PI_AUTH_FILE:-$HOME/.pi/agent/auth.json}"
SMOKE_PROBLEM="${SMOKE_PROBLEM:-file_backup}"
SMOKE_CHECKPOINTS="${SMOKE_CHECKPOINTS:-2}"
HELDOUT_CHECKPOINTS="${HELDOUT_CHECKPOINTS:-2}"
# Held-out dev set for pi-dev-team variants: never used while tuning (file_backup was).
HELDOUT_PROBLEMS=(file_merger layered_config_synthesizer dynamic_buffer)
SCBENCH_PROBLEMS_SRC="${SCBENCH_PROBLEMS_SRC:-$HOME/.cache/scbench/problems}"
THINKING="high"

die() { echo "run.sh: $*" >&2; exit 2; }
warn() { echo "run.sh: WARNING: $*" >&2; }
usage() { sed -n '2,29p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

DRY_RUN=0
WORKERS=1
EVALUATE="--evaluate"
RESUME=""
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --workers) WORKERS="${2:?--workers needs a value}"; shift 2 ;;
    --no-evaluate) EVALUATE="--no-evaluate"; shift ;;
    --resume) RESUME="${2:?--resume needs a dir}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    --) shift; POSITIONAL+=("$@"); break ;;
    -*) die "unknown option $1 (see --help)" ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done
[[ ${#POSITIONAL[@]} -ge 2 && ${#POSITIONAL[@]} -le 3 ]] || { usage >&2; exit 2; }
ARM="${POSITIONAL[0]}"
SUBSET="${POSITIONAL[1]}"
REPEAT="${POSITIONAL[2]:-1}"
[[ "$REPEAT" =~ ^[1-9][0-9]*$ ]] || die "REPEAT must be a positive integer, got '$REPEAT'"
[[ "$WORKERS" =~ ^[1-9][0-9]*$ ]] || die "--workers must be a positive integer"

# --- Arm -> agent / model / prompt / environment ----------------------------
ENV_PI="$ROOT/environments/docker-python3.12-uv-pidt.yaml"
NEEDS_CC_TOKEN=0
NEEDS_PI_AUTH=0
case "$ARM" in
  cc-clean)
    AGENT_YAML="$ROOT/agents/cc-clean.yaml"
    MODEL="${CC_MODEL:-claude_code_oauth/opus-5.5}"
    PROMPT="just-solve"                      # SCB built-in configs/prompts/just-solve.jinja
    ENVIRONMENT="docker-python3.12-uv"       # SCB built-in
    NEEDS_CC_TOKEN=1
    ;;
  pidt)
    AGENT_YAML="$ROOT/agents/pidt.yaml"
    MODEL="${PIDT_MODEL:-github-copilot/gpt-6.1-sol}"
    PROMPT="$ROOT/prompts/pidt.jinja"
    ENVIRONMENT="$ENV_PI"
    NEEDS_PI_AUTH=1
    ;;
  pidt-next)
    # A pi-dev-team candidate (agents/pidt-next.yaml pins its SHA) against arm B: same model and prompt.
    AGENT_YAML="$ROOT/agents/pidt-next.yaml"
    MODEL="${PIDT_MODEL:-github-copilot/gpt-6.1-sol}"
    PROMPT="$ROOT/prompts/pidt.jinja"
    ENVIRONMENT="$ENV_PI"
    NEEDS_PI_AUTH=1
    ;;
  pidt-opus)
    # pi-dev-team on the same model as arm A, to separate the harness effect from the model.
    AGENT_YAML="$ROOT/agents/pidt-opus.yaml"
    [[ -f "$AGENT_YAML" ]] || AGENT_YAML="$ROOT/agents/pidt.yaml"
    MODEL="${PIDT_OPUS_MODEL:-github-copilot/copilot-claude-opus-5.5}"
    PROMPT="$ROOT/prompts/pidt.jinja"
    ENVIRONMENT="$ENV_PI"
    NEEDS_PI_AUTH=1
    ;;
  pidt-full|pidt-lean|pidt-lean-fast)
    # pi-dev-team with DEV_TEAM_REVIEW=full | lean: the agent yamls differ only in env
    # vars and image tag and share one template (templates/pidt-tools.j2, adds ruff + lizard).
    AGENT_YAML="$ROOT/agents/$ARM.yaml"
    MODEL="${PIDT_MODEL:-github-copilot/gpt-6.1-sol}"
    PROMPT="$ROOT/prompts/pidt.jinja"
    ENVIRONMENT="$ENV_PI"
    NEEDS_PI_AUTH=1
    ;;
  pi-stock)
    AGENT_YAML="$ROOT/agents/pi-stock.yaml"
    MODEL="${PI_STOCK_MODEL:-github-copilot/gpt-6.1-sol}"
    PROMPT="just-solve"
    ENVIRONMENT="$ENV_PI"
    NEEDS_PI_AUTH=1
    ;;
  *) die "unknown ARM '$ARM' (cc-clean|pidt|pidt-next|pidt-opus|pidt-full|pidt-lean|pidt-lean-fast|pi-stock)" ;;
esac

# --- Subset -> problem selection ---------------------------------------------
RUN_CONFIG=""
PROBLEM_ARGS=()
SMOKE_ROOT=""
CATALOG_PROBLEMS=()
CATALOG_CHECKPOINTS=""
case "$SUBSET" in
  smoke|heldout)
    # slop-code has no checkpoint-limit flag: build a catalog of the chosen problems,
    # each cut to its first N checkpoints, and point SCBENCH_PROBLEMS_PATH at it.
    if [[ "$SUBSET" == smoke ]]; then
      IFS=, read -ra CATALOG_PROBLEMS <<< "$SMOKE_PROBLEM"
      CATALOG_CHECKPOINTS="$SMOKE_CHECKPOINTS"
      catalog_name="${SMOKE_PROBLEM//,/+}"
    else
      CATALOG_PROBLEMS=("${HELDOUT_PROBLEMS[@]}")
      CATALOG_CHECKPOINTS="$HELDOUT_CHECKPOINTS"
      catalog_name="heldout"
    fi
    [[ ${#CATALOG_PROBLEMS[@]} -gt 0 ]] || die "no problems for subset $SUBSET"
    [[ "$CATALOG_CHECKPOINTS" =~ ^[1-9][0-9]*$ ]] || die "checkpoint count must be a positive integer, got '$CATALOG_CHECKPOINTS'"
    # One catalog per arm: two arms started together would otherwise rebuild the same dir at once.
    SMOKE_ROOT="$ROOT/results/.smoke-catalog/${catalog_name}-c${CATALOG_CHECKPOINTS}-${ARM}"
    for p in "${CATALOG_PROBLEMS[@]}"; do PROBLEM_ARGS+=(--problem "$p"); done
    ;;
  lite_under20|lite)
    RUN_CONFIG="$ROOT/runs/${ARM}_${SUBSET}.yaml"
    [[ -f "$RUN_CONFIG" ]] || RUN_CONFIG="$SCB/configs/runs/${SUBSET}.yaml"
    ;;
  full) ;;  # all problems in the installed catalog
  *) die "unknown SUBSET '$SUBSET' (smoke|heldout|lite_under20|lite|full)" ;;
esac

# --- Output dir ---------------------------------------------------------------
SAVED_EXPECT_MODEL=""
if [[ -n "$RESUME" ]]; then
  RUN_DIR="$(cd "$RESUME" 2>/dev/null && pwd)" || die "--resume dir not found: $RESUME"
  # The clean check and expected model depend on the arm, so a resume must use
  # the arm (and subset) the run was started with.
  SAVED_MANIFEST="$RUN_DIR/manifest.json"
  [[ -f "$SAVED_MANIFEST" ]] || die "--resume: no manifest.json in $RUN_DIR (not a run.sh run dir?)"
  saved_arm="$(jq -r '.arm // empty' "$SAVED_MANIFEST")"
  saved_subset="$(jq -r '.subset // empty' "$SAVED_MANIFEST")"
  [[ "$saved_arm" == "$ARM" ]] || die "--resume: $RUN_DIR was run as arm '$saved_arm', not '$ARM'"
  [[ "$saved_subset" == "$SUBSET" ]] || die "--resume: $RUN_DIR was run as subset '$saved_subset', not '$SUBSET'"
  SAVED_EXPECT_MODEL="$(jq -r '.model.expected_runtime // empty' "$SAVED_MANIFEST")"
  saved_model="$(jq -r '.model.cli // empty' "$SAVED_MANIFEST")"
  [[ -n "$saved_model" ]] && MODEL="$saved_model"   # what the run was started with
else
  RUN_NAME="$(date +%Y%m%d)-${ARM}-${SUBSET}-r${REPEAT}"
  RUN_DIR="$ROOT/results/$RUN_NAME"
  if [[ -e "$RUN_DIR" && $DRY_RUN -eq 0 ]]; then
    die "$RUN_DIR exists; use a new REPEAT or --resume $RUN_DIR"
  fi
fi

# --- Preflight (no secrets printed) -------------------------------------------
missing=()
for f in "$AGENT_YAML" "$RUN_CONFIG"; do
  [[ -z "$f" || -f "$f" ]] || missing+=("$f")
done
[[ "$PROMPT" != /* || -f "$PROMPT" ]] || missing+=("$PROMPT")
[[ "$ENVIRONMENT" != /* || -f "$ENVIRONMENT" ]] || missing+=("$ENVIRONMENT")
if [[ ${#missing[@]} -gt 0 ]]; then
  for f in "${missing[@]}"; do
    if [[ $DRY_RUN -eq 1 ]]; then warn "missing $f"; else die "missing $f"; fi
  done
fi

model_name="${MODEL#*/}"
if [[ ! -f "$SCB/configs/models/${model_name}.yaml" ]] \
   && ! grep -qsE "^[[:space:]]*-[[:space:]]*${model_name//./\\.}[[:space:]]*$" "$SCB"/configs/models/*.yaml; then
  warn "model '$model_name' not in $SCB/configs/models (name or alias)"
fi

if [[ "$ARM" == cc-clean && -f "$AGENT_YAML" ]]; then
  grep -qE "^version:[[:space:]]*${CC_VERSION//./\\.}[[:space:]]*$" "$AGENT_YAML" \
    || warn "$AGENT_YAML version is not $CC_VERSION"
fi

cred_status="n/a"
if [[ $NEEDS_CC_TOKEN -eq 1 ]]; then
  if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
    cred_status="CLAUDE_CODE_OAUTH_TOKEN from environment"
  elif [[ -s "$CC_TOKEN_FILE" && $DRY_RUN -eq 1 ]]; then
    cred_status="token file present ($CC_TOKEN_FILE; not read in dry run)"
  elif [[ -s "$CC_TOKEN_FILE" ]]; then
    CLAUDE_CODE_OAUTH_TOKEN="$(tr -d '[:space:]' < "$CC_TOKEN_FILE")"
    export CLAUDE_CODE_OAUTH_TOKEN
    cred_status="CLAUDE_CODE_OAUTH_TOKEN loaded from $CC_TOKEN_FILE"
  elif [[ $DRY_RUN -eq 1 ]]; then
    cred_status="MISSING ($CC_TOKEN_FILE)"
  else
    die "no Claude OAuth token: set CLAUDE_CODE_OAUTH_TOKEN or write $CC_TOKEN_FILE"
  fi
fi
if [[ $NEEDS_PI_AUTH -eq 1 ]]; then
  if [[ -f "$PI_AUTH_FILE" ]] && jq -e 'has("github-copilot")' "$PI_AUTH_FILE" >/dev/null 2>&1; then
    cred_status="github-copilot entry present in $PI_AUTH_FILE"
  elif [[ $DRY_RUN -eq 1 ]]; then
    cred_status="MISSING github-copilot entry in $PI_AUTH_FILE"
  else
    die "no github-copilot login in $PI_AUTH_FILE (run: pi, then /login github-copilot)"
  fi
fi

# --- Build the slop-code command ------------------------------------------------
CMD=(uv run --project "$SCB" slop-code run)
if [[ -n "$RESUME" ]]; then
  # --resume loads the saved config; SCB rejects agent/model/prompt/env flags with it.
  CMD+=(--resume "$RUN_DIR")
else
  [[ -n "$RUN_CONFIG" ]] && CMD+=(--config "$RUN_CONFIG")
  CMD+=(--agent "$AGENT_YAML" --model "$MODEL" --prompt "$PROMPT" --environment "$ENVIRONMENT")
  CMD+=(${PROBLEM_ARGS[@]+"${PROBLEM_ARGS[@]}"})
fi
CMD+=(--num-workers "$WORKERS" "$EVALUATE" --no-live-progress)
if [[ -z "$RESUME" ]]; then
  CMD+=("thinking=$THINKING" "save_dir=$ROOT/results" "save_template=$RUN_NAME")
fi

quote_cmd() { local out="" a; for a in "$@"; do out+="$(printf '%q' "$a") "; done; echo "${out% }"; }
CMD_STR="$(quote_cmd "${CMD[@]}")"
ENV_PREFIX="TMPDIR=$TMPDIR"
[[ -n "${DOCKER_HOST:-}" ]] && ENV_PREFIX+=" DOCKER_HOST=$DOCKER_HOST"
[[ -n "$SMOKE_ROOT" ]] && ENV_PREFIX+=" SCBENCH_PROBLEMS_PATH=$SMOKE_ROOT"

PDT_TEMPLATE="$ROOT/$(sed -n 's/^docker_template: *//p' "$AGENT_YAML" 2>/dev/null)"
PDT_SHA="$(grep -oE 'PDT_SHA = "[0-9a-f]+"' "$PDT_TEMPLATE" 2>/dev/null | grep -oE '[0-9a-f]{7,}' || true)"
if [[ -z "$PDT_SHA" ]] && grep -q 'PDT_SHA = "TODO' "$PDT_TEMPLATE" 2>/dev/null; then
  PDT_SHA="TODO (placeholder in ${PDT_TEMPLATE#$ROOT/}; set it with scripts/set_pdt_sha.sh)"
  [[ $DRY_RUN -eq 1 ]] && warn "pi-dev-team SHA not set yet: $PDT_SHA" || die "pi-dev-team SHA not set: $PDT_SHA"
fi

echo "arm:         $ARM"
echo "subset:      $SUBSET (repeat $REPEAT)"
echo "agent:       $AGENT_YAML"
echo "model:       $MODEL  thinking=$THINKING"
echo "prompt:      $PROMPT"
echo "environment: $ENVIRONMENT"
[[ -n "$RUN_CONFIG" ]] && echo "run config:  $RUN_CONFIG"
[[ -n "$SMOKE_ROOT" ]] && echo "problems:    $(IFS=,; echo "${CATALOG_PROBLEMS[*]}"), first $CATALOG_CHECKPOINTS checkpoints -> $SMOKE_ROOT"
[[ "$ARM" == pidt* ]] && echo "pi-dev-team: ${PDT_SHA:-unknown}"
echo "credentials: $cred_status"
echo "run dir:     $RUN_DIR"
echo "command:     $ENV_PREFIX $CMD_STR"

MANIFEST_ARGS=(write --run-dir "$RUN_DIR" --arm "$ARM" --subset "$SUBSET" --repeat "$REPEAT"
  --agent "$AGENT_YAML" --model "$MODEL" --prompt "$PROMPT" --environment "$ENVIRONMENT"
  --thinking "$THINKING" --command "$ENV_PREFIX $CMD_STR")
[[ -n "$RUN_CONFIG" ]] && MANIFEST_ARGS+=(--run-config "$RUN_CONFIG")
[[ -n "$SMOKE_ROOT" ]] && MANIFEST_ARGS+=(${PROBLEM_ARGS[@]+"${PROBLEM_ARGS[@]}"} --smoke-checkpoints "$CATALOG_CHECKPOINTS")
PY=(uv run --quiet --project "$SCB" python)

# Model id the agent must report at run time (CC init model / pi provider/model);
# check_clean.py marks the run DIRTY on any other main or subagent model.
if [[ -n "$SAVED_EXPECT_MODEL" ]]; then
  EXPECT_MODEL="$SAVED_EXPECT_MODEL"
else
  EXPECT_MODEL="$("${PY[@]}" scripts/manifest.py expect-model --model "$MODEL" --agent "$AGENT_YAML")" \
    || die "cannot resolve the runtime model id for $MODEL"
fi
echo "expect model: $EXPECT_MODEL"

if [[ $DRY_RUN -eq 1 ]]; then
  if [[ -n "$RESUME" ]]; then
    # slop-code's own resume preview (what is done / still to run). It resolves
    # credentials before previewing, so a placeholder stands in for the Claude
    # token: the real one is not read in a dry run, and the preview returns
    # before any agent starts.
    echo "resume preview (slop-code --dry-run):"
    [[ -n "$SMOKE_ROOT" ]] && export SCBENCH_PROBLEMS_PATH="$SMOKE_ROOT"
    if [[ $NEEDS_CC_TOKEN -eq 1 && -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
      CLAUDE_CODE_OAUTH_TOKEN="dry-run-placeholder" "${CMD[@]}" --dry-run
    else
      "${CMD[@]}" --dry-run
    fi
    exit $?
  fi
  # Resolve through SCB's own loaders (config, env, model catalog, agent
  # schema, image tag). No credentials, no docker, no model calls.
  echo "resolved (SCB loaders):"
  if "${PY[@]}" scripts/resolve_config.py "${CMD[@]:6}"; then
    echo "dry run: OK, nothing executed"
  else
    echo "dry run: SCB could not resolve this configuration" >&2
    exit 1
  fi
  exit 0
fi

# --- Run ------------------------------------------------------------------------
if [[ -n "$SMOKE_ROOT" ]]; then
  for p in "${CATALOG_PROBLEMS[@]}"; do
    if [[ ! -d "$SCBENCH_PROBLEMS_SRC/$p" ]]; then
      echo "run.sh: installing SCB problem catalog (slop-code sync)"
      env -u SCBENCH_PROBLEMS_PATH uv run --project "$SCB" slop-code sync
      break
    fi
  done
  for p in "${CATALOG_PROBLEMS[@]}"; do
    "${PY[@]}" scripts/make_smoke_problem.py --src-root "$SCBENCH_PROBLEMS_SRC" \
      --dest-root "$SMOKE_ROOT" --problem "$p" --checkpoints "$CATALOG_CHECKPOINTS" >/dev/null
  done
  export SCBENCH_PROBLEMS_PATH="$SMOKE_ROOT"
fi

if [[ -z "$RESUME" ]]; then
  "${PY[@]}" scripts/manifest.py "${MANIFEST_ARGS[@]}"
fi

set +e
"${CMD[@]}"
RC=$?
set -e

"${PY[@]}" scripts/manifest.py finish --run-dir "$RUN_DIR" --exit-code "$RC" || warn "manifest finish failed"

CLEAN_ARGS=("$RUN_DIR" --arm "$ARM" --expect-model "$EXPECT_MODEL")
[[ "$ARM" == cc-clean ]] && CLEAN_ARGS+=(--expect-cc-version "$CC_VERSION")
set +e
"${PY[@]}" scripts/check_clean.py "${CLEAN_ARGS[@]}"
CLEAN_RC=$?
set -e

if [[ $RC -ne 0 ]]; then
  echo "run.sh: slop-code exited $RC" >&2
  exit "$RC"
fi
case $CLEAN_RC in
  0) echo "run.sh: CLEAN — $RUN_DIR" ;;
  1) echo "run.sh: DIRTY harness — exclude this run from comparisons ($RUN_DIR/clean_check.json)" >&2; exit 3 ;;
  *) echo "run.sh: clean check UNKNOWN ($RUN_DIR/clean_check.json)" >&2; exit 4 ;;
esac
