# flora-ocr

OCR pipeline for botanical floras: turn scanned PDF volumes into structured
markdown, translated text, interactive identification keys, and an
LLM-maintained Obsidian wiki.

The pipeline is flora-agnostic. Each flora has its own config under
`floras/<name>/flora.toml` declaring where its PDFs live, where output goes,
and how its volume filenames are named. Active flora: **flore_du_gabon**
(Flore du Gabon, 61 volumes).

---

## Layout

```
src/flora_ocr/         python package
  ocr/                 liteparse, mineru, paddle  — three OCR backends
  pipeline/            translate, reclassify_headings, reformat_keys, build_key_data
  app/                 key_app.py                 — Streamlit interactive key
  wiki/                ingest into the shared Obsidian wiki
  flora.py             flora.toml loader
floras/
  flore_du_gabon/      flora.toml, download.sh, *.keys.json, patches
wiki/                  shared cross-flora Obsidian vault (Karpathy-style)
docs/llm-wiki.md       reference: the LLM-wiki pattern
experiments/           archived OCR experiments (marker, mineru-diffusion, deepseek)
```

PDFs are gitignored. Most OCR output is also gitignored, but the repo may
intentionally track selected **family-level** and **article-level** OCR source
directories under `ocr_output/` when the wiki depends on them.

## Conda environments

| Env | Python | Used for |
|-----|--------|----------|
| `p12` | 3.14 | Most things: liteparse, mineru, marker, translation, key building, Streamlit app |
| `ds_ocr` | 3.x | PaddleOCR VL (alternative OCR — best figure separation) |

Always prefix commands with `conda run -n <env>`.

## Volume types

| Volumes | Type | Best OCR tool |
|---------|------|---------------|
| 1–37 | Scanned images | `flora_ocr.ocr.mineru` or `flora_ocr.ocr.paddle` |
| 38–60 | Embedded text | `flora_ocr.ocr.liteparse` |

## Running the pipeline

All scripts default to `--flora flore_du_gabon`. Pass `--flora <name>` to
target a different flora.

```bash
# OCR
conda run -n p12    python -m flora_ocr.ocr.liteparse --vol 60
conda run -n p12    python -m flora_ocr.ocr.mineru    --vol 11
conda run -n ds_ocr python -m flora_ocr.ocr.paddle    --vol 11

# Translate fr → en
conda run -n p12 python -m flora_ocr.pipeline.translate --vol 11

# Re-level headings → taxa index
conda run -n p12 python -m flora_ocr.pipeline.reclassify_headings \
    ocr_output/vol11_paddle/text_en.md

# Build interactive-key dataset (one per family)
conda run -n p12 python -m flora_ocr.pipeline.build_key_data \
    --source  ocr_output/vol11_paddle/text_en.md \
    --figures ocr_output/vol11_paddle/figures.md \
    --fig-dir ocr_output/vol11_paddle/figures \
    --family  Myrtaceae \
    --title   "Vol. 11 — Myrtaceae" \
    --output  floras/flore_du_gabon/vol11_myrtaceae.keys.json

# Run the Streamlit app
conda run -n p12 streamlit run src/flora_ocr/app/key_app.py
```

## OCR on RunPod

The scanned volumes (1–37) need a GPU we don't have locally. `scripts/runpod_setup.sh`
provisions a pod from scratch: it clones this repo, installs PaddlePaddle against
the right CUDA index, downloads the PDF from Zenodo, starts a vLLM genai server for
the VLM, and runs the OCR.

Launch a pod with:

- **compute capability ≥ 8.0** (Ampere, Ada, Hopper, or Blackwell). The script picks
  the PaddlePaddle CUDA index from the GPU's compute capability — `cu129` for 12.x,
  `cu126` for 8.x/9.x — and exits with a clear message on anything older rather than
  dying inside a CUDA kernel launch.
- **a persistent `/workspace` volume**. Resume checkpoints live there, so a pod that
  dies can pick up where it left off. Model weights (~1 GB) are cached there too; the
  container root disk is small and a download that fills it stalls with no error.

Then, in the pod's terminal:

```bash
cd /workspace
curl -fsSL https://raw.githubusercontent.com/ggosline/flora-ocr/main/scripts/runpod_setup.sh \
    -o runpod_setup.sh
chmod +x runpod_setup.sh

# nohup, so a dropped web terminal doesn't SIGHUP the run. A volume is hours of
# GPU time — don't run it in the foreground of a terminal you might lose.
nohup ./runpod_setup.sh 17 &
tail -f /workspace/ocr_vol17.log
```

Volumes are positional: `./runpod_setup.sh 17 18 29` does several in sequence. A
volume that fails doesn't abort the rest, and the pod is kept alive afterwards
(`sleep infinity`) so results can be retrieved.

### Why two venvs

Nothing is installed into the pod's system Python. Three CUDA stacks want the same
pip `nvidia-*` packages and none of them agree — the image's own torch (`2.4.1+cu124`),
`paddlepaddle-gpu` (which pins its nvidia deps *exactly*, e.g. `nvidia-nvjitlink-cu12==12.9.41`),
and the torch vLLM pulls in. Installing them together leaves whichever ran last
holding the shared libraries, and the loser dies at import with an undefined symbol.
So each gets its own environment:

| venv | Holds | Used for |
|------|-------|----------|
| `/workspace/venv-ocr` | paddlepaddle-gpu, paddleocr, this repo | the OCR process |
| `/workspace/venv-vllm` | paddleocr, vLLM + its torch | the genai server |

They talk over HTTP, so they never need to share an interpreter. Both live on
`/workspace` and are reused by a restarted pod rather than reinstalled. A vLLM
failure can no longer break Paddle — the worst case is a fallback to the native
backend.

### Reading the log

Everything is tee'd to `/workspace/ocr_vol<N>.log`. Two lines tell you the run is
healthy:

- `=== VLM backend: vllm-server ===` — the fast path. Measured at **~15 s/page** on a
  Blackwell pod, so ~85 min for vol 17's 338 pages. If it says `native` instead, the
  genai server failed to start and the run fell back to the in-process VLM at
  ~1 min/page — 5–6 hours for the same volume. The reason will be in
  `/workspace/genai_server.log`.
- `[volN] no checkpoint — starting from page 1`, or `[volN] resuming at page X/Y` on a
  restart.

Per-page progress lines carry an ETA, so the real rate is visible within minutes.

### If the run dies

Re-run the same command. The repo `git pull`s, `--resume` finds the checkpoint under
`ocr_output/_paddle_cache`, and only the pages never reached are OCR'd again — provided
the same `/workspace` volume is attached. Checkpoints are written every 25 pages
(`--checkpoint-every N`, `0` disables) and cleared only once the volume completes.

### Retrieving results

Output lands in `/workspace/flora-ocr/ocr_output/{Family}_vol{N}_paddle/`, one directory
per family detected in the volume. Copy it down with `runpodctl send`, or `scp -r` if
SSH is configured.

### Environment knobs

| Variable | Default | Effect |
|----------|---------|--------|
| `VL_BACKEND` | `vllm-server` | `native` runs the VLM in-process (~10× slower, no server) |
| `VL_GPU_FRAC` | `0.70` | Fraction of GPU memory vLLM may claim; layout detection shares the same GPU |
| `VL_PORT` | `8118` | Port for the genai server |
| `PADDLE_PDX_MODEL_SOURCE` | `huggingface` | Set to `bos` or `aistudio` if HF is slow or blocked from the pod's region |

## Adding a new flora

1. Create `floras/<name>/flora.toml`:

   ```toml
   title = "My Flora"
   language = "fr"
   pdf_dir = "/abs/path/to/pdfs"          # or relative to repo root
   output_dir = "/abs/path/to/output"
   pdf_glob = "*.pdf"
   vol_pattern = '(?P<label>\d+)\.pdf'    # named group 'label' is required
   ```

2. Run any pipeline script with `--flora <name>`.

3. Built `*.keys.json` files for the app go under `floras/<name>/`.

## Wiki

`wiki/` is a single Obsidian vault shared across all floras. The LLM ingests
OCR output and maintains entity pages (families, genera, species), source
volumes, and topic notes. See `wiki/AGENTS.md` and `wiki/CLAUDE.md` for the
schema, and `docs/llm-wiki.md` for the pattern this is based on.

## Portable checkout

This repository is meant to be usable on another machine without reconstructing
everything from scratch.

- The wiki content under `wiki/` is committed.
- The OCR code and flora configuration are committed.
- The specific `ocr_output/<Family>_vol<NN>_<engine>/` and
  `ocr_output/articles/<article_id>/<engine>/` directories that current wiki
  pages cite as sources are committed as needed.
- Old whole-volume OCR runs, scratch experiments, caches, and PDF corpora stay
  out of git.

That split is deliberate: the wiki must keep its cited OCR sources, but old
volume-level experiments are regenerable and too noisy to version.

When continuing the ingest on another machine:

1. Create the conda envs described above (`p12` and `ds_ocr`).
2. Use `wiki/index.md`, `wiki/log.md`, and `wiki/overview.md` to see current
   coverage.
3. Read `wiki/AGENTS.md` and `wiki/CLAUDE.md` before any non-trivial ingest.
4. If you add a new family/article OCR source that the wiki will cite, stage it
   explicitly because `ocr_output/` is ignored by default:

```bash
git add -f ocr_output/<Family>_vol<NN>_<engine>
git add -f ocr_output/articles/<article_id>/<engine>
```
