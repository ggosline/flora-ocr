#!/usr/bin/env bash
# Re-run post-processing (family split) for every volume that has a cached raw
# OCR dump. No GPU: --from-cache skips OCR entirely and only re-splits.
#
# Driven off the cache listing rather than --all, so a volume without a cache
# can never fall through into a fresh OCR run.
set -uo pipefail
cd /mnt/e/flora-ocr
LOG=${LOG:-/mnt/e/tmp/resplit}
mkdir -p "$LOG"
ok=0; fail=()
for raw in ocr_output/_paddle_cache/vol*_raw.md; do
    v=$(basename "$raw" _raw.md); v=${v#vol}
    conda run -n ds_ocr2 --no-capture-output env PYTHONPATH=src \
        python -u -m flora_ocr.ocr.paddle --vol "$v" \
        --from-cache --force --pipeline-version v1.5 \
        > "$LOG/vol$v.log" 2>&1
    if grep -q "Done in\|Families:" "$LOG/vol$v.log"; then
        echo "  vol $v: $(grep -h 'Families:' "$LOG/vol$v.log" | tail -1 | sed 's/.*Families: //')"
        grep -h "WARNING: split does not match\|Cover check OK" "$LOG/vol$v.log" | tail -1 | sed 's/^/      /'
        ok=$((ok+1))
    else
        echo "  vol $v: FAILED — see $LOG/vol$v.log"; fail+=("$v")
    fi
done
echo "resplit: $ok ok, ${#fail[@]} failed ${fail[*]:-}"
