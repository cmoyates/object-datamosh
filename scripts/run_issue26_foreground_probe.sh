#!/usr/bin/env bash
set -euo pipefail

: "${BLENDER_BIN:?Set BLENDER_BIN to the Blender 5.0 executable}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
probe="$repo_root/scripts/issue26_foreground_probe.py"
event_log="${TMPDIR:-/tmp}/object-datamosh-issue26/events.jsonl"
result="$repo_root/docs/evidence/issue-26-foreground-result.json"

rm -f "$event_log" "$result"
"$BLENDER_BIN" --factory-startup --python "$probe" &
blender_pid=$!
trap 'kill "$blender_pid" 2>/dev/null || true' EXIT

send_escape_after() {
  local marker=$1
  local deadline=$((SECONDS + 60))
  until [[ -f "$event_log" ]] && grep -q "\"event\": \"$marker\"" "$event_log"; do
    if ! kill -0 "$blender_pid" 2>/dev/null; then
      wait "$blender_pid"
      echo "Blender exited before $marker" >&2
      exit 1
    fi
    if (( SECONDS >= deadline )); then
      echo "Timed out waiting for $marker" >&2
      exit 1
    fi
    sleep 0.01
  done
  osascript \
    -e 'tell application "Blender" to activate' \
    -e 'tell application "System Events" to key code 53'
}

send_escape_after escape_ready
send_escape_after processing_escape_ready
wait "$blender_pid"
trap - EXIT

uv run python - "$result" <<'PY'
import json
import sys
from pathlib import Path

result_path = Path(sys.argv[1])
payload = json.loads(result_path.read_text(encoding="utf-8"))
assert payload["success"] is True, payload
print(json.dumps(payload, indent=2, sort_keys=True))
PY
