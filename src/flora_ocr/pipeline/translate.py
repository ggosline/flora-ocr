"""
Translate Flore du Gabon OCR output (text.md) from French to English using Claude.

Reads from:   ocr_output/vol{LABEL}_paddle/text.md   (preferred)
          or  ocr_output/vol{LABEL}/text.md           (marker fallback)
Writes to:    same directory / text_en.md

Run:
  conda run -n p12 python translate.py --vol 11
  conda run -n p12 python translate.py --all
  conda run -n p12 python translate.py --all --start-from 5
  conda run -n p12 python translate.py --vol 1 --source marker --force
"""

import argparse
import os
import pathlib
import re
import sys
import time
import traceback
from datetime import datetime, timezone

import anthropic

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

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
CHUNK_CHARS = 5_000       # source chars per API call (~1 250 tokens in, well under 8 192 out)
REQUEST_INTERVAL = 1.5    # seconds between calls — keeps us under 50 RPM

SYSTEM_PROMPT = """\
You are translating a French botanical monograph (Flore du Gabon) into English.

Rules:
1. Translate all French prose to natural English.
2. Preserve the following EXACTLY, character-for-character:
   - Latin scientific names (e.g. Xylopia aethiopica, ANNONACEAE, Garcinia kola Heckel)
   - Author citations (e.g. A. Rich., Engl., Hook.f., (Dunal) A. Rich.)
   - Markdown structure: heading markers (#, ##, ###, ####), bold (**…**), italic (*…*)
   - HTML page comments (<!-- page N -->)
   - Figure placeholders ([Figure N (p.X) — see figures.md])
   - Image references (![…](…))
   - Horizontal rules (---)
   - Numbers, measurements, coordinates, and abbreviations (mm, cm, m, alt., loc., etc.)
4. Silently correct small-caps OCR artefacts: these appear as words with erratic
   mixed case caused by the OCR model misreading small-capitals typography, e.g.
   "ANNoNoiDEAE" → "Annonoidéae", "UvARIEAE" → "Uvariéae", "XyLoPINEAE" → "Xylopineae",
   "A. Le THoMAs" → "A. Le Thomas". Apply standard title-case for family/tribe/subtribe
   names and standard capitalisation for author names. Use botanical context to infer
   the correct form — do not guess when uncertain.
5. Output ONLY the translated markdown. No preamble, no explanation, no code fences.\
"""


# ---------------------------------------------------------------------------
# Volume discovery (shared pattern with other scripts)
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
            vols.append({"label": label, "pdf_filename": pdf.name})
    vols.sort(key=lambda v: _label_sort_key(v["label"]))
    return vols


def _normalize_label(raw: str, known_labels: list[str]) -> str | None:
    if raw in known_labels:
        return raw
    if raw.isdigit():
        for candidate in (f"{int(raw):02d}", str(int(raw))):
            if candidate in known_labels:
                return candidate
    return None


def _find_source(label: str, source: str) -> pathlib.Path | None:
    """Return path to text.md for this volume, or None if not found."""
    candidates = []
    if source in ("paddle", "auto"):
        candidates.append(OUT_DIR / f"vol{label}_paddle" / "text.md")
    if source in ("marker", "auto"):
        candidates.append(OUT_DIR / f"vol{label}" / "text.md")
    for p in candidates:
        if p.exists():
            return p
    return None


def is_done(label: str, source: str) -> bool:
    src = _find_source(label, source)
    if src is None:
        return False
    return (src.parent / "text_en.md").exists()


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def make_chunks(text: str, max_chars: int) -> list[str]:
    """Split text at double-newline boundaries into chunks of at most max_chars."""
    paragraphs = re.split(r"\n\n", text)
    chunks = []
    current = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 2  # +2 for the \n\n we'll rejoin with
        if current and current_len + para_len > max_chars:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks


# ---------------------------------------------------------------------------
# Core translation
# ---------------------------------------------------------------------------
def translate_volume(label: str, source: str, model: str, request_interval: float) -> bool:
    src_path = _find_source(label, source)
    if src_path is None:
        print(f"[vol{label}] No source text.md found — run OCR first.")
        return False

    out_path = src_path.parent / "text_en.md"
    source_type = "paddle" if "_paddle" in src_path.parent.name else "marker"

    text = src_path.read_text(encoding="utf-8")
    chunks = make_chunks(text, CHUNK_CHARS)
    n = len(chunks)
    total_chars = len(text)

    print(f"\n[vol{label}] {src_path.parent.name}/text.md  ({total_chars:,} chars → {n} chunks)")

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from environment
    translated_chunks = []
    durations = []
    t_total = time.monotonic()

    for i, chunk in enumerate(chunks, 1):
        print(f"  [{i}/{n}] {len(chunk):,} chars …", end="\r", flush=True)
        t0 = time.monotonic()
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": chunk}],
            )
            translated = response.content[0].text
        except anthropic.RateLimitError:
            wait = 30
            print(f"\n  [{i}/{n}] rate limited — waiting {wait}s …", flush=True)
            time.sleep(wait)
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": chunk}],
            )
            translated = response.content[0].text
        except Exception as exc:
            print(f"\n  [{i}/{n}] API error: {exc}")
            translated = f"<!-- TRANSLATION FAILED chunk {i}: {exc} -->\n\n{chunk}"

        dt = time.monotonic() - t0
        durations.append(dt)
        translated_chunks.append(translated)

        avg = sum(durations) / len(durations)
        eta = avg * (n - i)
        usage = response.usage if hasattr(response, "usage") else None
        tok_str = f"{usage.input_tokens}→{usage.output_tokens} tok" if usage else ""
        print(
            f"  [{i}/{n}] {dt:.1f}s | {len(chunk):,}→{len(translated):,} chars"
            + (f" | {tok_str}" if tok_str else "")
            + f" | ETA ~{_fmt_time(eta)}   ",
            flush=True,
        )

        if i < n:
            time.sleep(request_interval)

    elapsed = time.monotonic() - t_total
    full_translation = "\n\n".join(translated_chunks)

    # Prepend a small header so readers know the provenance
    header = (
        f"<!-- Translated from French by {model} "
        f"on {datetime.now(timezone.utc).strftime('%Y-%m-%d')} "
        f"| source: {source_type} OCR -->\n\n"
    )
    out_path.write_text(header + full_translation, encoding="utf-8")

    print(
        f"[vol{label}] Done in {_fmt_time(elapsed)} — "
        f"{len(full_translation):,} chars → {out_path.name}"
    )
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Translate Flore du Gabon text.md files from French to English via Claude.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  conda run -n p12 python translate.py --vol 11\n"
            "  conda run -n p12 python translate.py --all\n"
            "  conda run -n p12 python translate.py --all --start-from 5\n"
            "  conda run -n p12 python translate.py --vol 1 --source marker --force"
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--vol", metavar="LABEL", help="Translate a single volume")
    mode.add_argument("--all", action="store_true", help="Translate all volumes with a text.md")
    parser.add_argument("--start-from", metavar="LABEL", help="With --all: skip volumes before this label")
    parser.add_argument("--force", action="store_true", help="Re-translate even if text_en.md exists")
    parser.add_argument(
        "--source", choices=["auto", "paddle", "marker"], default="auto",
        help="Which OCR output to translate (default: auto — prefers paddle)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--request-interval", type=float, default=REQUEST_INTERVAL, metavar="SECS",
        help=f"Seconds between API calls (default: {REQUEST_INTERVAL})",
    )
    add_flora_arg(parser)
    args = parser.parse_args()
    _apply_flora(args.flora)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    volumes = discover_volumes()
    if not volumes:
        print(f"No PDF volumes found in {PDF_DIR}")
        sys.exit(1)
    known_labels = [v["label"] for v in volumes]

    print(f"Model: {args.model}  |  chunk: {CHUNK_CHARS:,} chars  |  interval: {args.request_interval}s")

    if args.vol:
        label = _normalize_label(args.vol, known_labels)
        if label is None:
            print(f"Error: volume '{args.vol}' not found. Known: {', '.join(known_labels)}")
            sys.exit(1)
        if is_done(label, args.source) and not args.force:
            print(f"[vol{label}] Already translated. Use --force to redo.")
            sys.exit(0)
        ok = translate_volume(label, args.source, args.model, args.request_interval)
        sys.exit(0 if ok else 1)

    # --all mode
    start_label = None
    if args.start_from:
        start_label = _normalize_label(args.start_from, known_labels)
        if start_label is None:
            print(f"Error: --start-from '{args.start_from}' not found.")
            sys.exit(1)

    skipped_before = skipped_done = skipped_nosrc = 0
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
        if _find_source(lbl, args.source) is None:
            skipped_nosrc += 1
            continue
        if is_done(lbl, args.source) and not args.force:
            skipped_done += 1
            continue
        work_list.append(lbl)

    total = len(work_list)
    skipped = skipped_before + skipped_done + skipped_nosrc
    print(
        f"\n--- Batch: {total} to translate, {skipped} skipping "
        f"({skipped_before} before start, {skipped_done} already done, "
        f"{skipped_nosrc} no OCR source) ---\n"
    )

    processed = 0
    failed = []
    durations = []
    t_batch = time.monotonic()

    for idx, lbl in enumerate(work_list, 1):
        print(f"[{idx}/{total}] vol{lbl}")
        t0 = time.monotonic()
        try:
            ok = translate_volume(lbl, args.source, args.model, args.request_interval)
        except Exception:
            traceback.print_exc()
            ok = False
        durations.append(time.monotonic() - t0)

        if ok:
            processed += 1
        else:
            failed.append(lbl)

        avg = sum(durations) / len(durations)
        print(
            f"  Batch: {idx}/{total} | "
            f"Elapsed: {_fmt_time(time.monotonic() - t_batch)} | "
            f"ETA: ~{_fmt_time(avg * (total - idx))}"
        )

    print(f"\n--- Batch complete ---")
    print(f"  Translated: {processed}")
    print(f"  Skipped:    {skipped}")
    print(f"  Failed:     {len(failed)}")
    if failed:
        print(f"  Failed volumes: {', '.join(failed)}")
        print(f"  Re-run: conda run -n p12 python translate.py --vol {failed[0]} --force")


if __name__ == "__main__":
    main()
