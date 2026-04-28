"""OCR Flore du Gabon PDF volumes with MinerU-Diffusion-V1-0320-2.5B.

Diffusion-based vision-language OCR model: render each PDF page to an image,
pass it through the model, collect text.  ~3× faster than autoregressive VLMs.

Produces:
  ocr_output/vol{LABEL}_mineru_diff/
    text.md          full markdown (page-by-page output)
    figures/         botanical illustrations extracted directly from PDF
    metadata.json    timing, page count, truncated pages, figure list

Usage:
  conda run -n p12 python ocr_with_mineru_diffusion.py --vol 1
  conda run -n p12 python ocr_with_mineru_diffusion.py --all
  conda run -n p12 python ocr_with_mineru_diffusion.py --all --start-from 2
  conda run -n p12 python ocr_with_mineru_diffusion.py --vol 1 --force
  conda run -n p12 python ocr_with_mineru_diffusion.py --vol 1 --gen-length 4096
"""

import argparse
import json
import pathlib
import queue
import re
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timezone
from io import BytesIO

import fitz  # pymupdf
import torch
from PIL import Image

# ---------------------------------------------------------------------------
# Optional acceleration libraries — may not have Python 3.14 wheels yet
# ---------------------------------------------------------------------------
try:
    import flash_attn  # noqa: F401
    _FLASH_ATTN = True
except ImportError:
    _FLASH_ATTN = False

try:
    import triton       # noqa: F401
    import liger_kernel  # noqa: F401
    _TRITON_LIGER = True
except ImportError:
    _TRITON_LIGER = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PDF_DIR     = pathlib.Path("/mnt/e/FloreDuGabon")
OUT_DIR     = pathlib.Path("/mnt/e/FloreDuGabon/ocr_output")
MODEL_ID    = "opendatalab/MinerU-Diffusion-V1-0320-2.5B"
VOL_PATTERN = re.compile(r"FdG vol\. (\d+(?:bis)?)\s+OK(?:-\d+)?\.pdf")
SUFFIX      = "mineru_diff"
MIN_FIG_PX  = 150   # skip embedded images smaller than this in either dimension
BLOCK_LENGTH = 32   # diffusion block size — gen_length must be a multiple


# ---------------------------------------------------------------------------
# Volume discovery
# ---------------------------------------------------------------------------
def _label_sort_key(label: str):
    if label.endswith("bis"):
        return (int(label[:-3]), 1)
    return (int(label), 0)


def discover_volumes() -> list[dict]:
    vols = []
    for pdf in PDF_DIR.glob("FdG vol. *.pdf"):
        m = VOL_PATTERN.match(pdf.name)
        if m:
            label = m.group(1)
            vols.append({"label": label, "pdf_path": pdf, "pdf_filename": pdf.name})
    vols.sort(key=lambda v: _label_sort_key(v["label"]))
    return vols


def _normalize_label(raw: str, known_labels: list[str]) -> str | None:
    if raw in known_labels:
        return raw
    if raw.isdigit():
        padded = f"{int(raw):02d}"
        if padded in known_labels:
            return padded
    return None


def is_already_processed(label: str) -> bool:
    return (OUT_DIR / f"vol{label}_{SUFFIX}" / "text.md").exists()


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------
def load_model(model_id: str = MODEL_ID):
    """Load tokenizer, processor, and model onto GPU.

    Returns (tokenizer, processor, model).
    Tries flash_attention_2 first; falls back to eager.
    """
    from transformers import AutoModel, AutoProcessor, AutoTokenizer

    print(f"  flash-attn:       {'available' if _FLASH_ATTN else 'NOT available (slower attention)'}")
    print(f"  triton/liger:     {'available' if _TRITON_LIGER else 'NOT available (slower custom ops)'}")

    print("  Loading tokenizer …")
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)

    # use_fast=False is required: the fast tokenizer skips the custom chat
    # template logic baked into this model's trust_remote_code processor.
    print("  Loading processor …")
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True, use_fast=False)

    attn_impls = ["flash_attention_2"] if _FLASH_ATTN else []
    attn_impls.append("eager")

    for attn in attn_impls:
        try:
            print(f"  Loading model (attn={attn}) …")
            model = AutoModel.from_pretrained(
                model_id,
                trust_remote_code=True,
                torch_dtype=torch.bfloat16,
                low_cpu_mem_usage=True,
                attn_implementation=attn,
            )
            model = model.eval().to("cuda")
            print(f"  Model ready on {next(model.parameters()).device}  (attn={attn})")
            # NOTE: do NOT torch.compile — diffusion decoding uses dynamic
            # control flow that is incompatible with reduce-overhead mode.
            return tokenizer, processor, model
        except Exception as e:
            if attn == "flash_attention_2":
                print(f"  flash_attention_2 failed ({type(e).__name__}: {e}), trying eager …")
            else:
                raise


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------
def render_page_to_pil(doc: fitz.Document, page_idx: int, dpi: int) -> Image.Image:
    """Render one PDF page to an in-memory PIL Image (RGB). No disk I/O."""
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = doc[page_idx].get_pixmap(matrix=mat)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


# ---------------------------------------------------------------------------
# Per-page OCR inference
# ---------------------------------------------------------------------------
def ocr_page(
    page_image: Image.Image,
    tokenizer,
    processor,
    model,
    gen_length: int,
    denoising_steps: int,
) -> tuple[str, bool]:
    """Run MinerU-Diffusion inference on one page image.

    Returns (text, truncated) where truncated=True if gen_length was hit.
    """
    messages = [
        {
            "role": "system",
            "content": [{"type": "text", "text": "You are a helpful assistant."}],
        },
        {
            "role": "user",
            "content": [
                {"type": "image", "image": page_image},
                {"type": "text", "text": "\nText Recognition:"},
            ],
        },
    ]

    prompt_text = processor.apply_chat_template(messages, add_generation_prompt=True)
    if isinstance(prompt_text, tuple):
        prompt_text = prompt_text[0]

    # Try passing PIL Image directly; fall back to a temp PNG file if the
    # processor's custom code only accepts path strings.
    _tmp_file = None
    images_arg = [page_image]
    try:
        inputs = processor(
            images=images_arg,
            text=prompt_text,
            truncation=True,
            max_length=4096,
            return_tensors="pt",
        )
    except Exception:
        # Fallback: write PIL image to a temp file and pass the path
        _tmp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        page_image.save(_tmp_file.name)
        _tmp_file.close()
        images_arg = [_tmp_file.name]
        inputs = processor(
            images=images_arg,
            text=prompt_text,
            truncation=True,
            max_length=4096,
            return_tensors="pt",
        )

    try:
        mask_token_id = tokenizer.convert_tokens_to_ids("<|MASK|>")
        assert mask_token_id != tokenizer.unk_token_id, \
            "<|MASK|> not found in tokenizer vocab — model may not be fully downloaded"

        input_ids = inputs["input_ids"].to(torch.long).to("cuda")
        pixel_values = inputs["pixel_values"].to(torch.bfloat16).to("cuda")
        image_grid_thw = inputs.get("image_grid_thw")
        if image_grid_thw is not None:
            image_grid_thw = image_grid_thw.to(torch.long).to("cuda")

        with torch.no_grad():
            generate_outputs = model.generate(
                pixel_values=pixel_values,
                image_grid_thw=image_grid_thw,
                input_ids=input_ids,
                mask_token_id=mask_token_id,
                denoising_steps=denoising_steps,
                gen_length=gen_length,
                block_length=BLOCK_LENGTH,
                temperature=1.0,
                remasking_strategy="low_confidence_dynamic",
                dynamic_threshold=0.95,
                tokenizer=tokenizer,
                stopping_criteria=["<|endoftext|>", "<|im_end|>"],
            )

        output_ids = generate_outputs[0] if isinstance(generate_outputs, tuple) else generate_outputs
        raw = tokenizer.decode(output_ids[0], skip_special_tokens=False)

        for stop in ("<|endoftext|>", "<|im_end|>"):
            raw = raw.split(stop, 1)[0]

        text = raw.strip()

        # Truncation: if output token count is near gen_length, the model
        # likely hit the cap and output was cut short.
        truncated = len(tokenizer.encode(text)) >= gen_length - 10

    finally:
        if _tmp_file is not None:
            pathlib.Path(_tmp_file.name).unlink(missing_ok=True)

    return text, truncated


# ---------------------------------------------------------------------------
# Figure extraction
# ---------------------------------------------------------------------------
def extract_figures(pdf_path: pathlib.Path, fig_dir: pathlib.Path) -> list[dict]:
    """Extract embedded raster images from the PDF.

    Deduplicates by xref; skips images smaller than MIN_FIG_PX in either dim.
    """
    doc = fitz.open(str(pdf_path))
    figures = []
    seen: set[int] = set()

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
# Render worker
# ---------------------------------------------------------------------------

def _render_pages_worker(doc: fitz.Document, n_pages: int, dpi: int,
                         out_queue: queue.Queue) -> None:
    """Background thread: render PDF pages to PIL Images and push to a queue.

    Produces (page_idx, PIL.Image) tuples; puts None as sentinel when done.
    Queue is bounded so the thread stays at most a few pages ahead of the
    OCR loop, keeping RAM usage under control.
    """
    for page_idx in range(n_pages):
        img = render_page_to_pil(doc, page_idx, dpi)
        out_queue.put((page_idx, img))
    out_queue.put(None)


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------
def process_volume(
    pdf_path: pathlib.Path,
    vol_label: str,
    dpi: int,
    gen_length: int,
    denoising_steps: int,
    tokenizer,
    processor,
    model,
) -> bool:
    vol_dir = OUT_DIR / f"vol{vol_label}_{SUFFIX}"
    fig_dir = vol_dir / "figures"
    vol_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(exist_ok=True)

    print(f"\n[vol{vol_label}] Processing {pdf_path.name} …")
    t0 = time.monotonic()

    # --- Extract embedded figures (fast, CPU) ---
    print("  Extracting embedded figures …")
    try:
        figures = extract_figures(pdf_path, fig_dir)
    except Exception as exc:
        print(f"  WARNING: figure extraction failed: {exc}")
        figures = []
    print(f"  {len(figures)} figures saved")

    # --- Open PDF and OCR each page ---
    doc = fitz.open(str(pdf_path))
    n_pages = len(doc)
    print(f"  {n_pages} pages at {dpi} DPI, gen_length={gen_length}, steps={denoising_steps}\n")

    page_texts: list[str] = []
    truncated_pages: list[int] = []
    durations: list[float] = []

    # Start background render thread.  While the GPU runs inference on page N
    # the CPU is already rendering page N+1 into a PIL Image.  Queue size of 3
    # keeps RAM bounded while ensuring the main thread never waits for rendering.
    render_queue: queue.Queue = queue.Queue(maxsize=3)
    render_thread = threading.Thread(
        target=_render_pages_worker,
        args=(doc, n_pages, dpi, render_queue),
        daemon=True,
    )
    render_thread.start()

    try:
        while True:
            item = render_queue.get()
            if item is None:
                break
            page_idx, page_image = item
            page_num = page_idx + 1

            t_page = time.monotonic()
            try:
                text, truncated = ocr_page(
                    page_image, tokenizer, processor, model, gen_length, denoising_steps
                )
                if truncated:
                    truncated_pages.append(page_num)
            except Exception as exc:
                text = f"<!-- OCR FAILED page {page_num}: {exc} -->"
                truncated = False

            dt = time.monotonic() - t_page
            durations.append(dt)
            page_texts.append(f"<!-- page {page_num} -->\n\n{text}")

            avg = sum(durations) / len(durations)
            eta = avg * (n_pages - page_num)
            trunc_note = " [TRUNCATED]" if truncated else ""
            print(
                f"  [{page_num}/{n_pages}] {len(text):,} chars ({dt:.1f}s){trunc_note}"
                f"  ETA ~{_fmt_time(eta)}   ",
                end="\r",
            )

            # Periodically free VRAM fragmentation
            if page_num % 20 == 0:
                torch.cuda.empty_cache()
    finally:
        render_thread.join()
        doc.close()

    print()  # clear \r line

    elapsed = time.monotonic() - t0
    full_markdown = "\n\n---\n\n".join(page_texts)

    if truncated_pages:
        print(f"  WARNING: {len(truncated_pages)} pages hit gen_length={gen_length} "
              f"(may be truncated): pages {truncated_pages[:10]}"
              + (" …" if len(truncated_pages) > 10 else ""))
        print(f"  Re-run with --gen-length {gen_length * 2} to capture full text.")

    metadata = {
        "vol_label": vol_label,
        "pdf_filename": pdf_path.name,
        "model": MODEL_ID,
        "dpi": dpi,
        "denoising_steps": denoising_steps,
        "gen_length": gen_length,
        "page_count": n_pages,
        "figure_count": len(figures),
        "truncated_pages": truncated_pages,
        "markdown_char_count": len(full_markdown),
        "processing_time_seconds": round(elapsed, 2),
        "figures": figures,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    (vol_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # text.md written last — its presence marks the run as complete (sentinel)
    (vol_dir / "text.md").write_text(full_markdown, encoding="utf-8")

    print(
        f"[vol{vol_label}] Done in {_fmt_time(elapsed)} — "
        f"{len(full_markdown):,} chars, {len(figures)} figures"
        + (f", {len(truncated_pages)} truncated pages" if truncated_pages else "")
    )
    print(f"  Output: {vol_dir}")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="OCR Flore du Gabon PDF volumes with MinerU-Diffusion-V1-0320-2.5B.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  conda run -n p12 python ocr_with_mineru_diffusion.py --vol 1\n"
            "  conda run -n p12 python ocr_with_mineru_diffusion.py --all\n"
            "  conda run -n p12 python ocr_with_mineru_diffusion.py --all --start-from 5\n"
            "  conda run -n p12 python ocr_with_mineru_diffusion.py --vol 1 --force\n"
            "  conda run -n p12 python ocr_with_mineru_diffusion.py --vol 1 --gen-length 4096\n"
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--vol", metavar="LABEL", help="Process a single volume (e.g. 1, 01, 5bis)")
    mode.add_argument("--all", action="store_true", help="Process all volumes")
    parser.add_argument("--start-from", metavar="LABEL", help="With --all: resume from this label")
    parser.add_argument("--force", action="store_true", help="Reprocess even if text.md exists")
    parser.add_argument(
        "--dpi", type=int, default=200,
        help="Page rendering DPI (default 200; botanical pages have small text)",
    )
    parser.add_argument(
        "--gen-length", type=int, default=2048,
        help=(
            "Max tokens generated per page (default 2048; increase if output is truncated). "
            f"Must be a multiple of {BLOCK_LENGTH} — will be rounded up automatically."
        ),
    )
    parser.add_argument(
        "--denoising-steps", type=int, default=32,
        help="Diffusion denoising steps (default 32; fewer = faster, lower quality)",
    )
    args = parser.parse_args()

    # Validate / round gen_length to nearest block_length multiple
    gen_length = args.gen_length
    if gen_length % BLOCK_LENGTH != 0:
        gen_length = ((gen_length // BLOCK_LENGTH) + 1) * BLOCK_LENGTH
        print(f"Note: gen_length rounded up to {gen_length} (must be multiple of {BLOCK_LENGTH})")

    volumes = discover_volumes()
    known_labels = [v["label"] for v in volumes]

    if not volumes:
        print(f"Error: no PDFs found in {PDF_DIR}")
        sys.exit(1)

    # Build work list
    if args.vol:
        label = _normalize_label(args.vol, known_labels)
        if label is None:
            print(f"Error: volume '{args.vol}' not found. Known: {', '.join(known_labels)}")
            sys.exit(1)
        if is_already_processed(label) and not args.force:
            print(f"[vol{label}] Already processed. Use --force to reprocess.")
            sys.exit(0)
        work_list = [next(v for v in volumes if v["label"] == label)]
    else:
        # --all mode
        start_label = None
        if args.start_from:
            start_label = _normalize_label(args.start_from, known_labels)
            if start_label is None:
                print(f"Error: --start-from label '{args.start_from}' not found.")
                sys.exit(1)

        skipped_before = 0
        skipped_done = 0
        work_list = []
        in_range = start_label is None
        for vol in volumes:
            lbl = vol["label"]
            if not in_range:
                if lbl == start_label:
                    in_range = True
                else:
                    skipped_before += 1
                    continue
            if is_already_processed(lbl) and not args.force:
                skipped_done += 1
                continue
            work_list.append(vol)

        total = len(work_list)
        skipped = skipped_before + skipped_done
        print(f"\n--- Batch: {total} to process, {skipped} skipping "
              f"({skipped_before} before start, {skipped_done} already done) ---\n")

    if not work_list:
        print("Nothing to do.")
        sys.exit(0)

    # Load model once for all volumes
    print(f"Loading {MODEL_ID} …")
    try:
        tokenizer, processor, model = load_model()
    except Exception as exc:
        print(f"FATAL: could not load {MODEL_ID}: {exc}")
        traceback.print_exc()
        sys.exit(1)

    # Process
    processed = 0
    failed = []
    durations = []
    t_batch = time.monotonic()
    total = len(work_list)

    for idx, vol in enumerate(work_list, 1):
        lbl = vol["label"]
        print(f"\n[{idx}/{total}] vol{lbl} — {vol['pdf_filename']}")
        t0 = time.monotonic()
        try:
            ok = process_volume(
                vol["pdf_path"], lbl,
                dpi=args.dpi,
                gen_length=gen_length,
                denoising_steps=args.denoising_steps,
                tokenizer=tokenizer,
                processor=processor,
                model=model,
            )
        except Exception as exc:
            print(f"[vol{lbl}] FAILED: {exc}")
            traceback.print_exc()
            ok = False

        durations.append(time.monotonic() - t0)
        if ok:
            processed += 1
        else:
            failed.append(lbl)

        avg = sum(durations) / len(durations)
        remaining = avg * (total - idx)
        print(
            f"  Batch: {idx}/{total} done | "
            f"Elapsed: {_fmt_time(time.monotonic() - t_batch)} | "
            f"Est. remaining: ~{_fmt_time(remaining)}"
        )

    print("\n--- Batch complete ---")
    print(f"  Processed: {processed}")
    print(f"  Failed:    {len(failed)}")
    if failed:
        print(f"  Failed volumes: {', '.join(failed)}")
        print(f"  Re-run: conda run -n p12 python ocr_with_mineru_diffusion.py "
              f"--vol {failed[0]} --force")


if __name__ == "__main__":
    main()
