#!/bin/bash
# RunPod setup: install deps, download PDF(s) from Zenodo, run PaddleOCR.
#
# Usage:
#   ./scripts/runpod_setup.sh 17          # one volume
#   ./scripts/runpod_setup.sh 17 18 29    # several
#
# Results land in ocr_output/{Family}_vol{N}_paddle/. The pod is kept alive
# afterwards so the output can be retrieved.
set -e

if [ $# -eq 0 ]; then
    echo "Usage: $0 <volume> [volume ...]   e.g. $0 17" >&2
    exit 1
fi

VOLS=("$@")
LOG=/workspace/ocr_vol$(IFS=_; echo "${VOLS[*]}").log
mkdir -p /workspace
exec > >(tee -a "$LOG") 2>&1

echo "=== Setup started $(date) — volumes: ${VOLS[*]} ==="
cd /workspace

# PaddleOCR-VL downloads ~1 GB of weights on first use. Keep them on the
# /workspace volume: the container root disk is small, and a download that
# fills it stalls with no error message.
export PADDLE_PDX_CACHE_HOME=/workspace/.paddlex
export HF_HOME=/workspace/.cache/huggingface
mkdir -p "$PADDLE_PDX_CACHE_HOME" "$HF_HOME"

# Weights come from HuggingFace by default; BOS/AIStudio are the fallbacks if
# HF is slow or blocked from the pod's region.
export PADDLE_PDX_MODEL_SOURCE="${PADDLE_PDX_MODEL_SOURCE:-huggingface}"

echo "=== GPU ==="
nvidia-smi --query-gpu=name,compute_cap,memory.total,driver_version --format=csv || {
    echo "ERROR: nvidia-smi unavailable — this pod has no usable GPU" >&2
    exit 1
}
echo "=== Disk ==="
df -h / /workspace

# The paddlepaddle-gpu wheels ship FlashAttention kernels precompiled for a
# fixed set of GPU architectures, and picking the wrong index dies deep inside
# a kernel launch with "no kernel image is available for execution on the
# device". Select the index from the GPU's compute capability instead:
#   12.x  Blackwell (RTX PRO 4000/6000, 5090, B200) — needs sm_120 → cu129
#   8.x/9.x  Ampere, Ada, Hopper (A100, L4, L40S, 4090, H100)      → cu126
CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' ')
CC_MAJOR=${CC%%.*}
PADDLE_VER=3.3.1

if [ "$CC_MAJOR" -ge 12 ]; then
    CUDA_IDX=cu129
elif [ "$CC_MAJOR" -ge 8 ]; then
    CUDA_IDX=cu126
else
    echo "ERROR: GPU compute capability $CC is below 8.0 — PaddleOCR-VL needs" >&2
    echo "       Ampere or newer. Pick a different pod type." >&2
    exit 1
fi
echo "=== GPU compute capability $CC → paddlepaddle-gpu==$PADDLE_VER ($CUDA_IDX) ==="

if [ -d flora-ocr/.git ]; then
    echo "=== Repo already present, pulling ==="
    cd flora-ocr && git pull
else
    git clone https://github.com/ggosline/flora-ocr.git
    cd flora-ocr
fi

echo "=== Clearing pip cache to free disk space ==="
pip cache purge || true

echo "=== Installing PaddlePaddle GPU ($CUDA_IDX) ==="
pip install -q --no-cache-dir "paddlepaddle-gpu==$PADDLE_VER" \
    -i "https://www.paddlepaddle.org.cn/packages/stable/$CUDA_IDX/"

echo "=== Installing PaddleOCR deps ==="
pip install -q --no-cache-dir --ignore-installed blinker
pip install -q --no-cache-dir paddleocr "paddlex[ocr]" tomli "pymupdf==1.24.14"
pip install -q -e .

# Zenodo record IDs for the 61 Flore du Gabon volumes (same list as
# floras/flore_du_gabon/download.sh). Not ordered by volume number, so we
# query each record for its filename and match.
RECORD_IDS=(
    14910050 14900487 14900391
    11079343 11079277 11079224 11077824 11077792 11077761
    11074384 11074363 11073959 11073761 11073479 11072829
    11072743 11072660 11072531 11072140 11072103 11072024
    11068388 11068345 11068267 11066135 11066062 11065995
    11065903 11064952 11064786 11061591 11061516 11061444
    11061402 11061373 11059544 11059484 11059446 11059383
    11059307 11039844 11039572 11039294 11038976 11038686
    11032174 11032134 11032099 11005131 11005077 11004874
    11004851 11004527 11004506 11004467 11002410 11002365
    11002335 11002291 11002006 11001797
)

# Echo the filename Zenodo holds for a record, or empty on failure.
zenodo_filename() {
    curl -s --max-time 30 "https://zenodo.org/api/records/$1" | python3 -c "
import json, sys
try:
    files = json.load(sys.stdin).get('files', [])
except Exception:
    files = []
print(files[0]['key'] if files else '')
" 2>/dev/null
}

# Download 'FdG vol. <VOL> OK.pdf' into floras/flore_du_gabon/.
fetch_volume() {
    local vol="$1"
    local want="FdG vol. ${vol} OK.pdf"
    local dest="floras/flore_du_gabon/${want}"

    if [ -f "$dest" ]; then
        echo "  SKIPPED: '$want' already present"
        return 0
    fi

    for rec in "${RECORD_IDS[@]}"; do
        local name
        name=$(zenodo_filename "$rec")
        [ "$name" = "$want" ] || continue

        local encoded
        encoded=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$want")
        echo "  Downloading '$want' from record $rec"
        if curl -L --max-time 600 --retry 3 --retry-delay 5 \
                -o "$dest" "https://zenodo.org/api/records/$rec/files/$encoded/content"; then
            return 0
        fi
        rm -f "$dest"
        echo "  ERROR: download failed for '$want'" >&2
        return 1
    done

    echo "  ERROR: no Zenodo record holds '$want'" >&2
    return 1
}

# A broken python3 makes every zenodo_filename call return empty, which would
# look like "no record holds this volume" after 61 API calls. Fail loudly first.
if ! python3 -c 'import json, urllib.parse' 2>/dev/null; then
    echo "ERROR: python3 is not usable — needed to parse the Zenodo API response" >&2
    exit 1
fi

echo "=== Downloading PDFs ==="
for vol in "${VOLS[@]}"; do
    fetch_volume "$vol"
done

echo "=== Starting OCR ==="
# A failed volume must not kill the script — the pod has to stay alive so the
# other volumes run and the log can be retrieved.
failed=()
for vol in "${VOLS[@]}"; do
    echo "--- vol $vol ---"
    if ! python -u -m flora_ocr.ocr.paddle --vol "$vol"; then
        echo "ERROR: vol $vol failed" >&2
        failed+=("$vol")
    fi
    df -h /workspace | tail -1
done

if [ ${#failed[@]} -gt 0 ]; then
    echo "=== FAILED volumes: ${failed[*]} ==="
fi

echo "=== Done $(date) ==="
echo "Results in /workspace/flora-ocr/ocr_output/ — pod kept alive for retrieval"
sleep infinity
