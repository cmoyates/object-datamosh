#!/usr/bin/env bash
set -euo pipefail

: "${BLENDER_BIN:?Set BLENDER_BIN to the Blender 5.0 executable}"

update_evidence=false
if [[ "${1:-}" == "--update-evidence" ]]; then
  update_evidence=true
  shift
fi
if (( $# != 0 )); then
  echo "Usage: $0 [--update-evidence]" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
probe="$repo_root/scripts/issue26_foreground_probe.py"
evidence_result="$repo_root/docs/evidence/issue-26-foreground-result.json"
lock_dir="${TMPDIR:-/tmp}/object-datamosh-issue26.lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
  owner_pid="$(cat "$lock_dir/pid" 2>/dev/null || true)"
  if [[ -n "$owner_pid" ]] && kill -0 "$owner_pid" 2>/dev/null; then
    echo "Another issue #26 foreground probe is running as PID $owner_pid" >&2
    exit 1
  fi
  rm -r "$lock_dir"
  mkdir "$lock_dir"
fi
echo "$$" > "$lock_dir/pid"

work_root="$(mktemp -d "${TMPDIR:-/tmp}/object-datamosh-issue26.XXXXXX")"
event_log="$work_root/events.jsonl"
run_result="$work_root/result.json"
evidence_tmp="$evidence_result.tmp.$$"
blender_pid=""

cleanup() {
  if [[ -n "$blender_pid" ]] && kill -0 "$blender_pid" 2>/dev/null; then
    kill "$blender_pid" 2>/dev/null || true
  fi
  rm -f "$evidence_tmp"
  rm -r "$lock_dir"
}
trap cleanup EXIT

if [[ -n "$(git status --porcelain --untracked-files=all -- src/object_datamosh)" ]]; then
  fail_message="Extension source is dirty; commit or restore it before recording release evidence"
  echo "$fail_message" >&2
  exit 1
fi

ODM_ISSUE26_WORK_ROOT="$work_root" \
ODM_ISSUE26_RESULT="$run_result" \
  "$BLENDER_BIN" --factory-startup --python "$probe" &
blender_pid=$!

fail_with_log() {
  local message=$1
  echo "$message" >&2
  echo "Run artifacts retained at $work_root" >&2
  if [[ -f "$event_log" ]]; then
    tail -20 "$event_log" >&2
  fi
  exit 1
}

record_escape_event() {
  local event=$1
  local marker=$2
  uv run python - "$event_log" "$event" "$marker" <<'PY'
import json
import sys
import time
from pathlib import Path

log = Path(sys.argv[1])
record = {
    "time": round(time.monotonic(), 6),
    "event": sys.argv[2],
    "marker": sys.argv[3],
}
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, sort_keys=True) + "\n")
PY
}

send_escape_after() {
  local marker=$1
  local deadline=$((SECONDS + 60))
  until [[ -f "$event_log" ]] && grep -q "\"event\": \"$marker\"" "$event_log"; do
    if ! kill -0 "$blender_pid" 2>/dev/null; then
      wait "$blender_pid" || true
      fail_with_log "Blender exited before $marker"
    fi
    if (( SECONDS >= deadline )); then
      fail_with_log "Timed out waiting for $marker"
    fi
    sleep 0.01
  done
  record_escape_event external_escape_send_started "$marker"
  osascript - "$blender_pid" <<'APPLESCRIPT'
on run argv
  set targetPid to (item 1 of argv) as integer
  tell application "System Events"
    set targetProcess to first process whose unix id is targetPid
    set frontmost of targetProcess to true
    repeat 100 times
      if frontmost of targetProcess then exit repeat
      delay 0.01
    end repeat
    if not frontmost of targetProcess then error "Launched Blender did not become frontmost"
    key code 53
  end tell
end run
APPLESCRIPT
  record_escape_event external_escape_sent "$marker"
}

send_escape_after raw_render_active
send_escape_after processing_escape_ready

completion_deadline=$((SECONDS + 120))
while kill -0 "$blender_pid" 2>/dev/null; do
  if (( SECONDS >= completion_deadline )); then
    fail_with_log "Timed out waiting for the foreground probe to finish"
  fi
  sleep 0.1
done
wait "$blender_pid"
blender_pid=""

uv run python - "$run_result" <<'PY'
import json
import sys
from pathlib import Path

result_path = Path(sys.argv[1])
payload = json.loads(result_path.read_text(encoding="utf-8"))
assert payload["success"] is True, payload
print(json.dumps(payload, indent=2, sort_keys=True))
PY

if $update_evidence; then
  cp "$run_result" "$evidence_tmp"
  mv "$evidence_tmp" "$evidence_result"
  rm -r "$work_root"
  echo "Updated $evidence_result"
else
  echo "Run artifacts retained at $work_root"
fi
