#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# ------------------------------------------------------------------------------
# Ralph Loop (advanced) — NO auto-commit, WITH human messages
#
# Files:
#   - PRD.md         : your plan / requirements / checklist
#   - progress.txt   : append-only log (agent updates it)
# Optional:
#   - HUMAN.md       : persistent human notes (always included if present)
#
# Run:
#   chmod +x loop.sh
#   touch progress.txt
#   ./loop.sh 25
#
# Human note:
#   Each iteration, you can type a message. End with a single line: /send
#   Empty message is allowed (just type /send immediately).
# ------------------------------------------------------------------------------

die() {
    printf "fatal: %s\n" "$*" >&2
    exit 1
}
log() { printf "%s\n" "$*" >&2; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }
is_tty() { [[ -t 0 && -t 1 ]]; }

is_git_repo() { git rev-parse --is-inside-work-tree >/dev/null 2>&1; }

git_head() {
    is_git_repo || {
        printf ""
        return 0
    }
    git rev-parse HEAD 2>/dev/null || true
}

git_status_porcelain() {
    is_git_repo || {
        printf ""
        return 0
    }
    git status --porcelain 2>/dev/null || true
}

hash_file() {
    local f="$1"
    [[ -f "$f" ]] || {
        printf "missing"
        return 0
    }
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

json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\t'/\\t}"
    printf "%s" "$s"
}

read_human_note() {
    # Multiline human note: ends when user types a line exactly "/send".
    # Returns note via stdout (may be empty).
    local line
    local buf=""
    while IFS= read -r line; do
        [[ "$line" == "/send" ]] && break
        buf+="$line"$'\n'
    done
    printf "%s" "$buf"
}

usage() {
    cat >&2 <<'USAGE'
Usage:
  ./loop.sh <iterations>
  ./loop.sh <iterations> <sleep_sec>

Env knobs:
  RLOOP_PRD_FILE=PRD.md
  RLOOP_PROGRESS_FILE=progress.txt
  RLOOP_HUMAN_FILE=HUMAN.md                 # optional; included if present
  RLOOP_EXTRA_CONTEXT="AGENTS.md prd.json"  # optional extra files

  RLOOP_TOOL_KIND=claude
  RLOOP_TOOL_CMD="claude"
  RLOOP_CLAUDE_ARGS="--permission-mode acceptEdits"
  RLOOP_PROMPT_MODE=claude_print            # claude_print | stdin | args

  RLOOP_COMPLETE_SIGIL="<promise>COMPLETE</promise>"

  RLOOP_MAX_FAILS=3
  RLOOP_STALL_LIMIT=3
  RLOOP_ALLOW_DIRTY=1                       # default 1 because we are NOT auto-committing

  RLOOP_HUMAN_PROMPT=1                      # prompt every iter if TTY
  RLOOP_DEBUG=0
  RLOOP_RUN_DIR=".ralph"
USAGE
}

main() {
    local iterations="${1:-}"
    local sleep_sec="${2:-0}"

    [[ -n "$iterations" ]] || {
        usage
        exit 2
    }
    [[ "$iterations" =~ ^[0-9]+$ ]] || die "iterations must be an integer"
    [[ "$sleep_sec" =~ ^[0-9]+$ ]] || die "sleep_sec must be an integer"

    local prd_file="${RLOOP_PRD_FILE:-PRD.md}"
    local progress_file="${RLOOP_PROGRESS_FILE:-progress.txt}"
    local human_file="${RLOOP_HUMAN_FILE:-HUMAN.md}"
    local extra_context="${RLOOP_EXTRA_CONTEXT:-AGENTS.md prd.json}"

    local tool_kind="${RLOOP_TOOL_KIND:-claude}"
    local tool_cmd="${RLOOP_TOOL_CMD:-claude}"
    local claude_args="${RLOOP_CLAUDE_ARGS:---permission-mode acceptEdits}"
    local prompt_mode="${RLOOP_PROMPT_MODE:-claude_print}"

    local complete_sigil="${RLOOP_COMPLETE_SIGIL:-<promise>COMPLETE</promise>}"

    local max_fails="${RLOOP_MAX_FAILS:-3}"
    local stall_limit="${RLOOP_STALL_LIMIT:-3}"
    local allow_dirty="${RLOOP_ALLOW_DIRTY:-1}"

    local human_prompt="${RLOOP_HUMAN_PROMPT:-1}"
    local debug="${RLOOP_DEBUG:-0}"
    local run_dir="${RLOOP_RUN_DIR:-.ralph}"

    [[ -f "$prd_file" ]] || die "missing $prd_file (create it)"
    [[ -f "$progress_file" ]] || touch "$progress_file"

    need_cmd bash

    # tool command exists
    need_cmd "${tool_cmd%% *}"

    if is_git_repo; then
        need_cmd git
        if [[ "$allow_dirty" != "1" ]]; then
            local st
            st="$(git_status_porcelain)"
            [[ -z "$st" ]] || die "git tree not clean. commit/stash first, or set RLOOP_ALLOW_DIRTY=1"
        fi
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

    trap 'log "🧯 stopped. logs: '"$out_root"'"; exit 130' INT TERM

    log "🧠 ralph loop start  run_id=$run_id  iterations=$iterations  sleep=${sleep_sec}s"
    log "🧰 tool=$tool_kind cmd=$tool_cmd mode=$prompt_mode"
    log "📄 prd=$prd_file  progress=$progress_file  human_file=$human_file"
    if is_git_repo; then
        log "🧾 git_repo=1 allow_dirty=$allow_dirty"
    else
        log "🧾 git_repo=0"
    fi
    log "📦 output_dir=$out_root"
    log ""

    for ((i = 1; i <= iterations; i++)); do
        local iter
        iter="$(printf "%04d" "$i")"
        local iter_dir="$out_root/iter-$iter"
        mkdir -p "$iter_dir"

        local ts_start ts_end
        ts_start="$(date +%s)"

        local before_head before_prd before_prog before_status
        before_head="$(git_head)"
        before_prd="$(hash_file "$prd_file")"
        before_prog="$(hash_file "$progress_file")"
        before_status="$(git_status_porcelain)"

        # --- Human note (interactive)
        local human_note=""
        if [[ "$human_prompt" == "1" ]] && is_tty; then
            log "🧍 iter=$iter/$iterations"
            if is_git_repo; then
                if [[ -n "$before_status" ]]; then
                    log "   repo: DIRTY (uncommitted changes exist)"
                else
                    log "   repo: clean"
                fi
            fi
            log "   drop a human note for the agent (optional). end with: /send"
            human_note="$(read_human_note || true)"
            printf "%s" "$human_note" >"$iter_dir/human_note.txt"
            log "   noted. running agent…"
            log ""
        else
            : >"$iter_dir/human_note.txt"
        fi

        # --- Include persistent HUMAN.md if present
        local human_file_note=""
        if [[ -f "$human_file" ]]; then
            human_file_note="$(cat "$human_file" 2>/dev/null || true)"
        fi

        # --- Build prompt
        local prompt
        prompt="$(
            {
                # File includes (Claude Code supports @file; harmless for others as literal text)
                printf "@%s @%s " "$prd_file" "$progress_file"
                [[ -f "$human_file" ]] && printf "@%s " "$human_file"
                for f in $extra_context; do
                    [[ -f "$f" ]] && printf "@%s " "$f"
                done
                printf "\n\n"

                cat <<'TXT'
You are running in a Ralph loop.

Rules:
- Do ONE task per iteration.
- Read PRD and progress.txt first.
- Prefer the highest-priority incomplete item.
- Make real changes in the repo (code/tests/docs), not just talk.
- Run relevant checks/tests for this repo.
- Update progress.txt (append; short).
- If everything in PRD is done, output exactly: <promise>COMPLETE</promise>

Do NOT invent requirements. If something blocks you, write the blocker clearly and make the smallest safe progress.
TXT
                printf "\n"

                if [[ -n "$human_file_note" ]]; then
                    printf "Persistent Human Context (HUMAN.md):\n%s\n" "$human_file_note"
                    printf "\n"
                fi

                if [[ -n "$human_note" ]]; then
                    printf "Human Note For This Iteration (highest priority):\n%s\n" "$human_note"
                    printf "\n"
                fi
            } | sed 's/[[:space:]]\+$//' # trim trailing whitespace lines
        )"

        printf "%s\n" "$prompt" >"$iter_dir/prompt.txt"

        # --- Run tool
        local out_file="$iter_dir/output.txt"
        local rc=0

        log "🤖 iter=$iter launching tool…"

        set +e
        if [[ "$tool_kind" == "claude" ]]; then
            case "$prompt_mode" in
            claude_print)
                bash -lc "$tool_cmd $claude_args -p $(printf "%q" "$prompt")" >"$out_file" 2>&1
                rc=$?
                ;;
            stdin)
                printf "%s\n" "$prompt" | bash -lc "$tool_cmd $claude_args" >"$out_file" 2>&1
                rc=$?
                ;;
            args)
                bash -lc "$tool_cmd $claude_args $(printf "%q" "$prompt")" >"$out_file" 2>&1
                rc=$?
                ;;
            *)
                rc=2
                ;;
            esac
        else
            case "$prompt_mode" in
            stdin)
                printf "%s\n" "$prompt" | bash -lc "$tool_cmd" >"$out_file" 2>&1
                rc=$?
                ;;
            args)
                bash -lc "$tool_cmd $(printf "%q" "$prompt")" >"$out_file" 2>&1
                rc=$?
                ;;
            *)
                printf "%s\n" "$prompt" | bash -lc "$tool_cmd" >"$out_file" 2>&1
                rc=$?
                ;;
            esac
        fi
        set -e

        ts_end="$(date +%s)"

        local completed="0"
        grep -Fq "$complete_sigil" "$out_file" && completed="1" || true

        local after_head after_prd after_prog after_status
        after_head="$(git_head)"
        after_prd="$(hash_file "$prd_file")"
        after_prog="$(hash_file "$progress_file")"
        after_status="$(git_status_porcelain)"

        # --- Progress detection that DOES NOT require commits:
        # changed=1 if:
        #   - HEAD moved (commit happened)
        #   - PRD or progress.txt changed
        #   - git status snapshot changed (files edited/staged/unstaged changed)
        local changed="0"
        if [[ -n "$before_head" && -n "$after_head" && "$before_head" != "$after_head" ]]; then
            changed="1"
        elif [[ "$before_prd" != "$after_prd" || "$before_prog" != "$after_prog" ]]; then
            changed="1"
        elif [[ "$before_status" != "$after_status" ]]; then
            changed="1"
        fi

        # --- Handle failures
        if [[ "$rc" -ne 0 ]]; then
            fail_count=$((fail_count + 1))
            log "💥 iter=$iter tool rc=$rc  (fails $fail_count/$max_fails)"
            log "   output: $out_file"
            if [[ "$fail_count" -gt "$max_fails" ]]; then
                die "too many failures. see: $out_root"
            fi
            local backoff=$((2 ** (fail_count - 1)))
            [[ "$backoff" -gt 60 ]] && backoff=60
            log "⏳ backing off ${backoff}s"
            sleep "$backoff"
            continue
        fi

        fail_count=0

        # --- Stall logic (doesn't punish uncommitted work)
        if [[ "$changed" == "0" && "$completed" == "0" ]]; then
            stall_count=$((stall_count + 1))
            log "🧊 iter=$iter no detectable progress (stall $stall_count/$stall_limit)"
            log "   output: $out_file"
            if [[ "$stall_count" -ge "$stall_limit" ]]; then
                die "stalled for $stall_limit iterations. see: $out_root"
            fi
        else
            stall_count=0
        fi

        # --- State + JSONL record
        {
            printf "RLOOP_RUN_ID=%q\n" "$run_id"
            printf "RLOOP_ITER=%q\n" "$iter"
            printf "RLOOP_LAST_RC=%q\n" "$rc"
            printf "RLOOP_LAST_COMPLETED=%q\n" "$completed"
            printf "RLOOP_LAST_CHANGED=%q\n" "$changed"
            printf "RLOOP_LAST_OUT_FILE=%q\n" "$out_file"
            printf "RLOOP_BEFORE_HEAD=%q\n" "$before_head"
            printf "RLOOP_AFTER_HEAD=%q\n" "$after_head"
        } >"$state"

        printf '{"iter":%d,"ts_start":%s,"ts_end":%s,"rc":%d,"completed":%s,"changed":%s,"before_head":"%s","after_head":"%s","before_status":"%s","after_status":"%s","prd_hash_before":"%s","prd_hash_after":"%s","progress_hash_before":"%s","progress_hash_after":"%s","out_file":"%s"}\n' \
            "$i" \
            "$(json_escape "$ts_start")" \
            "$(json_escape "$ts_end")" \
            "$rc" \
            "$completed" \
            "$changed" \
            "$(json_escape "$before_head")" \
            "$(json_escape "$after_head")" \
            "$(json_escape "$before_status")" \
            "$(json_escape "$after_status")" \
            "$(json_escape "$before_prd")" \
            "$(json_escape "$after_prd")" \
            "$(json_escape "$before_prog")" \
            "$(json_escape "$after_prog")" \
            "$(json_escape "$out_file")" \
            >>"$jsonl"

        if [[ "$debug" == "1" ]]; then
            log "🧾 iter=$iter output tail:"
            tail -n 30 "$out_file" >&2 || true
        fi

        if [[ "$completed" == "1" ]]; then
            log "✅ COMPLETE at iter=$iter"
            exit 0
        fi

        log "✅ iter=$iter done. changed=$changed  repo_dirty=$([[ -n "$after_status" ]] && echo 1 || echo 0)"
        log "   logs: $iter_dir"
        log ""

        [[ "$sleep_sec" -gt 0 ]] && sleep "$sleep_sec"
    done

    die "max iterations hit. see: $out_root"
}

main "$@"
