# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "marker-pdf",
#   "pillow",
# ]
# ///

import argparse
import json
import pathlib
import re
import sys
import time
import traceback
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PDF_DIR = pathlib.Path("/mnt/e/FloreDuGabon")
OUT_DIR = pathlib.Path("/mnt/e/FloreDuGabon/ocr_output")
LANGUAGES = ["French"]
VOL_PATTERN = re.compile(r"FdG vol\. (\d+(?:bis)?)\s+OK(?:-\d+)?\.pdf")


# ---------------------------------------------------------------------------
# Marker API wrapper (v2 with v1 fallback)
# ---------------------------------------------------------------------------
def run_marker(pdf_path: pathlib.Path):
    """Convert PDF to markdown using marker-pdf.

    Returns (markdown_text: str, images: dict[str, PIL.Image])
    Tries marker v2+ API first, falls back to v1.
    """
    try:
        from marker.converters.pdf import PdfConverter
        from marker.config.parser import ConfigParser
        from marker.models import create_model_dict

        config = ConfigParser({"languages": LANGUAGES, "output_format": "markdown"})
        models = create_model_dict()
        converter = PdfConverter(
            config=config.generate_config_dict(),
            artifact_dict=models,
        )
        rendered = converter(str(pdf_path))
        return rendered.markdown, rendered.images or {}

    except ImportError:
        pass

    try:
        from marker.convert import convert_single_pdf
        from marker.models import load_all_models

        models = load_all_models()
        result = convert_single_pdf(str(pdf_path), models, langs=LANGUAGES)
        markdown = result[0]
        images = result[1] if len(result) >= 2 else {}
        return markdown, images or {}

    except ImportError:
        pass

    raise RuntimeError(
        "marker-pdf not found or incompatible version. "
        "Install with: uv add marker-pdf"
    )


# ---------------------------------------------------------------------------
# Volume discovery
# ---------------------------------------------------------------------------
def _label_sort_key(label: str):
    """Sort key so that '5bis' sorts after '5' and before '6'."""
    if label.endswith("bis"):
        return (int(label[:-3]), 1)
    return (int(label), 0)


def discover_volumes() -> list[dict]:
    """Scan PDF_DIR and return sorted list of volume dicts."""
    vols = []
    for pdf in PDF_DIR.glob("FdG vol. *.pdf"):
        m = VOL_PATTERN.match(pdf.name)
        if m:
            label = m.group(1)
            vols.append({
                "label": label,
                "pdf_path": pdf,
                "pdf_filename": pdf.name,
            })
    vols.sort(key=lambda v: _label_sort_key(v["label"]))
    return vols


def _normalize_label(raw: str, known_labels: list[str]) -> str | None:
    """Resolve user input ('1', '01', '5bis') to a known label."""
    if raw in known_labels:
        return raw
    if raw.isdigit():
        padded = f"{int(raw):02d}"
        if padded in known_labels:
            return padded
    return None


def is_already_processed(label: str) -> bool:
    return (OUT_DIR / f"vol{label}" / "text.md").exists()


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------
def process_volume(vol: dict) -> bool:
    label = vol["label"]
    pdf_path = vol["pdf_path"]
    vol_dir = OUT_DIR / f"vol{label}"
    fig_dir = vol_dir / "figures"

    vol_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(exist_ok=True)

    print(f"\n[vol{label}] Processing {pdf_path.name} ...")
    t0 = time.monotonic()

    try:
        markdown, images = run_marker(pdf_path)
    except Exception as exc:
        elapsed = time.monotonic() - t0
        error_log = vol_dir / "error.log"
        error_log.write_text(
            f"Processing failed after {elapsed:.1f}s\n\n" + traceback.format_exc()
        )
        print(f"[vol{label}] FAILED: {exc}  (see {error_log})")
        return False

    # Save figures
    figure_names = []
    for i, (_, img) in enumerate(images.items()):
        out_name = f"image_{i}.png"
        img.save(fig_dir / out_name, "PNG")
        figure_names.append(out_name)

    elapsed = time.monotonic() - t0

    # Write metadata first (text.md is the sentinel — written last)
    metadata = {
        "vol_label": label,
        "pdf_filename": vol["pdf_filename"],
        "processing_time_seconds": round(elapsed, 2),
        "figure_count": len(figure_names),
        "markdown_char_count": len(markdown),
        "figures": figure_names,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    (vol_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Write text.md last — its presence marks the volume as complete
    (vol_dir / "text.md").write_text(markdown, encoding="utf-8")

    print(
        f"[vol{label}] Done in {elapsed:.1f}s — "
        f"{len(markdown):,} chars, {len(figure_names)} figures"
    )
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="OCR Flore du Gabon PDF volumes with marker-pdf.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run ocr_with_marker.py --vol 1\n"
            "  uv run ocr_with_marker.py --all\n"
            "  uv run ocr_with_marker.py --all --start-from 20\n"
            "  uv run ocr_with_marker.py --vol 18 --force"
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--vol", metavar="LABEL",
        help="Process a single volume (e.g. 1, 01, 5bis)",
    )
    mode.add_argument(
        "--all", action="store_true",
        help="Process all volumes, skipping already-completed ones",
    )
    parser.add_argument(
        "--start-from", metavar="LABEL",
        help="With --all: skip volumes before this label (for resuming)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Reprocess even if text.md already exists",
    )
    args = parser.parse_args()

    volumes = discover_volumes()
    known_labels = [v["label"] for v in volumes]

    if args.vol:
        label = _normalize_label(args.vol, known_labels)
        if label is None:
            print(f"Error: volume '{args.vol}' not found. Known labels: {', '.join(known_labels)}")
            sys.exit(1)
        vol = next(v for v in volumes if v["label"] == label)
        if is_already_processed(label) and not args.force:
            print(f"[vol{label}] Already processed. Use --force to reprocess.")
            sys.exit(0)
        ok = process_volume(vol)
        sys.exit(0 if ok else 1)

    # --all mode
    start_label = None
    if args.start_from:
        start_label = _normalize_label(args.start_from, known_labels)
        if start_label is None:
            print(f"Error: --start-from label '{args.start_from}' not found.")
            sys.exit(1)

    # Pre-build the work list so we know the total upfront
    skipped_before = 0   # due to --start-from
    skipped_done = 0     # already processed
    work_list = []
    in_range = start_label is None
    for vol in volumes:
        label = vol["label"]
        if not in_range:
            if label == start_label:
                in_range = True
            else:
                skipped_before += 1
                continue
        if is_already_processed(label) and not args.force:
            skipped_done += 1
            continue
        work_list.append(vol)

    total = len(work_list)
    skipped = skipped_before + skipped_done
    print(f"\n--- Batch: {total} to process, {skipped} skipping "
          f"({skipped_before} before start, {skipped_done} already done) ---\n")

    processed = 0
    failed = []
    durations = []
    t_batch = time.monotonic()

    for idx, vol in enumerate(work_list, 1):
        label = vol["label"]
        print(f"[{idx}/{total}] vol{label} — {vol['pdf_filename']}")
        t0 = time.monotonic()
        ok = process_volume(vol)
        durations.append(time.monotonic() - t0)

        if ok:
            processed += 1
        else:
            failed.append(label)

        avg = sum(durations) / len(durations)
        remaining = avg * (total - idx)
        print(f"        Batch: {idx}/{total} done | "
              f"Elapsed: {_fmt_time(time.monotonic() - t_batch)} | "
              f"Est. remaining: ~{_fmt_time(remaining)}")

    print(f"\n--- Batch complete ---")
    print(f"  Processed: {processed}")
    print(f"  Skipped:   {skipped}")
    print(f"  Failed:    {len(failed)}")
    if failed:
        print(f"  Failed volumes: {', '.join(failed)}")
        print(f"  Re-run: conda run -n p12 python ocr_with_marker.py --vol {failed[0]} --force")


if __name__ == "__main__":
    main()
