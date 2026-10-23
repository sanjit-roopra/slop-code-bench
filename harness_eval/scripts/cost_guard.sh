#!/usr/bin/env bash
# Stops a running pi-dev-team arm when it gets too expensive or too long.
#
#   RUN=results/<run>/<problem> ARM_YAML=pidt-opus.yaml IMAGE=slop-code:pi-1.3.0-pdtfeb5afd-python3.12 \
#     MAX_COST=45 MAX_SECS=10800 scripts/cost_guard.sh
#
# Start it after the agent container is up: it exits as soon as no slop-code run
# for ARM_YAML is alive. SCB writes usage only when a checkpoint ends, so the cost
# check sees finished checkpoints only; the wall-clock cap is the real limit
# inside a checkpoint (observed: $8-16 per hour for pi-dev-team).
# RUN may also be the run dir itself: the cost then sums every problem in it.
# pidt-full and pidt-lean share one IMAGE: a STOP stops containers of both, so run
# those two arms one after the other, not side by side, while a guard is active.
RUN=${RUN:?}
ARM_YAML=${ARM_YAML:?}
IMAGE=${IMAGE:?}
MAX_COST=${MAX_COST:-40}
MAX_SECS=${MAX_SECS:-14400}
start=$(date +%s)
stop_run() {
  echo "$(date -u +%FT%TZ) STOP: $1"
  for c in $(docker ps -q --filter ancestor="$IMAGE"); do docker stop -t 5 "$c" >/dev/null; done
  pkill -f "slop-code run --agent .*${ARM_YAML}" || true
  exit 0
}
while pgrep -f "slop-code run --agent .*${ARM_YAML}" >/dev/null; do
  cost=$(python3 - "$RUN" <<'PY'
import glob, json, sys
total = 0.0
# RUN may be one problem dir or a whole run dir (several problems, e.g. heldout).
files = glob.glob(sys.argv[1] + "/checkpoint_*/inference_result.json") + glob.glob(
    sys.argv[1] + "/*/checkpoint_*/inference_result.json"
)
for f in files:
    try:
        total += json.load(open(f))["usage"]["cost"]
    except Exception:
        pass
print(round(total, 3))
PY
)
  el=$(( $(date +%s) - start ))
  echo "$(date -u +%FT%TZ) elapsed=${el}s finished_ckpt_cost=\$${cost}"
  python3 -c "import sys; sys.exit(0 if float('$cost') < $MAX_COST else 1)" || stop_run "cost \$${cost} >= \$${MAX_COST}"
  (( el < MAX_SECS )) || stop_run "elapsed ${el}s >= ${MAX_SECS}s"
  sleep 60
done
echo "$(date -u +%FT%TZ) run ended on its own"
