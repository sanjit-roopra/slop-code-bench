#!/usr/bin/env bash
# Run one arm of the harness eval on a SlopCodeBench subset.
#
#   ./run.sh [options] ARM SUBSET [REPEAT]
#
#   ARM     cc-clean | pidt | pidt-opus | pi-stock
#   SUBSET  smoke | lite_under20 | lite | full
#   REPEAT  repeat index, default 1 (output dir suffix -r<REPEAT>)
#
# Options:
#   --dry-run        resolve and print everything (incl. the slop-code command); run nothing
#   --workers N      parallel problems (slop-code --num-workers), default 1
#   --no-evaluate    skip SCB evaluation (agent only)
#   --resume DIR     resume an existing run dir (slop-code --resume); ARM/SUBSET still required
#   -h | --help      this help
#
# Env overrides: CC_MODEL, PIDT_MODEL, PIDT_OPUS_MODEL, PI_STOCK_MODEL,
#   SMOKE_PROBLEM (default file_backup), SMOKE_CHECKPOINTS (default 2),
#   SCBENCH_PROBLEMS_SRC (installed catalog, default ~/.cache/scbench/problems),
#   CC_TOKEN_FILE (default ~/.config/scb/cc_oauth_token), PI_AUTH_FILE (default ~/.pi/agent/auth.json).
#
# Output: results/<YYYYMMDD>-<arm>-<subset>-r<n>/ with manifest.json (written
# before the run) and clean_check.json (after). Secrets are never printed.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCB="$ROOT/vendor/slop-code-bench"
cd "$ROOT"

# SCB puts the agent HOME under tempfile.gettempdir(); macOS' default
# /var/folders/... TMPDIR is not mountable the way SCB expects.
export TMPDIR=/tmp

CC_VERSION="2.1.304"
CC_TOKEN_FILE="${CC_TOKEN_FILE:-$HOME/.config/scb/cc_oauth_token}"
PI_AUTH_FILE="${PI_AUTH_FILE:-$HOME/.pi/agent/auth.json}"
SMOKE_PROBLEM="${SMOKE_PROBLEM:-file_backup}"
SMOKE_CHECKPOINTS="${SMOKE_CHECKPOINTS:-2}"
SCBENCH_PROBLEMS_SRC="${SCBENCH_PROBLEMS_SRC:-$HOME/.cache/scbench/problems}"
THINKING="high"

die() { echo "run.sh: $*" >&2; exit 2; }
warn() { echo "run.sh: WARNING: $*" >&2; }
usage() { sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

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
  pidt-opus)
    # Same agent + image as pidt; only the model differs (isolates the harness vs arm A).
    AGENT_YAML="$ROOT/agents/pidt-opus.yaml"
    [[ -f "$AGENT_YAML" ]] || AGENT_YAML="$ROOT/agents/pidt.yaml"
    MODEL="${PIDT_OPUS_MODEL:-github-copilot/copilot-claude-opus-5.5}"
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
  *) die "unknown ARM '$ARM' (cc-clean|pidt|pidt-opus|pi-stock)" ;;
esac

# --- Subset -> problem selection ---------------------------------------------
RUN_CONFIG=""
PROBLEM_ARGS=()
SMOKE_ROOT=""
case "$SUBSET" in
  smoke)
    # slop-code has no checkpoint-limit flag: build a one-problem catalog with
    # only the first $SMOKE_CHECKPOINTS checkpoints and point SCBENCH_PROBLEMS_PATH at it.
    SMOKE_ROOT="$ROOT/results/.smoke-catalog/${SMOKE_PROBLEM}-c${SMOKE_CHECKPOINTS}"
    PROBLEM_ARGS=(--problem "$SMOKE_PROBLEM")
    ;;
  lite_under20|lite)
    RUN_CONFIG="$ROOT/runs/${ARM}_${SUBSET}.yaml"
    [[ -f "$RUN_CONFIG" ]] || RUN_CONFIG="$SCB/configs/runs/${SUBSET}.yaml"
    ;;
  full) ;;  # all problems in the installed catalog
  *) die "unknown SUBSET '$SUBSET' (smoke|lite_under20|lite|full)" ;;
esac

# --- Output dir ---------------------------------------------------------------
if [[ -n "$RESUME" ]]; then
  RUN_DIR="$(cd "$RESUME" 2>/dev/null && pwd)" || die "--resume dir not found: $RESUME"
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
ENV_PREFIX="TMPDIR=/tmp"
[[ -n "$SMOKE_ROOT" ]] && ENV_PREFIX+=" SCBENCH_PROBLEMS_PATH=$SMOKE_ROOT"

PDT_SHA="$(grep -oE 'PDT_SHA = "[0-9a-f]+"' "$ROOT/templates/pidt.j2" 2>/dev/null | grep -oE '[0-9a-f]{7,}' || true)"

echo "arm:         $ARM"
echo "subset:      $SUBSET (repeat $REPEAT)"
echo "agent:       $AGENT_YAML"
echo "model:       $MODEL  thinking=$THINKING"
echo "prompt:      $PROMPT"
echo "environment: $ENVIRONMENT"
[[ -n "$RUN_CONFIG" ]] && echo "run config:  $RUN_CONFIG"
[[ -n "$SMOKE_ROOT" ]] && echo "smoke:       $SMOKE_PROBLEM, first $SMOKE_CHECKPOINTS checkpoints -> $SMOKE_ROOT"
[[ "$ARM" == pidt* ]] && echo "pi-dev-team: ${PDT_SHA:-unknown}"
echo "credentials: $cred_status"
echo "run dir:     $RUN_DIR"
echo "command:     $ENV_PREFIX $CMD_STR"

MANIFEST_ARGS=(write --run-dir "$RUN_DIR" --arm "$ARM" --subset "$SUBSET" --repeat "$REPEAT"
  --agent "$AGENT_YAML" --model "$MODEL" --prompt "$PROMPT" --environment "$ENVIRONMENT"
  --thinking "$THINKING" --command "$ENV_PREFIX $CMD_STR")
[[ -n "$RUN_CONFIG" ]] && MANIFEST_ARGS+=(--run-config "$RUN_CONFIG")
[[ "$SUBSET" == smoke ]] && MANIFEST_ARGS+=(--problem "$SMOKE_PROBLEM" --smoke-checkpoints "$SMOKE_CHECKPOINTS")
PY=(uv run --quiet --project "$SCB" python)

if [[ $DRY_RUN -eq 1 ]]; then
  if [[ -z "$RESUME" ]]; then
    # Resolve through SCB's own loaders (config, env, model catalog, agent
    # schema, image tag). No credentials, no docker, no model calls.
    echo "resolved (SCB loaders):"
    if "${PY[@]}" scripts/resolve_config.py "${CMD[@]:6}"; then
      echo "dry run: OK, nothing executed"
    else
      echo "dry run: SCB could not resolve this configuration" >&2
      exit 1
    fi
  fi
  exit 0
fi

# --- Run ------------------------------------------------------------------------
if [[ -n "$SMOKE_ROOT" ]]; then
  if [[ ! -d "$SCBENCH_PROBLEMS_SRC/$SMOKE_PROBLEM" ]]; then
    echo "run.sh: installing SCB problem catalog (slop-code sync)"
    env -u SCBENCH_PROBLEMS_PATH uv run --project "$SCB" slop-code sync
  fi
  "${PY[@]}" scripts/make_smoke_problem.py --src-root "$SCBENCH_PROBLEMS_SRC" \
    --dest-root "$SMOKE_ROOT" --problem "$SMOKE_PROBLEM" --checkpoints "$SMOKE_CHECKPOINTS" >/dev/null
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

CLEAN_ARGS=("$RUN_DIR" --arm "$ARM")
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
