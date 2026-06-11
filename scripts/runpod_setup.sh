#!/bin/bash
# RunPod setup: install deps, download PDF, run PaddleOCR on vol 18
set -e
LOG=/workspace/ocr_vol18.log
exec > >(tee -a "$LOG") 2>&1

echo "=== Setup started $(date) ==="
cd /workspace

git clone https://github.com/ggosline/flora-ocr.git
cd flora-ocr

echo "=== Installing PaddlePaddle GPU (cu118) ==="
pip install -q paddlepaddle-gpu==3.0.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu118/

echo "=== Installing PaddleOCR deps ==="
pip install -q paddleocr "paddlex[ocr]" tomli "pymupdf==1.24.14"
pip install -q -e .

echo "=== Downloading vol 18 PDF ==="
wget -q --show-progress \
  -O "floras/flore_du_gabon/FdG vol. 18 OK.pdf" \
  "https://zenodo.org/records/11039294/files/FdG%20vol.%2018%20OK.pdf?download=1"

echo "=== Starting OCR ==="
python -m flora_ocr.ocr.paddle --vol 18

echo "=== Done $(date) ==="
echo "Results in /workspace/flora-ocr/ocr_output/ — pod kept alive for retrieval"
sleep infinity
