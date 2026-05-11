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
