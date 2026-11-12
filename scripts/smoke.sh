#!/usr/bin/env bash
# Start the host on fixtures and check health plus the insight list.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/aura-path.sh
source "$ROOT/scripts/aura-path.sh"
AURA_BIN="$(install_aura "$ROOT" "0.2.0")"
export AURA_BIN
export AURA_SANDBOX=off

PORT="${INSIGHT_PORT:-8091}"
TMP="$(mktemp -d)"
export INSIGHT_PORT="$PORT"
export INSIGHT_DB="$TMP/insight.sqlite"
export AURA_EVO_LOG="$TMP/evo.jsonl"
export INSIGHT_HOST="127.0.0.1"

python3 -m insight_host.app >"$TMP/host.log" 2>&1 &
PID=$!
cleanup() {
  kill "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

for _ in $(seq 1 80); do
  if curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null; then
    break
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "host exited" >&2
    cat "$TMP/host.log" >&2
    exit 1
  fi
  sleep 0.2
done

python3 - "$PORT" << 'PY'
import json, sys, urllib.request
port = sys.argv[1]
base = f"http://127.0.0.1:{port}"

def get(path):
    with urllib.request.urlopen(base + path, timeout=10) as resp:
        return json.loads(resp.read().decode())

health = get("/api/health")
assert health["ok"] is True, health
assert health["aura"] == "0.2.0"
assert health["sandbox"] == "off"
insights = get("/api/insights")
assert insights["ok"] is True
assert insights["policy_name"] == "seed"
assert len(insights["insights"]) >= 4, insights
page = urllib.request.urlopen(base + "/", timeout=10).read().decode()
assert "需求洞察" in page
print(f"smoke ok  insights={len(insights['insights'])} policy={insights['policy_name']} v{insights['policy_version']}")
PY
