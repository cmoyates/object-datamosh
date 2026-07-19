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

if [[ "${ODM_ISSUE26_LOCK_HELD:-}" != "1" ]]; then
  lock_file="/tmp/object-datamosh-issue26-evidence-$(id -u).lock"
  if $update_evidence; then
    exec /usr/bin/lockf -k -t 0 "$lock_file" \
      env ODM_ISSUE26_LOCK_HELD=1 "$0" --update-evidence
  fi
  exec /usr/bin/lockf -k -t 0 "$lock_file" env ODM_ISSUE26_LOCK_HELD=1 "$0"
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
probe="$repo_root/scripts/issue26_foreground_probe.py"
evidence_result="$repo_root/docs/evidence/issue-26-foreground-result.json"

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
}
trap cleanup EXIT

source_scope=(src/object_datamosh scripts pyproject.toml uv.lock)
if [[ -n "$(git status --porcelain --untracked-files=all -- "${source_scope[@]}")" ]]; then
  fail_message="Extension/probe source is dirty; commit or restore it before recording release evidence"
  echo "$fail_message" >&2
  exit 1
fi
start_head="$(git rev-parse HEAD)"
start_source_tree="$(git rev-parse HEAD:src/object_datamosh)"
start_probe_sha="$(shasum -a 256 "$probe" | awk '{print $1}')"
start_runner_sha="$(shasum -a 256 "$0" | awk '{print $1}')"

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
import hashlib
import json
import sys
from pathlib import Path

result_path = Path(sys.argv[1])
payload = json.loads(result_path.read_text(encoding="utf-8"))
assert payload["success"] is True, payload
event_log = payload["event_log_jsonl"].encode("utf-8")
assert hashlib.sha256(event_log).hexdigest() == payload[
    "event_log_sha256_before_completion"
]
print(json.dumps(payload, indent=2, sort_keys=True))
PY

if [[ -n "$(git status --porcelain --untracked-files=all -- "${source_scope[@]}")" \
   || "$(git rev-parse HEAD)" != "$start_head" \
   || "$(git rev-parse HEAD:src/object_datamosh)" != "$start_source_tree" \
   || "$(shasum -a 256 "$probe" | awk '{print $1}')" != "$start_probe_sha" \
   || "$(shasum -a 256 "$0" | awk '{print $1}')" != "$start_runner_sha" ]]; then
  fail_with_log "Extension/probe identity changed during the foreground run"
fi

if $update_evidence; then
  cp "$run_result" "$evidence_tmp"
  mv "$evidence_tmp" "$evidence_result"
  rm -r "$work_root"
  echo "Updated $evidence_result"
else
  echo "Run artifacts retained at $work_root"
fi
