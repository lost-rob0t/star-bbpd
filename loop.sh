#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# ------------------------------------------------------------------------------
# Ralph Loop (advanced) - generic bash loop for Claude Code / other agent CLIs
#
# Minimal setup:
#   - Create PRD.md (your plan / checklist / requirements)
#   - Create progress.txt (append-only log, can start empty)
#   - Run: ./loop.sh 25
#
# Defaults assume Claude Code, print mode (-p), acceptEdits.
# You can override everything via env vars.
# ------------------------------------------------------------------------------

die() { printf "fatal: %s\n" "$*" >&2; exit 1; }
log() { printf "[%s] %s\n" "$(date +'%Y-%m-%d %H:%M:%S')" "$*" >&2; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

hash_file() {
  local f="$1"
  [[ -f "$f" ]] || { printf "missing"; return 0; }
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$f" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$f" | awk '{print $1}'
  elif command -v python3 >/dev/null 2>&1; then
    python3 - <<PY
import hashlib,sys
p=sys.argv[1]
h=hashlib.sha256()
with open(p,'rb') as f:
  for b in iter(lambda: f.read(1024*1024), b''):
    h.update(b)
print(h.hexdigest())
PY
  else
    die "need sha256sum, shasum, or python3 for hashing"
  fi
}

is_git_repo() { git rev-parse --is-inside-work-tree >/dev/null 2>&1; }

git_head() {
  if is_git_repo; then
    git rev-parse HEAD 2>/dev/null || true
  else
    printf ""
  fi
}

git_clean_guard() {
  local allow_dirty="$1"
  is_git_repo || return 0
  if [[ "$allow_dirty" == "1" ]]; then
    return 0
  fi
  local st
  st="$(git status --porcelain || true)"
  [[ -z "$st" ]] || die "git tree not clean. commit/stash first, or set RLOOP_ALLOW_DIRTY=1"
}

json_escape() {
  # minimal JSON string escaper (enough for logs)
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf "%s" "$s"
}

usage() {
  cat >&2 <<'USAGE'
Usage:
  ./loop.sh <iterations>
  ./loop.sh <iterations> <sleep_sec>

Env knobs (sane defaults):
  RLOOP_PRD_FILE=PRD.md
  RLOOP_PROGRESS_FILE=progress.txt
  RLOOP_EXTRA_CONTEXT="AGENTS.md prd.json"     # optional files included if present
  RLOOP_TOOL_KIND=claude                       # claude | generic
  RLOOP_TOOL_CMD="claude"                      # or: "docker sandbox run claude"
  RLOOP_CLAUDE_ARGS="--permission-mode acceptEdits"
  RLOOP_PROMPT_MODE=claude_print               # claude_print | stdin | args
  RLOOP_COMPLETE_SIGIL="<promise>COMPLETE</promise>"
  RLOOP_MAX_FAILS=3
  RLOOP_STALL_LIMIT=3
  RLOOP_ALLOW_DIRTY=0
  RLOOP_RUN_DIR=".ralph"
  RLOOP_POST_HOOK=""                           # optional shell command run after a successful iteration
  RLOOP_DEBUG=0
USAGE
}

main() {
  local iterations="${1:-}"
  local sleep_sec="${2:-0}"

  [[ -n "$iterations" ]] || { usage; exit 2; }
  [[ "$iterations" =~ ^[0-9]+$ ]] || die "iterations must be an integer"
  [[ "$sleep_sec" =~ ^[0-9]+$ ]] || die "sleep_sec must be an integer"

  local prd_file="${RLOOP_PRD_FILE:-PRD.md}"
  local progress_file="${RLOOP_PROGRESS_FILE:-progress.txt}"
  local extra_context="${RLOOP_EXTRA_CONTEXT:-AGENTS.md prd.json}"
  local tool_kind="${RLOOP_TOOL_KIND:-claude}"
  local tool_cmd="${RLOOP_TOOL_CMD:-claude}"
  local claude_args="${RLOOP_CLAUDE_ARGS:---permission-mode acceptEdits}"
  local prompt_mode="${RLOOP_PROMPT_MODE:-claude_print}"
  local complete_sigil="${RLOOP_COMPLETE_SIGIL:-<promise>COMPLETE</promise>}"
  local max_fails="${RLOOP_MAX_FAILS:-3}"
  local stall_limit="${RLOOP_STALL_LIMIT:-3}"
  local allow_dirty="${RLOOP_ALLOW_DIRTY:-0}"
  local run_dir="${RLOOP_RUN_DIR:-.ralph}"
  local post_hook="${RLOOP_POST_HOOK:-}"
  local debug="${RLOOP_DEBUG:-0}"

  [[ -f "$prd_file" ]] || die "missing $prd_file (create it)"
  [[ -f "$progress_file" ]] || touch "$progress_file"

  need_cmd bash
  [[ "$tool_kind" == "claude" ]] && need_cmd "${tool_cmd%% *}" || true
  [[ "$tool_kind" != "claude" ]] && need_cmd "${tool_cmd%% *}" || true

  if is_git_repo; then
    need_cmd git
    git_clean_guard "$allow_dirty"
  fi

  mkdir -p "$run_dir"

  local run_id
  run_id="$(date +'%Y%m%d-%H%M%S')"
  local out_root="$run_dir/runs/$run_id"
  mkdir -p "$out_root"

  local jsonl="$out_root/run.log.jsonl"
  local state="$out_root/state.env"
  : >"$jsonl"
  : >"$state"

  local fail_count=0
  local stall_count=0

  trap 'log "caught signal; state at: '"$state"'"; exit 130' INT TERM

  log "run_id=$run_id iterations=$iterations sleep_sec=$sleep_sec"
  log "tool_kind=$tool_kind tool_cmd=$tool_cmd prompt_mode=$prompt_mode"
  log "prd=$prd_file progress=$progress_file out=$out_root"

  for ((i=1; i<=iterations; i++)); do
    local iter
    iter="$(printf "%04d" "$i")"
    local iter_dir="$out_root/iter-$iter"
    mkdir -p "$iter_dir"

    local ts_start ts_end
    ts_start="$(date +%s)"

    local before_head before_prd before_prog
    before_head="$(git_head)"
    before_prd="$(hash_file "$prd_file")"
    before_prog="$(hash_file "$progress_file")"

    local prompt
    prompt="$(
      {
        printf "@%s @%s " "$prd_file" "$progress_file"
        for f in $extra_context; do
          [[ -f "$f" ]] && printf "@%s " "$f"
        done
        cat <<'TXT'
You are running in a Ralph loop.

1) Read the PRD and progress.
2) Pick ONE highest-priority incomplete task.
3) Implement it end-to-end.
4) Run relevant checks/tests for this repo.
5) Commit changes with a clear message.
6) Update progress.txt with what you did (append; keep it short).
If everything in the PRD is done, output exactly: <promise>COMPLETE</promise>
ONLY DO ONE TASK.
TXT
      } | tr -s ' '
    )"

    # Save prompt used for this iteration (so you can audit later)
    printf "%s\n" "$prompt" >"$iter_dir/prompt.txt"

    local out_file="$iter_dir/output.txt"
    local rc=0

    log "iter=$iter start"

    set +e
    if [[ "$tool_kind" == "claude" ]]; then
      case "$prompt_mode" in
        claude_print)
          # Claude Code print mode (-p) is designed for non-interactive loops. :contentReference[oaicite:1]{index=1}
          eval "$tool_cmd" $claude_args -p "$(printf "%q" "$prompt")" >"$out_file" 2>&1
          rc=$?
          ;;
        stdin)
          printf "%s\n" "$prompt" | eval "$tool_cmd" $claude_args >"$out_file" 2>&1
          rc=$?
          ;;
        args)
          eval "$tool_cmd" $claude_args "$(printf "%q" "$prompt")" >"$out_file" 2>&1
          rc=$?
          ;;
        *)
          rc=2
          ;;
      esac
    else
      # Generic mode: pipe prompt via stdin by default.
      case "$prompt_mode" in
        stdin)
          printf "%s\n" "$prompt" | eval "$tool_cmd" >"$out_file" 2>&1
          rc=$?
          ;;
        args)
          eval "$tool_cmd" "$(printf "%q" "$prompt")" >"$out_file" 2>&1
          rc=$?
          ;;
        *)
          printf "%s\n" "$prompt" | eval "$tool_cmd" >"$out_file" 2>&1
          rc=$?
          ;;
      esac
    fi
    set -e

    ts_end="$(date +%s)"

    local after_head after_prd after_prog
    after_head="$(git_head)"
    after_prd="$(hash_file "$prd_file")"
    after_prog="$(hash_file "$progress_file")"

    local completed="0"
    if grep -Fq "$complete_sigil" "$out_file"; then
      completed="1"
    fi

    local changed="0"
    if [[ -n "$before_head" && -n "$after_head" && "$before_head" != "$after_head" ]]; then
      changed="1"
    elif [[ "$before_prd" != "$after_prd" || "$before_prog" != "$after_prog" ]]; then
      changed="1"
    fi

    if [[ "$rc" -ne 0 ]]; then
      fail_count=$((fail_count + 1))
      log "iter=$iter rc=$rc (fail_count=$fail_count/$max_fails)"
      if [[ "$fail_count" -gt "$max_fails" ]]; then
        die "too many failures; see logs: $out_root"
      fi
      # backoff: 2,4,8,... (cap at 60)
      local backoff=$(( 2 ** (fail_count - 1) ))
      [[ "$backoff" -gt 60 ]] && backoff=60
      log "sleeping backoff=${backoff}s"
      sleep "$backoff"
      continue
    fi

    # reset fail counter on success
    fail_count=0

    if [[ "$changed" == "0" && "$completed" == "0" ]]; then
      stall_count=$((stall_count + 1))
      log "iter=$iter no detectable progress (stall_count=$stall_count/$stall_limit)"
      if [[ "$stall_count" -ge "$stall_limit" ]]; then
        die "stalled for $stall_limit iterations; see logs: $out_root"
      fi
    else
      stall_count=0
    fi

    # Optional post-hook (runs in your shell; keep it safe)
    if [[ -n "$post_hook" ]]; then
      ( export RLOOP_ITER="$iter" RLOOP_OUT_DIR="$iter_dir" RLOOP_RUN_DIR="$out_root"; bash -lc "$post_hook" ) \
        >"$iter_dir/post_hook.txt" 2>&1 || true
    fi

    # Write state + JSONL record
    {
      printf "RLOOP_RUN_ID=%q\n" "$run_id"
      printf "RLOOP_ITER=%q\n" "$iter"
      printf "RLOOP_LAST_RC=%q\n" "$rc"
      printf "RLOOP_LAST_COMPLETED=%q\n" "$completed"
      printf "RLOOP_LAST_OUT_FILE=%q\n" "$out_file"
      printf "RLOOP_LAST_BEFORE_HEAD=%q\n" "$before_head"
      printf "RLOOP_LAST_AFTER_HEAD=%q\n" "$after_head"
    } >"$state"

    printf '{"iter":%d,"ts_start":%s,"ts_end":%s,"rc":%d,"completed":%s,"changed":%s,"before_head":"%s","after_head":"%s","prd_hash_before":"%s","prd_hash_after":"%s","progress_hash_before":"%s","progress_hash_after":"%s","out_file":"%s"}\n' \
      "$i" \
      "$(json_escape "$ts_start")" \
      "$(json_escape "$ts_end")" \
      "$rc" \
      "$completed" \
      "$changed" \
      "$(json_escape "$before_head")" \
      "$(json_escape "$after_head")" \
      "$(json_escape "$before_prd")" \
      "$(json_escape "$after_prd")" \
      "$(json_escape "$before_prog")" \
      "$(json_escape "$after_prog")" \
      "$(json_escape "$out_file")" \
      >>"$jsonl"

    if [[ "$debug" == "1" ]]; then
      log "iter=$iter output (tail):"
      tail -n 25 "$out_file" >&2 || true
    fi

    if [[ "$completed" == "1" ]]; then
      log "complete at iter=$iter"
      exit 0
    fi

    [[ "$sleep_sec" -gt 0 ]] && sleep "$sleep_sec"
  done

  log "max iterations hit; see logs: $out_root"
  exit 1
}

main "$@"
