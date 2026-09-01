#!/usr/bin/env bash
# Start / stop / check the PaddleOCR-VL genai server on the sglang backend.
#
#   scripts/sglang_server.sh start [--model-version v1.5|v1.6]
#   scripts/sglang_server.sh stop
#   scripts/sglang_server.sh status
#   scripts/sglang_server.sh restart
#
# The OCR client then runs with:
#   conda run -n ds_ocr2 python -m flora_ocr.ocr.paddle --vol N --vl-backend sglang-server
#
# Why sglang and not vLLM: paddlex pins vllm==0.10.2, which dies on inference
# with KeyError('pixel_values'); the engine goes with it and every later page
# 500s into a poisoned checkpoint. sglang serves the same model at ~1.3 s/page
# against the native backend's ~10-13.
set -uo pipefail

VENV=${SGLANG_VENV:-/mnt/e/venvs/sglang}
PORT=${VL_PORT:-8118}
HOST=${VL_HOST:-127.0.0.1}
URL="http://$HOST:$PORT/v1"
HEALTH="http://$HOST:$PORT/health"
LOG=${SGLANG_LOG:-/mnt/e/tmp/sglang_server.log}
MODELS=${PADDLEX_MODELS:-$HOME/.paddlex/official_models}
VERSION=v1.5
STARTUP_TIMEOUT=${STARTUP_TIMEOUT:-600}

cmd=${1:-start}; shift || true
while [ $# -gt 0 ]; do
    case "$1" in
        --model-version) VERSION="$2"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

case "$VERSION" in
    v1.5) MODEL_NAME=PaddleOCR-VL-1.5-0.9B; MODEL_DIR="$MODELS/PaddleOCR-VL-1.5" ;;
    v1.6) MODEL_NAME=PaddleOCR-VL-1.6-0.9B; MODEL_DIR="$MODELS/PaddleOCR-VL-1.6" ;;
    *) echo "--model-version must be v1.5 or v1.6" >&2; exit 2 ;;
esac

is_up() { curl -sf --max-time 5 "$HEALTH" >/dev/null 2>&1; }
# Match the server's own argv (an absolute .../bin/paddlex_genai_server plus this
# port), never a shell that merely mentions the name — do_stop kills what this
# returns, so a loose pattern here would kill the caller.
server_pids() { pgrep -f "$VENV/bin/paddlex_genai_server .*--port $PORT" 2>/dev/null; }

# sglang runs a tree — http_server/tokenizer_manager, scheduler, detokenizer and
# a pool of torch inductor compile workers. Killing only the launcher orphans
# them: they keep the port bound and ~13 GB on the GPU, and `status` then reports
# UP with no pid. They all share the launcher's process group (start() uses
# setsid to guarantee it is a fresh one), so the group is the handle to kill.
do_stop() {
    local pid pgid
    pid=$(server_pids | head -1)
    if [ -z "$pid" ]; then
        # launcher already gone; catch a group orphaned by an earlier bad stop
        pid=$(pgrep -f "sglang::http_server" 2>/dev/null | head -1)
        [ -z "$pid" ] && { echo "no server process on port $PORT"; return 0; }
    fi
    pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ')
    if [ -z "$pgid" ] || [ "$pgid" = "$(ps -o pgid= -p $$ | tr -d ' ')" ]; then
        echo "refusing to kill process group $pgid — it is our own" >&2
        return 1
    fi
    echo "stopping process group $pgid"
    kill -- "-$pgid" 2>/dev/null
    for _ in $(seq 30); do
        if ! kill -0 -- "-$pgid" 2>/dev/null; then
            echo "stopped"; return 0
        fi
        sleep 1
    done
    echo "still alive after 30s — sending SIGKILL to group $pgid"
    kill -9 -- "-$pgid" 2>/dev/null
    sleep 2
    echo "stopped (forced)"
}

do_status() {
    if is_up; then
        local pids; pids=$(server_pids | tr '\n' ' ')
        if [ -z "$pids" ]; then
            echo "port $PORT answers but the launcher is gone — orphaned worker tree;" \
                 "run '$0 stop' to clear it"
            return 1
        fi
        echo "server UP on $URL (pids: $pids)"
        return 0
    fi
    if [ -n "$(server_pids)" ]; then
        echo "process alive but not answering $HEALTH — still loading, or wedged"
        return 1
    fi
    echo "server DOWN"
    return 1
}

do_start() {
    # Starting a second server on a taken port fails to bind, the poll below
    # times out, and the caller silently drops to the native backend at ~8x the
    # cost per page. Reuse a live one, and skip the model load while we are at it.
    if is_up; then
        echo "=== reusing the genai server already answering on $URL ==="
        return 0
    fi
    if [ -n "$(server_pids)" ]; then
        echo "a server process exists but is not answering; stop it first" >&2
        return 1
    fi
    [ -x "$VENV/bin/paddlex_genai_server" ] || {
        echo "ERROR: no paddlex_genai_server in $VENV (see CLAUDE.md)" >&2; return 1; }
    [ -d "$MODEL_DIR" ] || {
        echo "ERROR: model dir not found: $MODEL_DIR" >&2; return 1; }

    mkdir -p "$(dirname "$LOG")"
    echo "=== starting sglang server ($VERSION) on $URL ==="
    # PATH must carry the venv's bin: flashinfer shells out to `ninja` to build
    # its kernels, and without it the worker dies with FileNotFoundError.
    # setsid: the worker tree must sit in its own process group so do_stop can
    # signal the group without touching this script or its caller.
    PATH="$VENV/bin:$PATH" setsid nohup "$VENV/bin/paddlex_genai_server" \
        --model_name "$MODEL_NAME" --model_dir "$MODEL_DIR" \
        --backend sglang --host "$HOST" --port "$PORT" \
        > "$LOG" 2>&1 &
    local pid=$!
    echo "    pid $pid, log $LOG"

    # Weight load + CUDA-graph capture is ~60-90s cold. Fail loudly rather than
    # let a caller queue a two-hour batch against a server that never came up.
    for _ in $(seq "$STARTUP_TIMEOUT"); do
        if is_up; then echo "=== server ready on $URL ==="; return 0; fi
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "ERROR: server exited during startup — last lines of $LOG:" >&2
            grep -viE "FutureWarning|import pynvml|Gloo" "$LOG" | tail -15 >&2
            return 1
        fi
        sleep 1
    done
    echo "ERROR: server did not answer $HEALTH within ${STARTUP_TIMEOUT}s" >&2
    return 1
}

case "$cmd" in
    start)   do_start ;;
    stop)    do_stop ;;
    status)  do_status ;;
    restart) do_stop; do_start ;;
    *) echo "usage: $0 {start|stop|status|restart} [--model-version v1.5|v1.6]" >&2; exit 2 ;;
esac
