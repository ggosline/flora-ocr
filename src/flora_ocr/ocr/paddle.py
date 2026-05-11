"""
OCR a Flore du Gabon PDF volume with PaddleOCR VL (v1.5).

Produces:
  ocr_output/vol{LABEL}_paddle/
    text.md       prose markdown; figures marked [Figure N (p.PP) — see figures.md]
    figures.md    one section per figure: PNG reference + caption text
    figures/      fig_000_p0001.png, fig_001_p0002.png, ...
    metadata.json timing, page/figure counts

Run:
  conda run -n ds_ocr python ocr_paddle.py --vol 1
  conda run -n ds_ocr python ocr_paddle.py --all
  conda run -n ds_ocr python ocr_paddle.py --all --start-from 20
  conda run -n ds_ocr python ocr_paddle.py --vol 1 --force
"""

import argparse
import io
import json
import pathlib
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import warnings
from datetime import datetime, timezone

# Suppress noisy but harmless PaddlePaddle internal warnings
warnings.filterwarnings("ignore", module="paddle")

import fitz  # pymupdf

from flora_ocr.flora import load_flora, add_flora_arg

# Set by _apply_flora() at startup.
PDF_DIR: pathlib.Path | None = None
OUT_DIR: pathlib.Path | None = None
CACHE_DIR: pathlib.Path | None = None   # raw OCR dumps for post-processing iteration
VOL_PATTERN: re.Pattern | None = None
PDF_GLOB: str = "*.pdf"


def _apply_flora(name: str) -> None:
    global PDF_DIR, OUT_DIR, CACHE_DIR, VOL_PATTERN, PDF_GLOB
    f = load_flora(name)
    PDF_DIR = f.pdf_dir
    OUT_DIR = f.output_dir
    CACHE_DIR = OUT_DIR / "_paddle_cache"
    VOL_PATTERN = f.vol_pattern
    PDF_GLOB = f.pdf_glob

# ---------------------------------------------------------------------------
# Block label categories
# ---------------------------------------------------------------------------
TEXT_LABELS = frozenset({
    "text", "ocr", "vertical_text", "abstract", "reference",
    "content", "reference_content", "footnote", "aside_text",
})
HEADING_LABELS = {
    "doc_title": "# ",
    "abstract_title": "## ",
    "content_title": "## ",
    "paragraph_title": "### ",
}
FIGURE_LABELS = frozenset({"image", "chart"})
IGNORE_LABELS = frozenset({"header", "footer", "header_image", "footer_image", "number"})

# ---------------------------------------------------------------------------
# Botanical taxonomy heading detection
# ---------------------------------------------------------------------------
# French words that start with a capital letter but are not genus names.
# This guards against promoting the first word of a French sentence.
_NON_GENUS = frozenset({
    # Articles / determiners
    "La", "Le", "Les", "Des", "Du", "Une", "Un", "Au", "Aux",
    "Ce", "Cet", "Cette", "Ces", "Mon", "Ton", "Son", "Nos", "Vos", "Leur", "Leurs",
    # Prepositions / conjunctions
    "Par", "Sur", "Sous", "Dans", "Pour", "Avec", "Sans", "Entre",
    "Mais", "Donc", "Lors", "Vers", "Chez", "Dès",
    # Pronouns
    "Il", "Elle", "Ils", "Elles", "On", "Nous", "Vous",
    # Common botanical description words (French)
    "Feuilles", "Fleurs", "Fruits", "Tiges", "Racines", "Graines",
    "Arbuste", "Arbre", "Liane", "Herbe", "Plante", "Tige", "Feuille",
    "Fleur", "Fruit", "Graine", "Bois", "Ecorce", "Rameau",
    # Section labels
    "Fig", "Pl", "Tab", "Obs", "Loc", "Alt", "Syn", "Type",
    "Note", "Voir", "Ref", "Sect", "Gen", "Var",
    # Common verbs / adjectives that start sentences
    "Distribution", "Habitat", "Ecologie", "Remarques", "Description",
    "Iconographie", "Bibliographie", "Noms", "Usages",
})

# Rank abbreviations that mark infraspecific taxa
_RANK_RE = re.compile(r'\b(var\.|subsp\.|ssp\.|f\.|cv\.)\b')

# Family-shaped first word: only promote to "# " if it actually looks like a
# family name. Latin -CEAE/-EAE, French -CÉES/-CEES (with or without accent).
# This is much tighter than "any all-caps word ≥4 chars" — it stops field
# labels (MATÉRIEL, SYNTYPES, LECTOTYPE, HOLOTYPE), running co-author headers
# (R. LETOUZEY & F. WHITE) and column-truncated junk (EBENACE) from being
# falsely promoted to family-level headings.
_FAMILY_SUFFIX_RE = re.compile(
    r'^[A-Z\u00C0-\u00DD]{3,}(?:CEAE|CÉES|CEES|ACEAE|ACÉES|ACEES|EAE)$'
)
_NOM_CONS_FAMILIES = frozenset({
    'LEGUMINOSAE', 'COMPOSITAE', 'UMBELLIFERAE', 'CRUCIFERAE',
    'LABIATAE', 'GRAMINEAE', 'PALMAE', 'GUTTIFERAE',
})


def _taxon_heading_level(text: str) -> str | None:
    """Return a markdown heading prefix if *text* looks like a botanical taxon name.

    Priority (most-specific first):
      '#### ' — infraspecific (var./subsp./ssp./f.)
      '### '  — species binomial (Genus species [Author …])
      '## '   — genus name alone
      '# '    — family name (ALL-CAPS, family-suffix or nom. cons.)
      None    — not a recognised taxon pattern
    """
    s = text.strip()
    # Only inspect single-line content (multi-line = paragraph, not a name)
    if not s or "\n" in s:
        return None

    words = s.split()
    first = words[0]

    # 1. Family: first word is a recognised family name (suffix-matched or
    #    nom. cons. legacy name). Title-page family declarations on scanned
    #    PDFs come through PaddleOCR as text blocks, not headers, so this
    #    branch is the only path that promotes them.
    if _FAMILY_SUFFIX_RE.match(first) or first in _NOM_CONS_FAMILIES:
        return "# "

    # 2. Infraspecific: contains a rank abbreviation AND begins with a capitalised word
    #    e.g. "Xylopia aethiopica var. grandiflora Engl."
    if _RANK_RE.search(s) and re.match(r"^[A-Z][a-z]{2,}", s):
        return "#### "

    # 3. Species binomial: Genus + lowercase epithet (≥3 chars), optional author
    #    e.g. "Xylopia aethiopica", "Garcinia kola Heckel"
    m = re.match(r"^([A-Z][a-z]{2,30})\s+([a-z]{3,30}(?:-[a-z]+)?)(?:[\s,\(]|$)", s)
    if m and m.group(1) not in _NON_GENUS:
        return "### "

    # 4. Genus alone: single capitalised word, 4–25 chars, optional trailing dot
    #    e.g. "Xylopia", "Garcinia"
    if (
        len(words) == 1
        and re.match(r"^[A-Z][a-z]{3,24}\.?$", first)
        and first.rstrip(".") not in _NON_GENUS
    ):
        return "## "

    return None


# ---------------------------------------------------------------------------
# Family detection in flat markdown
# ---------------------------------------------------------------------------
# A family heading is a top-level (#–####) heading whose content is one of:
#   ALL-CAPS French (MYRTACÉES, THYMÉLÉACÉES, EBENACEES, ANNONACÉES) — scanned PDFs
#   ALL-CAPS Latin (ANNONACEAE, RUBIACEAE, RANUNCULACEAE Juss.)      — scanned PDFs
#   Title-case Latin (Ancistrocladaceae, Menispermaceae)              — born-digital PDFs
#   ICN-allowed nom. cons. names (Leguminosae, Compositae, …)         — both
#
# The all-caps alternative requires ≥4 leading uppercase letters before the
# `C` so that we don't match short legitimate words (CECA, ACE, etc.). The
# accent-permissive [A-ZÀ-Ý] class catches French accented forms like
# THYMÉLÉACÉES that the ASCII-only [A-Z] class misses.
_UPPER = r'[A-Z\u00C0-\u00DD]'
_FAM_RE = re.compile(
    rf'^#{{1,4}}\s+'
    rf'('
    rf'{_UPPER}{{4,}}C[EÉ][AEÉ][ES]S?'                                  # ALL-CAPS -CEAE/-CÉES
    rf'|'
    rf'[A-Z][a-z]{{3,}}ceae'                                            # Title-case -ceae
    rf'|'
    rf'(?:LEGUMINOSAE|COMPOSITAE|UMBELLIFERAE|CRUCIFERAE|LABIATAE|GRAMINEAE|PALMAE|GUTTIFERAE)'
    rf'|'
    rf'(?:Leguminosae|Compositae|Umbelliferae|Cruciferae|Labiatae|Gramineae|Palmae|Guttiferae)'
    rf')'
    rf'(?=\s|\(|\)|$)',
    re.MULTILINE,
)


def _strip_accents(s: str) -> str:
    """Remove combining diacritics — THYMÉLÉACÉES → THYMELEACEES."""
    return ''.join(
        c for c in unicodedata.normalize('NFKD', s)
        if not unicodedata.combining(c)
    )


def _normalize_family(raw: str) -> str:
    """Convert a French/Latin all-caps family name to standard Title-case Latin.

    ANNONACEES → Annonaceae,  RUBIACEAE → Rubiaceae,  MYRTACÉES → Myrtaceae,
    THYMÉLÉACÉES → Thymeleaceae,  Leguminosae → Leguminosae.
    """
    name = re.sub(r'\s*\(.*', '', raw).strip()
    # nom. cons. names — keep as-is in Title case
    nom_cons = {
        'leguminosae', 'compositae', 'umbelliferae', 'cruciferae',
        'labiatae', 'gramineae', 'palmae', 'guttiferae',
    }
    if name.lower() in nom_cons:
        return name[0].upper() + name[1:].lower()
    name = _strip_accents(name).upper()
    # French -CEES/-CÉES and Latin -CEAE all end in C + two vowels (+ optional ES)
    name = re.sub(r'C[EÉ][AEÉ][ES]S?$', 'ceae', name, flags=re.IGNORECASE)
    return name[0].upper() + name[1:].lower() if name else name


def _detect_families(markdown: str) -> list[tuple[str, int]]:
    """Return [(family_name, char_offset), …] for each family heading found.

    Consecutive duplicates (same family name) are de-duplicated so that running
    headers like ``# EBENACEES`` repeated each page only count as the first
    occurrence's start position.
    """
    families: list[tuple[str, int]] = []
    for m in _FAM_RE.finditer(markdown):
        fam = _normalize_family(m.group(1))
        if not families or families[-1][0] != fam:
            families.append((fam, m.start()))
    return families


def _split_by_family(
    markdown: str,
    families: list[tuple[str, int]],
) -> dict[str, str]:
    """Split markdown into per-family chunks at the detected headings.

    The first family chunk starts at offset 0, not at the first family
    heading, so front matter (title page, TOC, abbreviations, genus
    declaration, key intro) is not dropped. Subsequent families start
    at their own heading.
    """
    chunks: dict[str, str] = {}
    for i, (fam, start) in enumerate(families):
        chunk_start = 0 if i == 0 else start
        chunk_end = families[i + 1][1] if i + 1 < len(families) else len(markdown)
        chunks[fam] = markdown[chunk_start:chunk_end]
    return chunks


# ---------------------------------------------------------------------------
# Running-header detection and removal
# ---------------------------------------------------------------------------
# Repeated all-caps short heading lines are page furniture (page running
# headers / footers) that PaddleOCR sometimes misclassifies as text/heading
# blocks instead of header/footer. They look like:
#   # EBENACE                       (truncated family name)
#   # R. LETOUZEY & F. WHITE        (running co-author header)
# These add huge noise to the family-split logic and the structured output.

_PAGE_MARKER_RE = re.compile(r'<!--\s*page\s+\d+\s*-->')
_RUN_HDR_MIN_PAGES = 5          # absolute floor — appear on at least this many pages
_RUN_HDR_MIN_FRACTION = 0.03    # …or this fraction of all pages, whichever larger


def _repair_family_stem(stem: str) -> str | None:
    """Try to repair a column-truncated family-stem like EBENACE → EBENACEAE.

    Heuristics — only fire when the stem clearly looks like a truncation:
      X…AC      → X…ACEAE      (Latin -ACEAE, lost final EAE)
      X…ACE     → X…ACEAE      (Latin -ACEAE, lost final AE)
      X…ACEE    → X…ACEES      (French -ACEES, lost final S)
      X…ACÉE    → X…ACÉES      (French -ACÉES, lost final S)
    """
    if re.match(r'^[A-Z\u00C0-\u00DD]{4,}AC$', stem):
        return stem + 'EAE'
    if re.match(r'^[A-Z\u00C0-\u00DD]{4,}ACE$', stem):
        return stem + 'AE'
    if re.match(r'^[A-Z\u00C0-\u00DD]{4,}ACEE$', stem):
        return stem + 'S'
    if re.match(r'^[A-Z\u00C0-\u00DD]{4,}ACÉE$', stem):
        return stem + 'S'
    return None


def _drop_running_headers(full_text: str) -> str:
    """Strip page running headers from the OCR'd markdown.

    Detection:
        Split text by ``<!-- page N -->`` markers, count short heading lines
        (#-prefixed, ≤80 chars) per page, then any line appearing on at least
        ``max(_RUN_HDR_MIN_PAGES, _RUN_HDR_MIN_FRACTION * N_pages)`` distinct
        pages is treated as a running header and dropped.

    Family stem repair:
        If a dropped running header is a truncated all-caps family name (e.g.
        ``# EBENACE`` → ``# EBENACEAE``), the FIRST occurrence is replaced
        with the repaired form so ``_detect_families`` can pick it up.
        All other occurrences are removed.
    """
    pages = _PAGE_MARKER_RE.split(full_text)
    n_pages = len(pages)
    if n_pages < 3:
        return full_text

    threshold = max(_RUN_HDR_MIN_PAGES, int(_RUN_HDR_MIN_FRACTION * n_pages))

    # Count distinct pages each short heading appears on
    pages_per_line: dict[str, int] = {}
    for page in pages:
        seen_in_page = set()
        for raw in page.splitlines():
            stripped = raw.strip()
            if (stripped.startswith('#') and len(stripped) <= 80
                    and stripped not in seen_in_page):
                pages_per_line[stripped] = pages_per_line.get(stripped, 0) + 1
                seen_in_page.add(stripped)

    running = {ln for ln, c in pages_per_line.items() if c >= threshold}
    if not running:
        return full_text

    # Classify each running header into one of:
    #   repair_map[h]   = repaired form (truncated family stem → completed)
    #   preserve_set[h] = keep first occurrence as-is (already-complete family)
    #   else           = drop all occurrences (page furniture / co-author header)
    # In all cases, only the FIRST hit is emitted; the rest are stripped.
    repair_map: dict[str, str] = {}
    preserve_set: set[str] = set()
    for header in running:
        m = re.match(r'^(#{1,6})\s+([A-Z\u00C0-\u00DD]{4,})\s*$', header)
        if not m:
            continue
        hashes, stem = m.group(1), m.group(2)
        if _FAMILY_SUFFIX_RE.match(stem) or stem in _NOM_CONS_FAMILIES:
            preserve_set.add(header)
            continue
        repaired = _repair_family_stem(stem)
        if repaired:
            repair_map[header] = f"{hashes} {repaired}"

    # Walk the original text, dropping / repairing / preserving-first
    out_lines: list[str] = []
    seen_first: set[str] = set()
    n_dropped = 0
    n_repaired = 0
    n_preserved = 0
    for raw in full_text.splitlines(keepends=True):
        stripped = raw.rstrip('\n').rstrip()
        if stripped in running:
            if stripped not in seen_first and stripped in repair_map:
                seen_first.add(stripped)
                out_lines.append(repair_map[stripped] + '\n')
                n_repaired += 1
            elif stripped not in seen_first and stripped in preserve_set:
                seen_first.add(stripped)
                out_lines.append(raw)
                n_preserved += 1
            else:
                n_dropped += 1
        else:
            out_lines.append(raw)

    if n_dropped or n_repaired or n_preserved:
        extras = []
        if n_repaired:
            extras.append(f"repaired {n_repaired}")
        if n_preserved:
            extras.append(f"kept-first {n_preserved}")
        extra_str = f" ({', '.join(extras)})" if extras else ""
        print(
            f"  Dropped {n_dropped} running-header lines "
            f"from {len(running)} unique{extra_str}"
        )
    return ''.join(out_lines)


# Reference written by parse_page_blocks: "[Figure N (p.PP) — see figures.md]"
_FIG_REF_RE = re.compile(r'\[Figure (\d+) \(p\.\d+\)')


def _figs_referenced_in(chunk: str) -> set[int]:
    """Return the set of fig_idx values referenced in a markdown chunk."""
    return {int(m.group(1)) for m in _FIG_REF_RE.finditer(chunk)}


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
        # Try zero-padded (e.g. "01") and stripped (e.g. "1")
        for candidate in (f"{int(raw):02d}", str(int(raw))):
            if candidate in known_labels:
                return candidate
    return None


def is_done(label: str) -> bool:
    # For Flore du Gabon completion we only count family-split outputs.
    # Older whole-volume vol{label}_paddle dirs are not sufficient.
    return any((d / "text.md").exists() for d in OUT_DIR.glob(f"*_vol{label}_paddle"))


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


# ---------------------------------------------------------------------------
# Block parsing
# ---------------------------------------------------------------------------
def parse_page_blocks(result, page_num: int, fig_idx_start: int):
    """Parse ordered blocks from one page's PaddleOCRVLResult.

    Args:
        result: PaddleOCRVLResult for a single page image.
        page_num: 0-based page index.
        fig_idx_start: global figure index to assign to the first figure on this page.

    Returns:
        (text_fragment, fig_dicts) where:
          text_fragment — prose markdown string for this page
          fig_dicts     — list of figure dicts (fig_idx, page_num, pil_img, caption, …)
    """
    blocks = result["parsing_res_list"]

    # First pass: locate figure and caption blocks by index
    figure_block_indices = []   # ordered list of block indices that are figures
    caption_blocks = {}         # block_index → caption text

    for i, block in enumerate(blocks):
        if block.label in FIGURE_LABELS:
            figure_block_indices.append(i)
        elif block.label == "figure_title":
            caption_blocks[i] = block.content.strip()

    # Build one dict per figure
    fig_dicts = []
    for pos, bi in enumerate(figure_block_indices):
        block = blocks[bi]
        pil_img = block.image["img"] if block.image is not None else None
        fig_dicts.append({
            "fig_idx": fig_idx_start + pos,
            "page_num": page_num,
            "label": block.label,
            "bbox": block.bbox,
            "pil_img": pil_img,
            "caption": None,
        })

    # Associate each caption to the nearest figure by block-index distance
    if figure_block_indices:
        for cap_idx, cap_text in caption_blocks.items():
            nearest = min(
                range(len(figure_block_indices)),
                key=lambda j: abs(figure_block_indices[j] - cap_idx),
            )
            fd = fig_dicts[nearest]
            fd["caption"] = cap_text if fd["caption"] is None else fd["caption"] + "\n" + cap_text

    # Map block index → position in fig_dicts
    fig_by_block = {figure_block_indices[p]: p for p in range(len(figure_block_indices))}

    # Second pass: build text in reading order (captions are skipped)
    text_parts = []
    for i, block in enumerate(blocks):
        label = block.label
        if label in IGNORE_LABELS or label == "figure_title":
            continue
        if label in TEXT_LABELS:
            txt = block.content.strip()
            if txt:
                # Promote short single-line text blocks that look like taxon names.
                # (Long / multi-line content is always prose.)
                taxon = _taxon_heading_level(txt) if len(txt) <= 80 else None
                text_parts.append(f"{taxon}{txt}" if taxon else txt)
        elif label in HEADING_LABELS:
            txt = block.content.strip()
            if txt:
                # Taxonomy beats visual heading level so the hierarchy is consistent.
                taxon = _taxon_heading_level(txt)
                prefix = taxon if taxon else HEADING_LABELS[label]
                text_parts.append(f"{prefix}{txt}")
        elif label == "table":
            txt = block.content.strip()
            if txt:
                text_parts.append(txt)
        elif label in FIGURE_LABELS:
            fd = fig_dicts[fig_by_block[i]]
            n, p = fd["fig_idx"], page_num + 1
            text_parts.append(f"[Figure {n} (p.{p}) — see figures.md]")

    return "\n\n".join(text_parts), fig_dicts


# ---------------------------------------------------------------------------
# Render worker
# ---------------------------------------------------------------------------

def _render_pages(doc, n_pages: int, mat, out_queue: queue.Queue, ahead: int) -> None:
    """Background thread: render PDF pages to numpy arrays and push to a queue.

    Produces (page_idx, np.ndarray) tuples with shape (H, W, 3) uint8 RGB.
    Puts None as a sentinel when done.  The queue size is capped at `ahead`
    so the thread doesn't race far ahead of the OCR loop.
    """
    import numpy as np

    for page_idx in range(n_pages):
        pixmap = doc[page_idx].get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        # Copy into a new array — frombuffer gives a read-only view into
        # PyMuPDF's internal buffer which is freed when pixmap goes out of scope.
        img = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, 3
        ).copy()
        out_queue.put((page_idx, img))

    out_queue.put(None)  # sentinel


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------
def _cache_paths(label: str) -> tuple[pathlib.Path, pathlib.Path]:
    """Return (raw_text_path, figures_pickle_path) for a volume's OCR cache."""
    return (
        CACHE_DIR / f"vol{label}_raw.md",
        CACHE_DIR / f"vol{label}_figures.pkl",
    )


def _load_cache(label: str) -> tuple[str, list[dict], int] | None:
    """Load previously-cached raw OCR output. Returns (full_text, figures, n_pages) or None."""
    import pickle
    raw_path, figs_path = _cache_paths(label)
    if not (raw_path.exists() and figs_path.exists()):
        return None
    full_text = raw_path.read_text(encoding="utf-8")
    with figs_path.open("rb") as f:
        bundle = pickle.load(f)
    return full_text, bundle["figures"], bundle["n_pages"]


def _save_cache(label: str, full_text: str, figures: list[dict], n_pages: int) -> None:
    """Save raw OCR output so post-processing can be iterated without re-running OCR."""
    import pickle
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    raw_path, figs_path = _cache_paths(label)
    raw_path.write_text(full_text, encoding="utf-8")
    with figs_path.open("wb") as f:
        pickle.dump({"figures": figures, "n_pages": n_pages}, f)


def process_volume(
    vol: dict,
    pipeline,
    dpi: int,
    batch_size: int = 1,
    from_cache: bool = False,
) -> bool:
    label = vol["label"]
    pdf_path = vol["pdf_path"]

    print(f"\n[vol{label}] {pdf_path.name}")
    t_total = time.monotonic()

    # Fast path: reuse cached raw OCR from a previous run
    if from_cache:
        cached = _load_cache(label)
        if cached is None:
            print(f"  [vol{label}] no cache found at {CACHE_DIR} — falling back to full OCR")
        else:
            full_text, all_figures, n_pages = cached
            print(f"  loaded cache: {len(full_text):,} chars, {len(all_figures)} figures, {n_pages} pages")
            elapsed = time.monotonic() - t_total
            _finalize_volume(vol, label, full_text, all_figures, n_pages, dpi, elapsed)
            return True

    doc = fitz.open(str(pdf_path))
    n_pages = len(doc)
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    print(f"  {n_pages} pages | {dpi} DPI | batch {batch_size}", flush=True)

    all_text_fragments: list[str] = []
    all_figures: list[dict] = []
    durations: list[float] = []

    # Start background render thread.  It renders pages to PIL Images (raw RGB
    # bytes — no PNG encode/decode) and pushes them onto a small bounded queue.
    # While the GPU is busy with OCR the CPU is already rendering the next pages.
    render_queue: queue.Queue = queue.Queue(maxsize=batch_size + 2)
    render_thread = threading.Thread(
        target=_render_pages,
        args=(doc, n_pages, mat, render_queue, batch_size + 2),
        daemon=True,
    )
    render_thread.start()

    # Drain render_queue in batches
    pending: list[tuple[int, object]] = []   # (page_idx, PIL.Image)
    sentinel_seen = False

    while not sentinel_seen or pending:
        # Fill one batch from the queue
        while len(pending) < batch_size and not sentinel_seen:
            item = render_queue.get()
            if item is None:
                sentinel_seen = True
            else:
                pending.append(item)

        if not pending:
            break

        batch = pending[:batch_size]
        pending = pending[batch_size:]

        batch_indices = [pidx for pidx, _ in batch]
        batch_imgs    = [img  for _, img  in batch]

        first_label = batch_indices[0] + 1
        last_label  = batch_indices[-1] + 1
        range_str = (f"{first_label}" if len(batch_indices) == 1
                     else f"{first_label}–{last_label}")
        print(f"  [{range_str}/{n_pages}] OCR …      ", end="\r", flush=True)
        t_batch_start = time.monotonic()

        # Run PaddleOCR VL with numpy arrays (supported input type).
        try:
            results = list(pipeline.predict(input=batch_imgs))
            if len(results) != len(batch_indices):
                raise RuntimeError(
                    f"predict() returned {len(results)} results for {len(batch_indices)} pages"
                )
        except Exception as exc:
            print(f"\n  [p{range_str}] OCR error: {exc}")
            for pidx, _ in batch:
                all_text_fragments.append(f"<!-- OCR FAILED page {pidx+1}: {exc} -->")
            continue

        batch_elapsed = time.monotonic() - t_batch_start
        per_page = batch_elapsed / len(batch_indices)
        durations.extend([per_page] * len(batch_indices))

        # Parse results for each page in the batch
        for pidx, result in zip(batch_indices, results):
            plabel = pidx + 1
            fig_idx_start = len(all_figures)
            try:
                frag, figs = parse_page_blocks(result, pidx, fig_idx_start)
            except Exception as exc:
                print(f"\n  [p{plabel}] parse error: {exc}")
                frag = f"<!-- PARSE FAILED page {plabel}: {exc} -->"
                figs = []

            all_text_fragments.append(f"<!-- page {plabel} -->\n\n{frag}")
            all_figures.extend(figs)

        processed_so_far = batch_indices[-1] + 1
        avg = sum(durations) / len(durations)
        eta = avg * (n_pages - processed_so_far)
        eta_str = _fmt_time(eta) if eta > 0 else "—"
        print(
            f"  [{range_str}/{n_pages}] {batch_elapsed:.1f}s ({per_page:.1f}s/pg) | "
            f"{len(all_figures)} figs | ETA ~{eta_str}   ",
            flush=True,
        )

    render_thread.join()
    doc.close()

    raw_full_text = "\n\n---\n\n".join(all_text_fragments)

    # Cache raw OCR output so post-processing can be iterated without re-OCR
    try:
        _save_cache(label, raw_full_text, all_figures, n_pages)
    except Exception as exc:
        print(f"  [vol{label}] cache save failed: {exc}")

    elapsed = time.monotonic() - t_total
    _finalize_volume(vol, label, raw_full_text, all_figures, n_pages, dpi, elapsed)
    return True


def _finalize_volume(
    vol: dict,
    label: str,
    raw_full_text: str,
    all_figures: list[dict],
    n_pages: int,
    dpi: int,
    elapsed: float,
) -> None:
    """Run all post-OCR processing: drop running headers, split families, write dirs."""
    # Strip page running headers (PaddleOCR sometimes mislabels them as text)
    full_text = _drop_running_headers(raw_full_text)

    # Detect families and decide output layout
    families = _detect_families(full_text)
    if families:
        chunks = _split_by_family(full_text, families)
        print(f"[vol{label}] Families: {', '.join(chunks.keys())}")
        for fam, chunk in chunks.items():
            out_dir = OUT_DIR / f"{fam}_vol{label}_paddle"
            _write_paddle_dir(
                out_dir=out_dir,
                label=label,
                family=fam,
                pdf_filename=vol["pdf_filename"],
                full_text=chunk,
                all_figures=all_figures,
                fig_filter=_figs_referenced_in(chunk),
                dpi=dpi,
                n_pages=n_pages,
                elapsed=elapsed,
            )
    else:
        out_dir = OUT_DIR / f"vol{label}_paddle"
        _write_paddle_dir(
            out_dir=out_dir,
            label=label,
            family=None,
            pdf_filename=vol["pdf_filename"],
            full_text=full_text,
            all_figures=all_figures,
            fig_filter=None,   # keep all figures
            dpi=dpi,
            n_pages=n_pages,
            elapsed=elapsed,
        )

    print(
        f"[vol{label}] Done in {_fmt_time(elapsed)} — "
        f"{len(full_text):,} chars, {len(all_figures)} figures total"
    )


def _write_paddle_dir(
    *,
    out_dir: pathlib.Path,
    label: str,
    family: str | None,
    pdf_filename: str,
    full_text: str,
    all_figures: list[dict],
    fig_filter: set[int] | None,
    dpi: int,
    n_pages: int,
    elapsed: float,
) -> None:
    """Write text.md, figures.md, figures/, metadata.json for one output dir.

    If *fig_filter* is None, keep all figures (whole-volume mode); otherwise
    keep only the figures whose ``fig_idx`` is in the set (per-family mode).
    """
    fig_dir = out_dir / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(exist_ok=True)

    saved_figs = []
    fig_md_parts = []
    for fd in all_figures:
        if fig_filter is not None and fd["fig_idx"] not in fig_filter:
            continue
        n = fd["fig_idx"]
        p = fd["page_num"] + 1
        fname = f"fig_{n:03d}_p{p:04d}.png"
        if fd["pil_img"] is not None:
            fd["pil_img"].save(str(fig_dir / fname), "PNG")
        saved_figs.append({
            "filename": fname,
            "page": p,
            "caption": fd["caption"],
            "label": fd["label"],
        })
        caption = fd["caption"] or "*(no caption detected)*"
        fig_md_parts.append(
            f"## Figure {n} (page {p})\n\n"
            f"![{fname}](figures/{fname})\n\n"
            f"*Caption:* {caption}"
        )
    figures_md = "\n\n---\n\n".join(fig_md_parts) or "*(No figures detected)*\n"

    metadata = {
        "vol_label": label,
        "family": family,
        "pdf_filename": pdf_filename,
        "pipeline": "PaddleOCRVL v1.5",
        "dpi": dpi,
        "page_count": n_pages,
        "figure_count": len(saved_figs),
        "markdown_char_count": len(full_text),
        "processing_time_seconds": round(elapsed, 2),
        "figures": saved_figs,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "figures.md").write_text(figures_md, encoding="utf-8")

    # Indented key view (best-effort — failures should never block the OCR run)
    try:
        from reformat_keys import reformat as _reformat
        keyfmt = _reformat(full_text.splitlines(keepends=True))
        (out_dir / "text_keyfmt.md").write_text("".join(keyfmt), encoding="utf-8")
    except Exception as exc:
        print(f"  [{family or 'vol' + label}] reformat_keys skipped: {exc}")

    # text.md is the sentinel — written last so is_done() only sees complete dirs
    (out_dir / "text.md").write_text(full_text, encoding="utf-8")
    print(f"  → {out_dir.name}  ({len(full_text):,} chars, {len(saved_figs)} figures)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    # Force line-buffered stdout/stderr so `print()` progress shows up live
    # when this script is launched under `conda run` (which otherwise captures
    # the whole pipe and only flushes at exit).  Paddle's C++ layer still
    # writes directly to fd 1 with its own buffer, so the cleanest user-side
    # fix is also to pass `conda run --no-capture-output ... python -u ...`.
    import os
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except (AttributeError, io.UnsupportedOperation):
        pass

    parser = argparse.ArgumentParser(
        description="OCR Flore du Gabon PDF volumes with PaddleOCR VL 1.5.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  conda run --no-capture-output -n ds_ocr python -u ocr_paddle.py --vol 1\n"
            "  conda run --no-capture-output -n ds_ocr python -u ocr_paddle.py --all\n"
            "  conda run --no-capture-output -n ds_ocr python -u ocr_paddle.py --all --start-from 20\n"
            "  conda run --no-capture-output -n ds_ocr python -u ocr_paddle.py --vol 1 --force"
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--vol", metavar="LABEL",
        help="Process a single volume (e.g. 1, 01, 5bis)",
    )
    mode.add_argument(
        "--all", action="store_true",
        help="Process all volumes, skipping completed ones",
    )
    parser.add_argument(
        "--start-from", metavar="LABEL",
        help="With --all: skip volumes before this label (for resuming)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Reprocess even if text.md already exists",
    )
    parser.add_argument(
        "--from-cache", action="store_true",
        help="Skip OCR and re-run only post-processing from cached raw OCR "
             "(use after editing Fix A/B / family split logic)",
    )
    parser.add_argument(
        "--dpi", type=int, default=150,
        help="Page rendering DPI (default 150; use 200 for higher quality)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=1, metavar="N",
        help="Pages to send to the OCR model at once (default 1; try 2–4 if GPU RAM allows)",
    )
    parser.add_argument(
        "--device", choices=("auto", "gpu", "cpu"), default="auto",
        help="Execution device for PaddleOCR VL (default: auto)",
    )
    add_flora_arg(parser)
    args = parser.parse_args()
    _apply_flora(args.flora)

    volumes = discover_volumes()
    if not volumes:
        print(f"No PDF volumes found in {PDF_DIR}")
        sys.exit(1)
    known_labels = [v["label"] for v in volumes]

    # Pipeline loading is expensive — skip it entirely in --from-cache mode
    pipeline = None
    if not args.from_cache:
        # Set the global Paddle device — this propagates to all sub-models
        # inside the pipeline, not just the top-level constructor argument.
        import paddle
        use_gpu = args.device == "gpu" or (
            args.device == "auto" and paddle.device.is_compiled_with_cuda()
        )
        paddle_device = "gpu:0" if use_gpu else "cpu"
        pipeline_device = "gpu" if use_gpu else "cpu"
        try:
            paddle.device.set_device(paddle_device)
        except Exception:
            if args.device == "gpu":
                raise
            paddle_device = "cpu"
            pipeline_device = "cpu"
            paddle.device.set_device("cpu")

        if (
            pipeline_device == "cpu"
            and paddle.device.is_compiled_with_cuda()
            and paddle.device.cuda.device_count() == 0
        ):
            print(
                "ERROR: ds_ocr has a CUDA build of PaddleOCR-VL but no CUDA-capable GPU "
                "is available on this machine. This environment cannot run ocr_paddle.py "
                "here; use a machine with a working GPU or a CPU-compatible PaddleOCR setup.",
                flush=True,
            )
            sys.exit(2)

        print(f"Paddle device: {paddle.device.get_device()}", flush=True)

        # Load pipeline once — avoids reloading weights per volume.
        print("Loading PaddleOCR VL pipeline …", flush=True)
        from paddleocr import PaddleOCRVL
        pipeline = PaddleOCRVL(
            pipeline_version="v1.5",
            device=pipeline_device,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
        )
        print("Pipeline ready.\n", flush=True)

    if args.vol:
        label = _normalize_label(args.vol, known_labels)
        if label is None:
            print(f"Error: volume '{args.vol}' not found. Known: {', '.join(known_labels)}")
            sys.exit(1)
        vol = next(v for v in volumes if v["label"] == label)
        if is_done(label) and not args.force and not args.from_cache:
            print(f"[vol{label}] Already processed. Use --force to reprocess.")
            sys.exit(0)
        ok = process_volume(
            vol, pipeline, args.dpi, args.batch_size, from_cache=args.from_cache
        )
        sys.exit(0 if ok else 1)

    # --all mode
    start_label = None
    if args.start_from:
        start_label = _normalize_label(args.start_from, known_labels)
        if start_label is None:
            print(f"Error: --start-from '{args.start_from}' not found.")
            sys.exit(1)

    skipped_before = skipped_done = 0
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
        if is_done(lbl) and not args.force:
            skipped_done += 1
            continue
        work_list.append(vol)

    total = len(work_list)
    skipped = skipped_before + skipped_done
    print(f"--- Batch: {total} to process, {skipped} skipping "
          f"({skipped_before} before start, {skipped_done} already done) ---\n")

    processed = 0
    failed = []
    durations = []
    t_batch = time.monotonic()

    for idx, vol in enumerate(work_list, 1):
        lbl = vol["label"]
        print(f"[{idx}/{total}] vol{lbl} — {vol['pdf_filename']}")
        t0 = time.monotonic()
        try:
            ok = process_volume(vol, pipeline, args.dpi, args.batch_size)
        except Exception:
            traceback.print_exc()
            ok = False
        durations.append(time.monotonic() - t0)

        if ok:
            processed += 1
        else:
            failed.append(lbl)

        avg = sum(durations) / len(durations)
        remaining = avg * (total - idx)
        print(f"  Batch: {idx}/{total} | Elapsed: {_fmt_time(time.monotonic() - t_batch)} | ETA: ~{_fmt_time(remaining)}")

    print(f"\n--- Batch complete ---")
    print(f"  Processed: {processed}")
    print(f"  Skipped:   {skipped}")
    print(f"  Failed:    {len(failed)}")
    if failed:
        print(f"  Failed volumes: {', '.join(failed)}")
        print(f"  Re-run: conda run -n ds_ocr python ocr_paddle.py --vol {failed[0]} --force")


if __name__ == "__main__":
    main()
