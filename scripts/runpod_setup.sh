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
    CUDA_MM=12.9
elif [ "$CC_MAJOR" -ge 8 ]; then
    CUDA_IDX=cu126
    CUDA_MM=12.6
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

# nvidia-cusparse-cu12 depends on nvidia-nvjitlink-cu12 *without pinning a
# version*, and the pod image already ships a torch with its own older nvidia-*
# packages. So when paddlepaddle-gpu pulls in a CUDA 12.9 cusparse, pip is
# satisfied by the older nvjitlink already present and leaves it alone. Nothing
# fails at install time; `import paddle` then dies with
#   libcusparse.so.12: undefined symbol: __nvJitLinkGetErrorLogSize_12_9
#
# Pin nvjitlink from CUDA_MM (the index selected above), NOT from cusparse's own
# version: cuSPARSE carries an independent library version — 12.5.10.65 is the
# build shipped *with* CUDA 12.9 — so reading it back would pin nvjitlink to
# 12.5 and reintroduce the very mismatch this is fixing. nvjitlink's version
# does track the CUDA release (12.9.86 ships with 12.9).
align_nvjitlink() {
    echo "  $CUDA_IDX → pinning nvidia-nvjitlink-cu12 ~=$CUDA_MM.0"
    pip install -q --no-cache-dir "nvidia-nvjitlink-cu12~=${CUDA_MM}.0"
}

# Import paddle in a subprocess so a broken CUDA stack surfaces here, in
# seconds, rather than 20 minutes later after the vLLM install and the Zenodo
# download have already run.
check_paddle() {
    python - <<'PY'
import sys
try:
    import paddle
except Exception as exc:
    print(f"  import paddle FAILED: {exc}", file=sys.stderr)
    sys.exit(1)
print(f"  paddle {paddle.__version__} | cuda build: "
      f"{paddle.device.is_compiled_with_cuda()} | gpus: "
      f"{paddle.device.cuda.device_count()}")
PY
}

echo "=== Aligning CUDA runtime libs ==="
align_nvjitlink

echo "=== Verifying paddle imports ==="
if ! check_paddle; then
    echo "ERROR: paddle cannot import — the pip nvidia-* CUDA stack is inconsistent." >&2
    echo "       Inspect with:  pip list | grep nvidia-" >&2
    exit 1
fi

# The in-process ("native") VLM backend runs about 1 min/page. Offloading the
# VLM to a local vLLM server is the supported fast path. Set VL_BACKEND=native
# to opt out.
VL_BACKEND="${VL_BACKEND:-vllm-server}"
VL_PORT="${VL_PORT:-8118}"
VL_MODEL="${VL_MODEL:-PaddleOCR-VL-1.5-0.9B}"   # must match pipeline_version v1.5
# Layout detection still runs in-process on the same GPU, so vLLM must not
# claim all of it.
VL_GPU_FRAC="${VL_GPU_FRAC:-0.70}"
VL_URL="http://127.0.0.1:${VL_PORT}/v1"
SERVER_LOG=/workspace/genai_server.log

start_genai_server() {
    echo "=== Installing vLLM genai-server deps (large — a few minutes) ==="
    paddleocr install_genai_server_deps vllm

    # vLLM brings its own torch, which can drag the nvidia-* stack back out of
    # alignment under paddle's feet. Layout detection still runs in-process, so
    # a paddle that no longer imports means no OCR at all — check before the
    # model spends 15 minutes loading.
    echo "=== Re-aligning CUDA runtime libs after the vLLM install ==="
    align_nvjitlink
    if ! check_paddle; then
        echo "ERROR: installing the vLLM deps broke paddle's CUDA stack." >&2
        echo "       Re-run with VL_BACKEND=native to skip the vLLM install." >&2
        exit 1
    fi

    echo "=== Starting genai server: $VL_MODEL on port $VL_PORT ==="
    local cfg=/workspace/genai_backend_config.yaml
    echo "gpu-memory-utilization: $VL_GPU_FRAC" > "$cfg"

    paddleocr genai_server \
        --model_name "$VL_MODEL" \
        --backend vllm \
        --host 127.0.0.1 \
        --port "$VL_PORT" \
        --backend_config "$cfg" \
        > "$SERVER_LOG" 2>&1 &
    GENAI_PID=$!

    # The server loads the model before it answers. Poll the OpenAI-compatible
    # /v1/models endpoint rather than sleeping a fixed amount.
    echo "  waiting for $VL_URL to answer (up to 15 min) …"
    for i in $(seq 1 180); do
        if ! kill -0 "$GENAI_PID" 2>/dev/null; then
            echo "ERROR: genai server exited during startup. Last 40 lines:" >&2
            tail -40 "$SERVER_LOG" >&2
            return 1
        fi
        if curl -sf --max-time 5 "$VL_URL/models" > /dev/null 2>&1; then
            echo "  server ready after $((i * 5))s (pid $GENAI_PID)"
            return 0
        fi
        sleep 5
    done

    echo "ERROR: genai server not ready after 15 min. Last 40 lines:" >&2
    tail -40 "$SERVER_LOG" >&2
    kill "$GENAI_PID" 2>/dev/null || true
    return 1
}

OCR_ARGS=()
if [ "$VL_BACKEND" = "vllm-server" ]; then
    if start_genai_server; then
        OCR_ARGS=(--vl-backend vllm-server --vl-server-url "$VL_URL")
    else
        echo "WARNING: falling back to the native backend (~1 min/page)" >&2
        VL_BACKEND=native
    fi
fi
echo "=== VLM backend: $VL_BACKEND ==="

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
#
# --resume picks up any checkpoint left by an interrupted run. Checkpoints live
# under ocr_output/_paddle_cache on the /workspace volume, so they outlive the
# pod: re-running this script after a pod dies costs only the pages that were
# never reached, not the whole volume.
failed=()
for vol in "${VOLS[@]}"; do
    echo "--- vol $vol ---"
    if ! python -u -m flora_ocr.ocr.paddle --vol "$vol" --resume "${OCR_ARGS[@]}"; then
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
