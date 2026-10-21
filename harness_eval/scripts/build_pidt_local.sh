#!/usr/bin/env bash
# Pre-build a pi-dev-team candidate image from a local checkout, for a commit that is
# not on GitHub yet.
#
#   scripts/build_pidt_local.sh [AGENT_YAML] [PDT_CHECKOUT]
#
#   AGENT_YAML    default agents/pidt-next.yaml
#   PDT_CHECKOUT  default ../../personal/pi-dev-team (relative to this repo)
#
# SCB builds an agent image only when its tag is missing (build_image_from_str), and the
# tag comes from the agent config's version. The image built here gets that tag, so
# SCB uses it instead of cloning the pinned SHA from GitHub. The image starts from the
# arm B image (same pi, same base) and swaps /opt/pi-dev-team for a clone of the
# pinned commit, taken from a git bundle of the local checkout. It is the same tree a
# GitHub clone at that SHA gives, so the image stays valid after the commit is pushed.
# If the template has a "# --- tools:begin" ... "# --- tools:end" block (templates/pidt-tools.j2:
# ruff + lizard), that block is rendered from the template and added too, so the local
# image matches what SCB would build from the template.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_YAML="${1:-$ROOT/agents/pidt-next.yaml}"
PDT_CHECKOUT="${2:-$ROOT/../../personal/pi-dev-team}"
SCB="${SCB:-$ROOT/vendor/slop-code-bench}"
ENV_NAME="python3.12"
BASE_IMAGE="slop-code:pi-1.3.0-pdt3c34bf5-$ENV_NAME"

die() { echo "build_pidt_local: $*" >&2; exit 2; }

template="$ROOT/$(sed -n 's/^docker_template: *//p' "$AGENT_YAML")"
version="$(sed -n 's/^version: *//p' "$AGENT_YAML")"
sha="$(grep -oE 'PDT_SHA = "[0-9a-f]{40}"' "$template" | grep -oE '[0-9a-f]{40}')" || die "no 40-char PDT_SHA in $template"
[[ "$version" =~ pdt${sha:0:7}(-[a-z0-9]+)?$ ]] || die "$AGENT_YAML version '$version' does not end in pdt${sha:0:7}[-suffix]"
git -C "$PDT_CHECKOUT" cat-file -e "$sha^{commit}" 2>/dev/null || die "$PDT_CHECKOUT has no commit $sha"
docker image inspect "$BASE_IMAGE" >/dev/null 2>&1 || die "$BASE_IMAGE missing: build arm B once first (./run.sh pidt smoke builds it)"

image="slop-code:pi-$version-$ENV_NAME"
ctx="$(mktemp -d "${TMPDIR:-/tmp}/pidt-local.XXXXXX")"
trap 'rm -rf "$ctx"' EXIT

ref="refs/scb/candidate"
git -C "$PDT_CHECKOUT" update-ref "$ref" "$sha"
git -C "$PDT_CHECKOUT" bundle create -q "$ctx/pdt.bundle" "$ref"
git -C "$PDT_CHECKOUT" update-ref -d "$ref"

# Tools block from the template (empty for templates without one), rendered with
# the template's own variables.
tools_block="$(uv run --quiet --project "$SCB" python - "$template" "$version" <<'PY'
import sys
from pathlib import Path
from jinja2 import Template
text = Template(Path(sys.argv[1]).read_text()).render(base_image="unused", version=sys.argv[2])
lines = text.splitlines()
try:
    start = lines.index(next(l for l in lines if l.startswith("# --- tools:begin")))
    end = lines.index("# --- tools:end")
except (StopIteration, ValueError):
    sys.exit(0)
print("\n".join(lines[start : end + 1]))
PY
)"

cat > "$ctx/Dockerfile" <<EOF
FROM $BASE_IMAGE
USER root
$tools_block
COPY pdt.bundle /tmp/pdt.bundle
RUN rm -rf /opt/pi-dev-team \\
    && git init -q /opt/pi-dev-team \\
    && git -C /opt/pi-dev-team fetch -q /tmp/pdt.bundle $ref \\
    && git -C /opt/pi-dev-team checkout -q $sha \\
    && test "\$(git -C /opt/pi-dev-team rev-parse HEAD)" = "$sha" \\
    && git -C /opt/pi-dev-team remote add origin https://github.com/sanjit-roopra/pi-dev-team \\
    && rm /tmp/pdt.bundle \\
    && chmod -R a+rwX /opt/pi-dev-team
USER agent
EOF

docker build -q -t "$image" "$ctx" >/dev/null
echo "built $image (pi-dev-team $sha from $PDT_CHECKOUT)"
