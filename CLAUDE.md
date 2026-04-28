# flora-ocr — Project Context for Claude Code

## Project

OCR pipeline for botanical floras. Active flora is **Flore du Gabon** (61 volumes,
French). The pipeline is flora-agnostic — every active script takes a
`--flora <name>` flag (default `flore_du_gabon`) and reads paths/patterns from
`floras/<name>/flora.toml`.

- Repo root: `/mnt/e/FloreDuGabon/`
- Source PDFs (gitignored): `FloreDuGabon/FdG vol. NN OK.pdf`
- OCR output (gitignored): `ocr_output/`
- Platform: native Linux (NOT WSL), CUDA 12.8

## Conda environments

| Env | Python | Used for |
|-----|--------|---------|
| `p12` | 3.14 | Most pipeline scripts (liteparse, mineru, marker, translate, build, app) |
| `ds_ocr` | 3.x | PaddleOCR VL (best figure separation) |

Always prefix commands with `conda run -n <env>`.

### Key packages in `p12`
- torch 2.10.0+cu128
- transformers 4.53.3, tokenizers 0.21.4
- marker-pdf 1.9.2 + surya-ocr 0.16.1
- magic-pdf 1.3.10 (MinerU)
- pymupdf 1.24.14 (pinned — magic-pdf compat; do not upgrade)
- matplotlib 3.9.4 (pinned <3.10 — newer requires CXXABI_1.3.15 unavailable on this system)

### Model cache (symlinked off home drive)
- `~/.cache/datalab` → `/mnt/e/model_cache/datalab/`
- `~/.cache/huggingface` → `/mnt/e/model_cache/huggingface/`

## Repo layout

```
src/flora_ocr/
  flora.py             load_flora(name) → Flora config (pdf_dir, output_dir, vol_pattern, …)
  ocr/                 liteparse.py, mineru.py, paddle.py
  pipeline/            translate.py, reclassify_headings.py, reformat_keys.py, build_key_data.py
  app/                 key_app.py
  wiki/                wiki ingest scripts
floras/
  flore_du_gabon/
    flora.toml         pdf_dir, output_dir, vol_pattern, language, pdf_glob
    download.sh        fetch source PDFs
    patches_vol11.json
    vol11_*.keys.json  built artefacts consumed by the app
wiki/                  shared Obsidian vault across all floras (AGENTS.md / CLAUDE.md schema, families/, genera/, species/, sources/, topics/, volumes/, assets/)
docs/llm-wiki.md       Karpathy-style LLM-wiki reference pattern
experiments/           archived OCR experiments (marker, mineru-diffusion, deepseek)
```

## flora.toml format

```toml
title = "Flore du Gabon"
language = "fr"
pdf_dir = "FloreDuGabon"            # relative paths resolve from repo root
output_dir = "ocr_output"
pdf_glob = "FdG vol. *.pdf"
vol_pattern = 'FdG vol\. (?P<label>\d+(?:bis)?)\s+OK(?:-\d+)?\.pdf'
```

The regex must define a named group `label`. Use TOML *literal* strings
(single quotes) so backslashes pass through unescaped.

## How to run

All commands run from repo root. Pass `--flora <name>` to target a different
flora; default is `flore_du_gabon`.

```bash
# OCR — embedded text vols (38–60)
conda run -n p12 python -m flora_ocr.ocr.liteparse --vol 60
conda run -n p12 python -m flora_ocr.ocr.liteparse --all --start-from 38

# OCR — scanned vols (1–37) with MinerU
conda run -n p12 python -m flora_ocr.ocr.mineru --vol 11
conda run -n p12 python -m flora_ocr.ocr.mineru --all

# OCR — PaddleOCR (alternative, better figure separation)
conda run -n ds_ocr python -m flora_ocr.ocr.paddle --vol 11
conda run -n ds_ocr python -m flora_ocr.ocr.paddle --all --start-from 20

# Translate fr → en
conda run -n p12 python -m flora_ocr.pipeline.translate --vol 11
conda run -n p12 python -m flora_ocr.pipeline.translate --all --start-from 5

# Reclassify headings (operates on a specific text_en.md path; flora-agnostic)
conda run -n p12 python -m flora_ocr.pipeline.reclassify_headings \
    ocr_output/vol11_paddle/text_en.md

# Build interactive-key dataset (one per family)
conda run -n p12 python -m flora_ocr.pipeline.build_key_data \
    --source  ocr_output/vol11_paddle/text_en.md \
    --figures ocr_output/vol11_paddle/figures.md \
    --fig-dir ocr_output/vol11_paddle/figures \
    --family  Myrtaceae \
    --title   "Vol. 11 — Myrtaceae" \
    --default-key genera_myrtaceae \
    --genus-links "Eugenia:species_eugenia,Syzygium:species_syzygium" \
    --patches floras/flore_du_gabon/patches_vol11.json \
    --output  floras/flore_du_gabon/vol11_myrtaceae.keys.json

# Streamlit app — auto-discovers floras/*/*.keys.json
conda run -n p12 streamlit run src/flora_ocr/app/key_app.py
# Stop: click "⏹ Quit app" in sidebar, or pkill -f "streamlit run"
```

## Output directory layout

```
ocr_output/
  volNN_paddle/
    text.md              OCR output (source language)
    text_en.md           translated → English (input to build_key_data)
    text_en_keyfmt.md    indented keys (human-readable)
    text_structured.md   reclassified headings
    text_taxa.json       taxa index
    text_taxa.tsv        taxa index (TSV)
    figures.md           figure captions
    figures/             fig_NNN_pPPPP.png …
    metadata.json
  volNN_mineru/          text.md, figures/, metadata.json
  volNN_liteparse/       text.md, metadata.json
  articles/{id}/liteparse/   per-article output
```

## MinerU setup notes

- Config: `~/magic-pdf.json` (models-dir, layoutreader-model-dir, device-mode=cuda)
- Models: `/mnt/e/model_cache/mineru/PDF-Extract-Kit-1.0/models/`
- LayoutReader: `/mnt/e/model_cache/mineru/layoutreader/`
- OCR symlinks required (v3→v3 mapping):
  - `ch_PP-OCRv3_det_infer.pth` → `Multilingual_PP-OCRv3_det_infer.pth`
  - `en_PP-OCRv3_det_infer.pth` → `Multilingual_PP-OCRv3_det_infer.pth`
- Use `lang=None` (ch default): `fr`/`latin` maps to v5 rec models incompatible with pytorchocr; the ch v4 model handles French script correctly.

## Patches file format

Correct OCR/parse errors after the fact without re-running OCR.

```json
{
  "genera_myrtaceae": {
    "nodes": {
      "2": {"is_terminal": true, "terminal_name": "Psidium", "leads_to": null}
    }
  }
}
```

## Adding a new flora

1. Create `floras/<name>/flora.toml` with `pdf_dir`, `output_dir`, `pdf_glob`, `vol_pattern`, `language`.
2. Run any pipeline script with `--flora <name>`.
3. Built `*.keys.json` files for the app go under `floras/<name>/`.

## Adding a new volume (existing flora)

1. OCR: `flora_ocr.ocr.paddle --vol N` (scanned) or `flora_ocr.ocr.liteparse --vol N` (born-digital)
2. Translate: `flora_ocr.pipeline.translate --vol N`
3. (Optional) `flora_ocr.pipeline.reclassify_headings` on `text_en.md`
4. `flora_ocr.pipeline.build_key_data` once per family
5. App auto-discovers new `*.keys.json` on next page load

To inspect parsed nodes before building:
```python
import json
data = json.load(open("floras/flore_du_gabon/volN_family.keys.json"))
for kid, key in data["keys"].items():
    print(kid, list(key["nodes"].keys())[:10])
```
