#!/usr/bin/env bash
# Point arms pidt-full, pidt-lean and pidt-lean-fast at one pi-dev-team commit, in one step.
#
#   scripts/set_pdt_sha.sh SHA [PDT_CHECKOUT]
#
#   SHA           full or short SHA (resolved to 40 chars in PDT_CHECKOUT)
#   PDT_CHECKOUT  default ../../../personal/pi-dev-team (relative to harness_eval/)
#
# Writes PDT_SHA in templates/pidt-tools.j2, "version: 1.3.0-pdt<sha7>-tools" in
# agents/pidt-full.yaml, "version: 1.3.0-pdt<sha7>-lean" in agents/pidt-lean.yaml and
# "version: 1.3.0-pdt<sha7>-fast" in agents/pidt-lean-fast.yaml.
# version is SCB's image cache key. The arms get separate image tags so that
# scripts/cost_guard.sh (which stops containers by image) can guard each arm on its
# own while they run at once. Build the full image, then the lean images FROM it:
#   scripts/build_pidt_local.sh agents/pidt-full.yaml
#   printf 'FROM slop-code:pi-1.3.0-pdt<sha7>-tools-python3.12\nLABEL scb.arm=pidt-lean\n' \
#     | docker build -t slop-code:pi-1.3.0-pdt<sha7>-lean-python3.12 -
#   (same for -fast with LABEL scb.arm=pidt-lean-fast)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHA_IN="${1:?usage: scripts/set_pdt_sha.sh SHA [PDT_CHECKOUT]}"
PDT_CHECKOUT="${2:-$ROOT/../../../personal/pi-dev-team}"
TEMPLATE="$ROOT/templates/pidt-tools.j2"
AGENTS=("$ROOT/agents/pidt-full.yaml" "$ROOT/agents/pidt-lean.yaml" "$ROOT/agents/pidt-lean-fast.yaml")

die() { echo "set_pdt_sha: $*" >&2; exit 2; }

sha="$(git -C "$PDT_CHECKOUT" rev-parse --verify -q "$SHA_IN^{commit}")" || die "$PDT_CHECKOUT has no commit $SHA_IN"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "could not resolve $SHA_IN to a full SHA"

grep -qE '^\{%- set PDT_SHA = "[^"]*" -%\}$' "$TEMPLATE" || die "no PDT_SHA line in $TEMPLATE"
sed -i.bak -E "s/^\{%- set PDT_SHA = \"[^\"]*\" -%\}$/{%- set PDT_SHA = \"$sha\" -%}/" "$TEMPLATE"
rm -f "$TEMPLATE.bak"
for a in "${AGENTS[@]}"; do
  grep -qE '^version: ' "$a" || die "no version line in $a"
  suffix=tools
  [[ "$a" == *pidt-lean.yaml ]] && suffix=lean
  [[ "$a" == *pidt-lean-fast.yaml ]] && suffix=fast
  sed -i.bak -E "s/^version: .*/version: 1.3.0-pdt${sha:0:7}-$suffix/" "$a"
  rm -f "$a.bak"
done
echo "pidt-full/pidt-lean/pidt-lean-fast -> pi-dev-team $sha (versions 1.3.0-pdt${sha:0:7}-tools / -lean / -fast)"
