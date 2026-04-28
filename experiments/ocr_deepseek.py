"""
OCR a Flore du Gabon PDF volume with DeepSeek-OCR-2.

Produces:
  ocr_output/vol{LABEL}_deepseek/
    text.md          full markdown (page-by-page DeepSeek output)
    figures/         botanical illustrations extracted directly from PDF
    metadata.json    timing, page count, figure list

Run:
  conda run -n p12 python ocr_deepseek.py --vol 18
  conda run -n p12 python ocr_deepseek.py --pdf /mnt/e/FloreDuGabon/"FdG vol. 1 OK.pdf" --vol 1
"""

import argparse
import json
import pathlib
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from io import BytesIO

import fitz  # pymupdf
import torch
from PIL import Image

PDF_DIR = pathlib.Path("/mnt/e/FloreDuGabon")
OUT_DIR = pathlib.Path("/mnt/e/FloreDuGabon/ocr_output")
MODEL_NAME = "deepseek-ai/DeepSeek-OCR-2"

PROMPT = "<image>\n<|grounding|>Convert the document to markdown. "
MIN_FIG_PX = 150  # skip images smaller than this in either dimension


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------
def load_model():
    """Load DeepSeek-OCR-2 tokenizer and model onto GPU."""
    from transformers import AutoModel, AutoTokenizer

    print(f"Loading tokenizer …")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id

    for attn in ("flash_attention_2", "eager"):
        try:
            print(f"Loading model (attn_impl={attn}) …")
            model = AutoModel.from_pretrained(
                MODEL_NAME,
                _attn_implementation=attn,
                trust_remote_code=True,
                use_safetensors=True,
            )
            model = model.eval().cuda().to(torch.bfloat16)
            if model.config.pad_token_id is None:
                model.config.pad_token_id = tokenizer.pad_token_id
            device = next(model.parameters()).device
            try:
                model = torch.compile(model, mode="reduce-overhead")
                print(f"Model compiled (torch.compile reduce-overhead)")
            except Exception as ce:
                print(f"  torch.compile skipped ({ce})")
            print(f"Model ready on {device}  (attn={attn})")
            return tokenizer, model
        except Exception as e:
            if attn == "flash_attention_2":
                print(f"  flash_attention_2 unavailable ({type(e).__name__}), trying eager …")
            else:
                raise


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------
def render_pages(pdf_path: pathlib.Path, tmp_dir: pathlib.Path, dpi: int = 150):
    """Render every page to a PNG file. Returns list of (page_num, png_path)."""
    doc = fitz.open(str(pdf_path))
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pages = []
    for i in range(len(doc)):
        png_path = tmp_dir / f"page_{i + 1:04d}.png"
        doc[i].get_pixmap(matrix=mat).save(str(png_path))
        pages.append((i + 1, png_path))
    doc.close()
    return pages


# ---------------------------------------------------------------------------
# Figure extraction
# ---------------------------------------------------------------------------
def extract_figures(pdf_path: pathlib.Path, fig_dir: pathlib.Path) -> list[dict]:
    """Extract embedded raster images from the PDF as botanical figures.

    Skips images smaller than MIN_FIG_PX in either dimension (decorations, borders).
    Deduplicates by xref so the same illustration embedded on multiple pages is
    only saved once.
    """
    doc = fitz.open(str(pdf_path))
    figures = []
    seen = set()

    for page_num, page in enumerate(doc, start=1):
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)

            base = doc.extract_image(xref)
            w, h = base["width"], base["height"]
            if w < MIN_FIG_PX or h < MIN_FIG_PX:
                continue

            idx = len(figures)
            fname = f"fig_{idx:03d}_p{page_num:04d}.png"
            raw = base["image"]
            ext = base["ext"].lower()

            if ext == "png":
                (fig_dir / fname).write_bytes(raw)
            else:
                pil = Image.open(BytesIO(raw)).convert("RGB")
                pil.save(str(fig_dir / fname), "PNG")

            figures.append({"filename": fname, "page": page_num, "width": w, "height": h})

    doc.close()
    return figures


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------
def process_volume(pdf_path: pathlib.Path, vol_label: str, dpi: int):
    vol_dir = OUT_DIR / f"vol{vol_label}_deepseek"
    fig_dir = vol_dir / "figures"
    vol_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(exist_ok=True)

    t_total = time.monotonic()

    # --- Step 1: load model ---
    try:
        tokenizer, model = load_model()
    except Exception as exc:
        print(f"FATAL – could not load DeepSeek-OCR-2: {exc}")
        traceback.print_exc()
        sys.exit(1)

    # --- Step 2: extract botanical figures (fast, CPU, no temp files) ---
    print("\nExtracting embedded figures from PDF …")
    figures = extract_figures(pdf_path, fig_dir)
    print(f"  {len(figures)} figures saved to {fig_dir}")

    # --- Step 3: render pages + OCR ---
    print(f"\nRendering pages at {dpi} DPI …")
    with tempfile.TemporaryDirectory(prefix="fdg_ocr_") as tmp:
        tmp_dir = pathlib.Path(tmp)
        pages = render_pages(pdf_path, tmp_dir, dpi=dpi)
        n = len(pages)
        print(f"  {n} pages rendered\n")

        page_texts: list[str] = []
        t_batch = time.monotonic()
        durations: list[float] = []

        for i, (page_num, png_path) in enumerate(pages, 1):
            t0 = time.monotonic()
            try:
                with torch.no_grad():
                    res = model.infer(
                        tokenizer,
                        prompt=PROMPT,
                        image_file=str(png_path),
                        output_path=str(vol_dir),
                        base_size=1024,
                        image_size=768,
                        crop_mode=False,
                        save_results=False,
                    )
                text = res if isinstance(res, str) else (res.get("text", str(res)) if isinstance(res, dict) else str(res))
            except Exception as exc:
                text = f"<!-- OCR FAILED page {page_num}: {exc} -->"

            dt = time.monotonic() - t0
            durations.append(dt)
            page_texts.append(f"<!-- page {page_num} -->\n\n{text.strip()}")

            avg = sum(durations) / len(durations)
            eta = avg * (n - i)
            m, s = divmod(int(eta), 60)
            h, m = divmod(m, 60)
            eta_str = f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"
            print(f"  [{i}/{n}] page {page_num}: {len(text):,} chars ({dt:.1f}s)  ETA ~{eta_str}   ", end="\r")

    print()  # clear \r line

    elapsed = time.monotonic() - t_total
    full_markdown = "\n\n---\n\n".join(page_texts)

    metadata = {
        "vol_label": vol_label,
        "pdf_filename": pdf_path.name,
        "model": MODEL_NAME,
        "dpi": dpi,
        "page_count": n,
        "figure_count": len(figures),
        "markdown_char_count": len(full_markdown),
        "processing_time_seconds": round(elapsed, 2),
        "figures": figures,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    (vol_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # text.md written last — its presence marks the run as complete
    (vol_dir / "text.md").write_text(full_markdown, encoding="utf-8")

    m2, s2 = divmod(int(elapsed), 60)
    h2, m2 = divmod(m2, 60)
    t_str = f"{h2}h {m2:02d}m" if h2 else f"{m2}m {s2:02d}s"
    print(f"\nDone in {t_str} — {len(full_markdown):,} chars, {len(figures)} figures")
    print(f"Output: {vol_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="OCR a Flore du Gabon PDF with DeepSeek-OCR-2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  conda run -n p12 python ocr_deepseek.py --vol 18\n'
            '  conda run -n p12 python ocr_deepseek.py --pdf "/mnt/e/FloreDuGabon/FdG vol. 1 OK.pdf" --vol 1\n'
        ),
    )
    parser.add_argument(
        "--vol", default="16",
        help="Volume label — used for the output directory name (default: 18)",
    )
    parser.add_argument(
        "--pdf",
        help='Path to PDF (default: FdG vol. {VOL} OK.pdf in /mnt/e/FloreDuGabon)',
    )
    parser.add_argument(
        "--dpi", type=int, default=150,
        help="Page rendering DPI (default 150; use 200 for higher quality)",
    )
    args = parser.parse_args()

    if args.pdf:
        pdf_path = pathlib.Path(args.pdf)
    else:
        # Try to find it automatically
        candidates = list(PDF_DIR.glob(f"FdG vol. {args.vol} OK*.pdf"))
        if not candidates:
            candidates = list(PDF_DIR.glob(f"FdG vol. {int(args.vol):02d} OK*.pdf"))
        if not candidates:
            print(f"Error: could not find PDF for vol {args.vol} in {PDF_DIR}")
            print("Specify with --pdf.")
            sys.exit(1)
        pdf_path = candidates[0]

    if not pdf_path.exists():
        print(f"Error: PDF not found: {pdf_path}")
        sys.exit(1)

    print(f"PDF: {pdf_path}")
    process_volume(pdf_path, args.vol, args.dpi)


if __name__ == "__main__":
    main()
