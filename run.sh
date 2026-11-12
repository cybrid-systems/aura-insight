#!/usr/bin/env bash
# One command: pinned Aura v0.2.0 plus the Python host.
# Checksums and progress go to stderr. The only stdout from install_aura is the binary path.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# shellcheck source=scripts/aura-path.sh
source "$ROOT/scripts/aura-path.sh"

AURA_BIN="$(install_aura "$ROOT" "0.2.0")"
if [[ "$(printf '%s\n' "$AURA_BIN" | wc -l)" -ne 1 || ! -x "$AURA_BIN" ]]; then
  echo "Aura binary path is not a single executable: [$AURA_BIN]" >&2
  exit 1
fi

export AURA_BIN
export AURA_SANDBOX=off
export AURA_EVO_LOG="${AURA_EVO_LOG:-$ROOT/data/evo.jsonl}"
export INSIGHT_PORT="${INSIGHT_PORT:-8080}"
export INSIGHT_DB="${INSIGHT_DB:-$ROOT/data/insight.sqlite}"
mkdir -p "$(dirname "$AURA_EVO_LOG")" "$(dirname "$INSIGHT_DB")"

echo "Aura $("$AURA_BIN" --version)  pin cf7353373ea276bc03e8364592814382ebd62539" >&2
echo "AURA_SANDBOX=off is required so evo:trial can install a candidate policy in a child workspace" >&2
echo "open http://127.0.0.1:${INSIGHT_PORT}" >&2

exec python3 -m insight_host.app
