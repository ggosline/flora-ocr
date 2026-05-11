# Repository Guidelines

## Project Structure & Module Organization
Core code lives in `src/flora_ocr/`. Use `ocr/` for backend-specific extraction (`liteparse.py`, `mineru.py`, `paddle.py`), `pipeline/` for post-OCR transforms, `app/` for the Streamlit key browser, and `flora.py` for shared config loading. Per-flora configuration and generated key datasets belong under `floras/<name>/` (for example `floras/flore_du_gabon/flora.toml`). Reference material and notes live in `docs/`, `experiments/`, and `wiki/`. Large inputs and generated outputs such as `FloreDuGabon/`, `ocr_output/`, and `article_pdfs/` are working data, not source code.

## Build, Test, and Development Commands
Run commands from the repo root and always use the expected conda environment.

```bash
conda run -n p12 python -m flora_ocr.ocr.liteparse --vol 60
conda run -n p12 python -m flora_ocr.pipeline.translate --vol 11
conda run -n p12 python -m flora_ocr.pipeline.build_key_data --source ocr_output/vol11_paddle/text_en.md --output floras/flore_du_gabon/vol11_myrtaceae.keys.json
conda run -n p12 streamlit run src/flora_ocr/app/key_app.py
```

Use `ds_ocr` only for `flora_ocr.ocr.paddle`. If you package the project, `pyproject.toml` exposes CLI entry points such as `flora-build-keys`.

## Coding Style & Naming Conventions
Follow the existing Python style: 4-space indentation, type hints where practical, `snake_case` for functions/modules, and short, factual docstrings. Keep new modules under `src/flora_ocr/` focused on one pipeline stage. Name flora configs and outputs predictably: `floras/<flora>/flora.toml`, `patches_vol11.json`, `vol11_myrtaceae.keys.json`. Prefer `pathlib.Path`, keep repo-root-relative paths configurable, and avoid hardcoding machine-specific paths outside flora config.

## Testing Guidelines
There is no formal `tests/` suite yet. Verify changes by running the affected module on a small real example and checking outputs in `ocr_output/` or `floras/<name>/`. For app changes, start Streamlit and confirm the new `*.keys.json` is discovered. If you add reusable parsing logic, add a focused `tests/` module alongside the change.

## Commit & Pull Request Guidelines
History is currently minimal, so keep commits short and imperative, like `Add flora config loader` or `Fix key parsing for vol60`. Keep generated bulk data out of commits unless it is the intentional deliverable. PRs should state the affected flora/volume, commands used for verification, expected output paths, and screenshots only when the Streamlit UI changes.
