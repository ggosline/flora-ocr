#!/usr/bin/env bash
# OCR a list of volumes through the sglang genai server, one at a time.
#
#   scripts/run_batch_sglang.sh <logtag> <vol> [<vol> ...]
#
# Volumes run SEQUENTIALLY on purpose: concurrent invocations exhaust the GPU
# (see e3522f6). Before each volume the server is health-checked, so a dead
# server aborts the batch immediately instead of failing every remaining
# volume one by one.
set -uo pipefail

TAG="${1:?usage: run_batch_sglang.sh <logtag> <vol>...}"; shift
VOLS=("$@")
URL="${VL_SERVER_URL:-http://127.0.0.1:8118/v1}"
HEALTH="${URL%/v1}/health"
LOGDIR="${LOGDIR:-/mnt/e/tmp/batch_$TAG}"
mkdir -p "$LOGDIR"

echo "=== batch $TAG: ${#VOLS[@]} volumes -> $LOGDIR ==="

ok=0; failed=(); skipped=()
for v in "${VOLS[@]}"; do
    if ! curl -sf --max-time 10 "$HEALTH" >/dev/null 2>&1; then
        echo "!!! sglang server not answering at $HEALTH — aborting batch $TAG"
        echo "    remaining: ${VOLS[*]}"
        break
    fi

    echo "--- vol $v starting $(date +%H:%M:%S)"
    t0=$SECONDS
    conda run -n ds_ocr2 --no-capture-output env PYTHONPATH=src \
        python -u -m flora_ocr.ocr.paddle \
        --vol "$v" --pipeline-version v1.5 \
        --vl-backend sglang-server --vl-server-url "$URL" \
        > "$LOGDIR/vol$v.log" 2>&1
    rc=$?
    dt=$((SECONDS - t0))

    if [ $rc -eq 0 ] && grep -q "Done in" "$LOGDIR/vol$v.log"; then
        line=$(grep -h "Done in" "$LOGDIR/vol$v.log" | tail -1)
        echo "    OK  vol $v in ${dt}s — $line"
        ok=$((ok+1))
    elif grep -qi "not found\|Known:" "$LOGDIR/vol$v.log"; then
        echo "    SKIP vol $v (no such volume)"
        skipped+=("$v")
    else
        echo "    FAIL vol $v (rc=$rc) — see $LOGDIR/vol$v.log"
        failed+=("$v")
    fi
done

echo "=== batch $TAG done: $ok ok, ${#failed[@]} failed, ${#skipped[@]} skipped"
[ ${#failed[@]} -gt 0 ] && echo "    failed: ${failed[*]}"
[ ${#skipped[@]} -gt 0 ] && echo "    skipped: ${skipped[*]}"
exit 0
