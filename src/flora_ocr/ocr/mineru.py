"""OCR Flore du Gabon PDF volumes with MinerU (mineru 3.x).

Usage:
    conda run -n p12 python ocr_with_mineru.py --vol 1
    conda run -n p12 python ocr_with_mineru.py --all
    conda run -n p12 python ocr_with_mineru.py --all --start-from 2
    conda run -n p12 python ocr_with_mineru.py --vol 1 --force
    conda run -n p12 python ocr_with_mineru.py --vol 1 --backend pipeline
    conda run -n p12 python ocr_with_mineru.py --vol 1 --backend vlm-auto-engine
    conda run -n p12 python ocr_with_mineru.py --vol 1 --backend hybrid-auto-engine
"""

import argparse
import json
import pathlib
import re
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Figure parsing helpers
# ---------------------------------------------------------------------------

# Caption line patterns:
#   Scanned PDFs:      "PL.1.-", "Pl.2.—", "PL. 3. -", "PL.49．-", "PL.—"
#   Born-digital PDFs: "Planche 1. Species …", "Figure 1. Species …", "Fig. 1. …"
# Cross-references like "Planche 1(1-5)" or "Figure 3(D, E)" are NOT captions
# (they are in-text references and have parentheses directly after the number).
_CAPTION_RE = re.compile(
    r'^(?:'
    r'(?:PL|Pl|pl)\.?\s*\d*[\uff0e\u30fb\u00b7\.·]?[\s\-—–]'   # PL.N.- style
    r'|'
    r'(?:Planche|Figure|Fig\.)\s+\d+\s*\.'                         # Planche N. / Figure N.
    r')',
    re.IGNORECASE,
)
# Image reference written by MinerU: ![](images/<hash>.ext)
_IMG_REF_RE = re.compile(r'!\[\]\(images/([^)]+)\)')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
from flora_ocr.flora import load_flora, add_flora_arg

# Set by _apply_flora() at startup.
PDF_DIR: pathlib.Path | None = None
OUT_DIR: pathlib.Path | None = None
VOL_PATTERN: re.Pattern | None = None
PDF_GLOB: str = "*.pdf"


def _apply_flora(name: str) -> None:
    global PDF_DIR, OUT_DIR, VOL_PATTERN, PDF_GLOB
    f = load_flora(name)
    PDF_DIR = f.pdf_dir
    OUT_DIR = f.output_dir
    VOL_PATTERN = f.vol_pattern
    PDF_GLOB = f.pdf_glob

# Use 'ch' (Chinese/multilingual) model for OCR — it recognizes Latin script
# and its v4 weights are compatible with the pytorchocr loader.
# The 'fr'/'latin' lang uses v5 weights with an incompatible architecture.
LANG = None


# ---------------------------------------------------------------------------
# Volume discovery
# ---------------------------------------------------------------------------
def _label_sort_key(label: str):
    if label.endswith("bis"):
        return (int(label[:-3]), 1)
    return (int(label), 0)


def discover_volumes() -> list[dict]:
    vols = []
    for pdf in PDF_DIR.glob(PDF_GLOB):
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
    # Check both new family-named dirs and old-style vol{label}_mineru dir
    return (
        any((d / "text.md").exists() for d in OUT_DIR.glob(f"*_vol{label}_mineru"))
        or (OUT_DIR / f"vol{label}_mineru" / "text.md").exists()
    )


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


# ---------------------------------------------------------------------------
# MinerU processing
# ---------------------------------------------------------------------------
def run_mineru(pdf_path: pathlib.Path, mineru_tmp: pathlib.Path, backend: str = "hybrid-auto-engine") -> tuple[str, list[pathlib.Path]]:
    """Run MinerU on pdf_path, write to mineru_tmp, return (markdown, image_paths)."""
    from mineru.cli.common import do_parse
    from mineru.utils.enum_class import MakeMode

    # Keep loguru but filter to WARNING+ to reduce noise while still showing progress
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    pdf_bytes = pdf_path.read_bytes()
    pdf_stem = pdf_path.stem

    # parse_method="auto" lets MinerU decide OCR vs. text-extraction per page.
    # Output lands at: mineru_tmp / pdf_stem / "auto" / {pdf_stem}.md
    do_parse(
        output_dir=str(mineru_tmp),
        pdf_file_names=[pdf_stem],
        pdf_bytes_list=[pdf_bytes],
        p_lang_list=["ch"],     # ch model handles Latin/French script
        backend=backend,
        parse_method="auto",
        formula_enable=False,   # skip formula detection — not needed for botanical text
        table_enable=False,     # skip table detection — not needed
        f_draw_span_bbox=False,
        f_draw_layout_bbox=False,
        f_dump_middle_json=False,
        f_dump_model_output=False,
        f_dump_orig_pdf=False,
        f_dump_content_list=False,
        f_dump_md=True,
        f_make_md_mode=MakeMode.MM_MD,
    )

    # MinerU sanitizes the pdf stem and names the subdirectory after the backend
    # (e.g. hybrid-auto-engine → hybrid_auto, pipeline → auto), so search
    # recursively for the .md that has a sibling images/ directory.
    md_candidates = [
        p for p in mineru_tmp.rglob("*.md")
        if (p.parent / "images").is_dir()
    ]
    if not md_candidates:
        md_candidates = list(mineru_tmp.rglob("*.md"))
    if not md_candidates:
        raise FileNotFoundError(f"No markdown output found under {mineru_tmp}")
    md_file = md_candidates[0]
    md_dir = md_file.parent
    img_dir = md_dir / "images"

    markdown = md_file.read_text(encoding="utf-8")
    images = sorted(img_dir.glob("*.png")) + sorted(img_dir.glob("*.jpg")) if img_dir.exists() else []
    return markdown, images


def _strip_figure_blocks(markdown: str) -> str:
    """Remove captioned plate images from the main text.

    Only image blocks that are immediately followed by a recognised caption line
    (Planche N. / Figure N. / PL.N.-) are stripped and replaced by a
    '[Figure N — see figures.md]' placeholder.

    Uncaptioned images (e.g. key tables extracted as images, maps) are kept in
    text.md as-is so no content is silently discarded.  Orphan caption lines
    (caption without a preceding image) are still removed.

    The figure counter here must stay in sync with _build_figures_md, which
    likewise only counts captioned groups.
    """
    lines = markdown.splitlines(keepends=True)
    out: list[str] = []
    fig_num = 0
    img_buf: list[str] = []   # lines in current image block

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if re.match(r'!\[\]\(figures/([^)]+)\)', stripped):
            img_buf.append(line)
            i += 1
            continue

        if img_buf:
            if not stripped:
                # blank line between images — keep buffering
                img_buf.append(line)
                i += 1
                continue
            # Non-blank line after images
            if _CAPTION_RE.match(stripped):
                # Confirmed plate caption — strip images + caption, emit placeholder
                fig_num += 1
                i += 1   # drop caption line
                out.append(f"[Figure {fig_num} — see figures.md]\n")
            else:
                # No caption — keep the image references in text (might be a key
                # table, map, or diagram whose text content must not be lost)
                out.extend(img_buf)
                # Do NOT advance i — reprocess this line in next iteration
            img_buf = []
            continue

        # Orphan caption line (no preceding image) — strip it
        if _CAPTION_RE.match(stripped):
            i += 1
            continue

        out.append(line)
        i += 1

    # Flush any trailing image block — no caption found, so keep as-is
    if img_buf:
        out.extend(img_buf)

    return "".join(out)


def _build_figures_md(markdown: str) -> str:
    """Parse MinerU markdown for image+caption blocks and build figures.md.

    MinerU groups plate images together, followed by a 'PL.N.-' caption line.
    Each group (one or more images + caption) becomes one figures.md entry.
    Images at this point already use the rewritten 'figures/fig_NNN.ext' paths.
    """
    lines = markdown.splitlines()
    entries: list[dict] = []  # [{imgs: [path, ...], caption: str}]

    img_buf: list[str] = []   # images collected so far in current group

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        m = re.match(r'!\[\]\(figures/([^)]+)\)', line)
        if m:
            img_buf.append(m.group(1))
            i += 1
            continue

        # Caption line immediately after (or separated by blanks from) images
        if img_buf:
            if not line:
                i += 1
                continue   # skip blanks while waiting for caption
            if _CAPTION_RE.match(line):
                # Captioned plate — record in figures.md
                entries.append({"imgs": list(img_buf), "caption": line})
                img_buf = []
                i += 1
            else:
                # No caption — skip (kept in text.md by _strip_figure_blocks)
                img_buf = []
                # Don't advance i — reprocess this line
            continue

        i += 1

    # Flush any trailing images without caption — skip (not in figures.md)
    img_buf = []  # noqa: just discard

    if not entries:
        return "*(No figures detected)*\n"

    parts: list[str] = []
    for n, entry in enumerate(entries):
        fig_num = n + 1
        caption = entry["caption"]
        img_lines = "\n\n".join(
            f"![{caption}](figures/{img_path})" for img_path in entry["imgs"]
        )
        parts.append(
            f"## Figure {fig_num}\n\n"
            f"{img_lines}\n\n"
            f"*Caption:* {caption}\n\n"
            f"---"
        )

    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Family detection in flat markdown (MinerU output)
# ---------------------------------------------------------------------------

# Family heading: a #-prefixed line whose content is (only) a family name,
# either:
#   ALL-CAPS French (ANNONACEES) or Latin (ANNONACEAE)  — scanned PDFs
#   Title-case Latin (Ancistrocladaceae, Dilleniaceae)  — born-digital PDFs
# Optionally followed by a parenthetical species count.
_MINERU_FAM_RE = re.compile(
    r'^#{1,4}\s+'
    r'('
    r'[A-Z]{4,}C[EÉ][AEÉ][ES]S?'        # ALL-CAPS scanned: ANNONACEAE, ANNONACEES
    r'|'
    r'[A-Z][a-z]{3,}ceae'                 # Title-case born-digital: Ancistrocladaceae
    r')'
    r'\b(?:\s*\(.*)?$',
    re.MULTILINE,
)


def _normalize_family(raw: str) -> str:
    """Convert French/Latin all-caps family name to standard form.

    ANNONACEES → Annonaceae,  RUBIACEAE → Rubiaceae
    """
    name = re.sub(r'\s*\(.*', '', raw).strip().upper()
    # French -CEES/-CÉES and Latin -CEAE all end in C + two vowels + optional ES
    name = re.sub(r'C[EÉ][AEÉ][ES]S?$', 'ceae', name, flags=re.IGNORECASE)
    return name[0].upper() + name[1:].lower() if name else name


def _detect_families_md(markdown: str) -> list[tuple[str, int]]:
    """Return [(family_name, char_offset), …] for each family heading found."""
    families: list[tuple[str, int]] = []
    for m in _MINERU_FAM_RE.finditer(markdown):
        fam = _normalize_family(m.group(1))
        if not families or families[-1][0] != fam:
            families.append((fam, m.start()))
    return families


def _split_by_family(
    markdown: str,
    families: list[tuple[str, int]],
) -> dict[str, str]:
    """Split markdown into per-family chunks at the detected headings."""
    chunks: dict[str, str] = {}
    for i, (fam, start) in enumerate(families):
        end = families[i + 1][1] if i + 1 < len(families) else len(markdown)
        chunks[fam] = markdown[start:end]
    return chunks


def _write_family_dir(
    fam: str,
    vol_label: str,
    fam_markdown: str,          # markdown chunk for this family (refs already fixed)
    all_src_images: list[pathlib.Path],
    img_map: dict[str, str],    # hash → fig_NNN.ext (global numbering)
    pdf_filename: str,
    elapsed: float,
) -> pathlib.Path:
    """Write all outputs for one family to its output directory."""
    fam_dir = OUT_DIR / f"{fam}_vol{vol_label}_mineru"
    fig_dir = fam_dir / "figures"
    fam_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(exist_ok=True)

    # Find which figures are referenced in this family's markdown chunk
    refs = set(re.findall(r'!\[\]\(figures/(fig_[^)]+)\)', fam_markdown))
    figure_names = []
    for staged_img in all_src_images:   # staged_img already named fig_NNN.ext
        if staged_img.name in refs:
            shutil.copy2(staged_img, fig_dir / staged_img.name)
            figure_names.append(staged_img.name)

    # figures.md (built before stripping)
    figures_md = _build_figures_md(fam_markdown)
    (fam_dir / "figures.md").write_text(figures_md, encoding="utf-8")

    # Strip image/caption blocks from prose
    clean_md = _strip_figure_blocks(fam_markdown)

    # Indented key view
    try:
        from reformat_keys import reformat as _reformat
        keyfmt = _reformat(clean_md.splitlines(keepends=True))
        (fam_dir / "text_keyfmt.md").write_text("".join(keyfmt), encoding="utf-8")
    except Exception as exc:
        print(f"  [{fam}] reformat_keys skipped: {exc}")

    metadata = {
        "vol_label": vol_label,
        "family": fam,
        "pdf_filename": pdf_filename,
        "ocr_engine": "mineru",
        "processing_time_seconds": round(elapsed, 2),
        "figure_count": len(figure_names),
        "markdown_char_count": len(clean_md),
        "figures": figure_names,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    (fam_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # text.md written last — sentinel for is_already_processed
    (fam_dir / "text.md").write_text(clean_md, encoding="utf-8")
    return fam_dir


def process_volume(vol: dict, backend: str = "hybrid-auto-engine") -> bool:
    label = vol["label"]
    pdf_path = vol["pdf_path"]

    print(f"\n[vol{label}] Processing {pdf_path.name} with MinerU (backend={backend}) ...")
    t0 = time.monotonic()

    import tempfile
    with tempfile.TemporaryDirectory(prefix=f"mineru_vol{label}_") as tmp:
        mineru_tmp = pathlib.Path(tmp)
        try:
            markdown, src_images = run_mineru(pdf_path, mineru_tmp, backend=backend)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            # Write error log to a fallback dir
            err_dir = OUT_DIR / f"vol{label}_mineru"
            err_dir.mkdir(parents=True, exist_ok=True)
            (err_dir / "error.log").write_text(
                f"Processing failed after {elapsed:.1f}s\n\n" + traceback.format_exc()
            )
            print(f"[vol{label}] FAILED: {exc}  (see {err_dir}/error.log)")
            return False

        # Copy all images to a staging dir that survives the tempfile cleanup,
        # and build hash→sequential-name map.
        import tempfile as _tf
        staging = pathlib.Path(_tf.mkdtemp(prefix=f"mineru_imgs_{label}_"))
        img_map: dict[str, str] = {}
        staged: list[pathlib.Path] = []
        for i, src in enumerate(src_images):
            new_name = f"fig_{i:03d}{src.suffix}"
            dst = staging / new_name
            shutil.copy2(src, dst)
            img_map[src.name] = new_name
            staged.append(dst)

        elapsed = time.monotonic() - t0

    # Fix broken image references (hash → fig_NNN) pointing to figures/
    def _fix_ref(m: re.Match) -> str:
        return f"![](figures/{img_map.get(m.group(1), m.group(1))})"
    markdown = _IMG_REF_RE.sub(_fix_ref, markdown)

    # Detect family sections
    families = _detect_families_md(markdown)
    if families:
        chunks = _split_by_family(markdown, families)
        fam_names = list(chunks.keys())
    else:
        # No family heading found — write to a generic dir
        fam_names = [f"vol{label}"]
        chunks = {fam_names[0]: markdown}

    print(f"[vol{label}] Families: {', '.join(fam_names)}")

    total_figs = len(img_map)
    try:
        for fam, chunk in chunks.items():
            fam_dir = _write_family_dir(
                fam, label, chunk, staged, img_map,
                vol["pdf_filename"], elapsed,
            )
            fam_fig_count = len(list((fam_dir / "figures").iterdir()))
            print(f"  → {fam_dir.name}  ({len(chunk):,} chars, {fam_fig_count} figures)")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    print(f"[vol{label}] Done in {_fmt_time(elapsed)} — {total_figs} figures total")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="OCR Flore du Gabon PDF volumes with MinerU.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  conda run -n p12 python ocr_with_mineru.py --vol 1\n"
            "  conda run -n p12 python ocr_with_mineru.py --all\n"
            "  conda run -n p12 python ocr_with_mineru.py --all --start-from 20\n"
            "  conda run -n p12 python ocr_with_mineru.py --vol 1 --force"
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--vol", metavar="LABEL", help="Process a single volume (e.g. 1, 01, 5bis)")
    mode.add_argument("--all", action="store_true", help="Process all volumes")
    parser.add_argument("--start-from", metavar="LABEL", help="With --all: resume from this label")
    parser.add_argument("--force", action="store_true", help="Reprocess even if text.md exists")
    parser.add_argument(
        "--backend", "-b",
        choices=["pipeline", "vlm-auto-engine", "vlm-http-client", "hybrid-auto-engine", "hybrid-http-client"],
        default="hybrid-auto-engine",
        help=(
            "MinerU backend (default: hybrid-auto-engine):\n"
            "  pipeline            – general purpose\n"
            "  vlm-auto-engine     – high accuracy, local\n"
            "  vlm-http-client     – high accuracy, remote\n"
            "  hybrid-auto-engine  – next-gen high accuracy, local\n"
            "  hybrid-http-client  – high accuracy, minimal local compute, remote"
        ),
    )
    add_flora_arg(parser)
    args = parser.parse_args()
    _apply_flora(args.flora)

    volumes = discover_volumes()
    known_labels = [v["label"] for v in volumes]

    if args.vol:
        label = _normalize_label(args.vol, known_labels)
        if label is None:
            print(f"Error: volume '{args.vol}' not found. Known: {', '.join(known_labels)}")
            sys.exit(1)
        vol = next(v for v in volumes if v["label"] == label)
        if is_already_processed(label) and not args.force:
            print(f"[vol{label}] Already processed. Use --force to reprocess.")
            sys.exit(0)
        ok = process_volume(vol, backend=args.backend)
        sys.exit(0 if ok else 1)

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
        ok = process_volume(vol, backend=args.backend)
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
        print(f"  Re-run: conda run -n p12 python ocr_with_mineru.py --vol {failed[0]} --force")


if __name__ == "__main__":
    main()
