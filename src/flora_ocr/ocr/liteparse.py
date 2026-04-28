"""Parse botanical PDFs using liteparse (embedded text) + PyMuPDF (figure extraction).

Produces the same core output structure as ocr_paddle.py / ocr_with_mineru.py:
  text.md, figures.md, figures/, metadata.json

Best suited for born-digital PDFs whose text is embedded. Falls back
gracefully when liteparse finds little text on a page.

Volume mode writes one directory per detected family:
  ocr_output/{Family}_vol{label}_liteparse/

Article mode writes one directory per article id:
  ocr_output/articles/{article_id}/liteparse/

Usage
-----
  conda run -n p12 python ocr_liteparse.py --vol 60
  conda run -n p12 python ocr_liteparse.py --all
  conda run -n p12 python ocr_liteparse.py --all --start-from 55
  conda run -n p12 python ocr_liteparse.py --pdf article_pdfs/queued/foo.pdf --article-id foo
"""

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import fitz  # PyMuPDF

from flora_ocr.flora import load_flora, add_flora_arg

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Set by _apply_flora() at startup; safe to leave None until main() runs.
PDF_DIR: pathlib.Path | None = None
OUT_DIR: pathlib.Path | None = None
ARTICLE_OUT_DIR: pathlib.Path | None = None
VOL_PATTERN: re.Pattern | None = None
PDF_GLOB: str = "*.pdf"

SUFFIX      = "liteparse"
PADDLE_ENV  = "ds_ocr"
MIN_FIG_PX  = 150
WEB_IMAGE_EXTS = {"png", "jpg", "jpeg"}
FULL_PAGE_IMAGE_AREA_FRAC = 0.65
FULL_PAGE_TEXT_CHAR_THRESHOLD = 500


def _apply_flora(name: str) -> None:
    global PDF_DIR, OUT_DIR, ARTICLE_OUT_DIR, VOL_PATTERN, PDF_GLOB
    f = load_flora(name)
    PDF_DIR = f.pdf_dir
    OUT_DIR = f.output_dir
    ARTICLE_OUT_DIR = OUT_DIR / "articles"
    VOL_PATTERN = f.vol_pattern
    PDF_GLOB = f.pdf_glob

# Path to Node 20 liteparse binary (installed via nvm)
_NVM_NODE       = pathlib.Path.home() / ".nvm/versions/node/v20.20.2/bin/node"
_LITEPARSE_BIN  = pathlib.Path.home() / ".nvm/versions/node/v20.20.2/bin/liteparse"

# Figure caption markers (French and English)
_CAPTION_RE = re.compile(
    r"^((?:Figure|Fig\.|Planche|Pl\.|Tableau|Tab\.)\s*\d+[A-Za-z]?\s*[\.\:])",
    re.IGNORECASE,
)

# Running header / footer: appears in the top 10% or bottom 5% of the page.
# These thresholds (fraction of page height) apply to the mean y of the line.
_RUNHDR_TOP_FRAC    = 0.08   # headers at ~6%, content starts at ~9.5%
_RUNHDR_BOTTOM_FRAC = 0.95

# Species treatment heading:  "Genus epithet [subsp. sub] Author …"
# Genus   ≥ 4 chars, starts uppercase
# epithet ≥ 4 lowercase ASCII chars (Linnaean names are always ASCII)
# Author  starts with uppercase letter or "("
_SPECIES_HEAD_RE = re.compile(
    r"^([A-Z][a-z]{3,})"                        # Genus
    r"\s+([a-z]{4,}[a-z\-]*)"                   # epithet
    r"(?:\s+(?:subsp|var|f)\.\s+[a-z]{3,})?"    # optional infraspecific rank
    r"\s+(?:[A-Z\(])"                            # Author begins with uppercase or (
)
# Synonym / bibliographic citation lines contain a 4-digit year "(1898)" — exclude them
_YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")
# Latin differential diagnosis lines: "Genus_gen. epithet_gen. Author similis …"
_LATIN_DIAG_RE = re.compile(r"\b(similis|affinis|differt|distincta|separatur)\b", re.IGNORECASE)

# Fix mixed-case words that are PDF font-encoding artefacts, e.g. "BiBliographie"
# Pattern: word starting with uppercase, then lowercase, then uppercase again
_MIXED_CASE_RE = re.compile(r"\b[A-Z][a-z]+[A-Z][A-Za-z]{2,}\b")
_GARBAGE_ALNUM_DIGIT_RE = re.compile(r"(?i)\b(?=\w*[A-Za-z])(?=\w*\d)\w{6,}\b")
_GARBAGE_MID_UPPER_RE = re.compile(r"\b[A-Za-z]{2,}[A-Z][A-Za-z]{2,}\b")
_GARBAGE_MID_EXT_UPPER_RE = re.compile(r"\b[^\W\d_]*[a-zà-ÿ][À-Ý][a-zà-ÿ]+\b")
_GARBAGE_PREFIX_RE = re.compile(r"^[%&$#/][A-Za-z0-9]{6,}$")
_GARBAGE_SYMBOL_RE = re.compile(r"[¶§¦]")
_GARBAGE_PAGE_SCORE_THRESHOLD = 10
_GARBAGE_MIN_PAGES = 8
_GARBAGE_PAGE_FRACTION_THRESHOLD = 0.15


def _fix_mixed_case(text: str) -> str:
    def _normalise(m):
        w = m.group()
        # Only normalise if the interior uppercase is not a recognised acronym position
        # Safe heuristic: title-case the whole word
        return w[0].upper() + w[1:].lower()
    return _MIXED_CASE_RE.sub(_normalise, text)


# ---------------------------------------------------------------------------
# Volume discovery
# ---------------------------------------------------------------------------

def discover_volumes() -> list[tuple[str, pathlib.Path]]:
    """Return sorted list of (label, pdf_path) for all volumes."""
    vols = []
    for p in PDF_DIR.glob(PDF_GLOB):
        m = VOL_PATTERN.match(p.name)
        if m:
            vols.append((m.group(1), p))

    def _sort_key(item):
        label = item[0]
        if label.endswith("bis"):
            return (int(label[:-3]), 1)
        return (int(label), 0)

    vols.sort(key=_sort_key)
    return vols


def is_already_processed(vol_label: str) -> bool:
    """Return True if any family output dir exists for this volume."""
    return any(
        (d / "text.md").exists()
        for d in OUT_DIR.iterdir()
        if d.is_dir() and f"_vol{vol_label}_{SUFFIX}" in d.name
    )


def article_id_from_path(pdf_path: pathlib.Path) -> str:
    """Return a filesystem-safe article id derived from a PDF filename."""
    article_id = pdf_path.stem.lower()
    article_id = re.sub(r"[^a-z0-9]+", "_", article_id)
    article_id = re.sub(r"_+", "_", article_id).strip("_")
    return article_id or "article"


def is_article_processed(article_id: str) -> bool:
    """Return True if an article output directory already exists."""
    return (ARTICLE_OUT_DIR / article_id / SUFFIX / "text.md").exists()


# ---------------------------------------------------------------------------
# liteparse invocation
# ---------------------------------------------------------------------------

def run_liteparse(pdf_path: pathlib.Path) -> dict:
    """Run liteparse CLI and return the parsed JSON dict."""
    cmd = [
        str(_NVM_NODE), str(_LITEPARSE_BIN),
        "parse", "--format", "json", "--no-ocr",
        str(pdf_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(
            f"liteparse failed (exit {result.returncode}):\n{result.stderr[:1000]}"
        )
    return json.loads(result.stdout)


def run_paddle_fallback(vol_label: str, force: bool = False) -> bool:
    """Run the PaddleOCR volume pipeline in its dedicated conda environment."""
    paddle_script = pathlib.Path(__file__).with_name("ocr_paddle.py")
    cmd = [
        "conda", "run", "--no-capture-output",
        "-n", PADDLE_ENV,
        "python", "-u", str(paddle_script),
        "--vol", vol_label,
    ]
    if force:
        cmd.append("--force")

    print(
        f"  Falling back to PaddleOCR ({PADDLE_ENV}) for vol {vol_label} …",
        flush=True,
    )
    try:
        result = subprocess.run(cmd, timeout=7200)
    except Exception as exc:
        print(f"  ERROR: Paddle fallback failed to start: {exc}")
        return False

    if result.returncode != 0:
        print(f"  ERROR: Paddle fallback exited with code {result.returncode}")
        return False
    return True


# ---------------------------------------------------------------------------
# Text processing helpers
# ---------------------------------------------------------------------------

def _group_lines(text_items: list[dict], y_tol: float = 2.5) -> list[list[dict]]:
    """Group textItems by y-position into visual lines, sorted left-to-right."""
    buckets: dict[int, list[dict]] = {}
    for item in text_items:
        key = round(item["y"] / y_tol)
        buckets.setdefault(key, []).append(item)
    return [
        sorted(items, key=lambda i: i["x"])
        for _, items in sorted(buckets.items())
    ]


def _line_str(items: list[dict]) -> str:
    """Join textItems into a string with a space between each token.

    Item texts already carry trailing spaces in many cases; strip them first
    so we don't get double spaces, then rejoin with a single space.
    """
    return " ".join(it["text"].strip() for it in items if it["text"].strip())


def _max_fontsize(items: list[dict]) -> float:
    return max(i["fontSize"] for i in items)


def _modal_fontsize(text_items: list[dict]) -> float:
    """Return the most common font size across all items on the page (= body size)."""
    counts: dict[float, int] = {}
    for it in text_items:
        counts[it["fontSize"]] = counts.get(it["fontSize"], 0) + 1
    return max(counts, key=counts.__getitem__)


def _page_plain_text(page_data: dict) -> str:
    return " ".join(
        it["text"].strip()
        for it in page_data.get("textItems", [])
        if it["text"].strip()
    )


def _garbage_score(text: str) -> int:
    """Return a heuristic score for mojibake / broken embedded text."""
    tokens = re.findall(r"\S+", text)
    if len(tokens) < 20:
        return 0

    mixed_digit = sum(1 for t in tokens if _GARBAGE_ALNUM_DIGIT_RE.search(t))
    mid_upper = sum(
        1 for t in tokens
        if _GARBAGE_MID_UPPER_RE.search(t)
        and not t.isupper()
        and not (t[:1].isupper() and t[1:].islower())
    )
    mid_ext_upper = sum(1 for t in tokens if _GARBAGE_MID_EXT_UPPER_RE.search(t))
    prefixed = sum(1 for t in tokens if _GARBAGE_PREFIX_RE.match(t))
    symbols = len(_GARBAGE_SYMBOL_RE.findall(text))

    return (
        mixed_digit * 3
        + mid_upper * 2
        + mid_ext_upper * 2
        + prefixed * 2
        + symbols * 2
    )


def detect_garbage_pages(pages_data: list[dict]) -> list[tuple[int, int]]:
    """Return [(page_1based, score), ...] for pages with broken embedded text."""
    bad_pages: list[tuple[int, int]] = []
    for page_idx, page_data in enumerate(pages_data, 1):
        score = _garbage_score(_page_plain_text(page_data))
        if score >= _GARBAGE_PAGE_SCORE_THRESHOLD:
            bad_pages.append((page_idx, score))
    return bad_pages


def should_fallback_to_paddle(pages_data: list[dict]) -> tuple[bool, list[tuple[int, int]]]:
    """Return whether the document's embedded text is bad enough to OCR instead."""
    bad_pages = detect_garbage_pages(pages_data)
    n_pages = len(pages_data)
    if n_pages == 0:
        return False, bad_pages

    threshold_pages = max(
        _GARBAGE_MIN_PAGES,
        int(n_pages * _GARBAGE_PAGE_FRACTION_THRESHOLD + 0.999),
    )
    return len(bad_pages) >= threshold_pages, bad_pages


# Key-section heading pattern (same as reformat_keys.py KEY_HEADING)
_KEY_HEADING_RE = re.compile(r"(?i)^(?:KEY\b|CL[EÉé]S?\b)")


def _line_avg_y(items: list[dict]) -> float:
    return sum(i["y"] for i in items) / len(items)


def _is_running_header(line_items: list[dict], page_height: float) -> bool:
    """Return True for page-number/running-header lines to suppress.

    Primary criterion: the line sits in the top 10% or bottom 5% of the page.
    """
    avg_y = _line_avg_y(line_items)
    return (avg_y < page_height * _RUNHDR_TOP_FRAC
            or avg_y > page_height * _RUNHDR_BOTTOM_FRAC)


# ---------------------------------------------------------------------------
# Per-page markdown conversion
# ---------------------------------------------------------------------------

def page_to_markdown(page_data: dict) -> tuple[list[str], list[str]]:
    """Convert one liteparse page to markdown lines.

    Returns:
        md_lines  : list of markdown-formatted lines for text.md
        captions  : list of raw caption strings (for figures.md)
    """
    text_items = page_data.get("textItems", [])
    if not text_items:
        return [], []

    page_height = page_data.get("height", 800)

    # Per-page body font size (mode); heading threshold = body * 1.15
    body_fs   = _modal_fontsize(text_items)
    head_thr  = body_fs * 1.15   # e.g. 9.5 * 1.15 ≈ 10.9 → 11pt items are headings
    major_thr = body_fs * 1.7    # very large → ## level

    lines = _group_lines(text_items)
    md_lines: list[str] = []
    captions: list[str] = []

    in_caption = False
    caption_buf: list[str] = []

    for line_items in lines:
        raw = _line_str(line_items).strip()
        if not raw:
            continue

        raw = _fix_mixed_case(raw)

        # Running header / footer (top/bottom 10%/5% of page) → skip
        if _is_running_header(line_items, page_height):
            continue

        # Figure caption detection: starts with "Figure N." / "Planche N." etc.
        if _CAPTION_RE.match(raw):
            if in_caption and caption_buf:
                captions.append(" ".join(caption_buf))
                caption_buf = []
            in_caption = True
            caption_buf = [raw]
            continue

        if in_caption:
            fs = _max_fontsize(line_items)
            # Absorb continuation lines (smaller font, starts with dash, etc.)
            if fs < head_thr and (raw.startswith("–") or not raw[0].isupper()):
                caption_buf.append(raw)
                continue
            else:
                captions.append(" ".join(caption_buf))
                caption_buf = []
                in_caption = False

        # Content-based heading: key section headings ("Clé des …", "KEY TO …")
        if _KEY_HEADING_RE.match(raw):
            md_lines.append(f"### {raw}")
            continue

        # Content-based heading: species treatment ("Genus epithet Author …")
        # Exclude synonym/citation lines which always contain a year like "(1898)"
        if (_SPECIES_HEAD_RE.match(raw)
                and not _YEAR_RE.search(raw)
                and not _LATIN_DIAG_RE.search(raw)):
            md_lines.append(f"### {raw}")
            continue

        # Font-size-based heading (relative to this page's body size)
        fs = _max_fontsize(line_items)
        if fs >= major_thr:
            md_lines.append(f"## {raw}")
        elif fs >= head_thr:
            md_lines.append(f"### {raw}")
        else:
            md_lines.append(raw)

    if in_caption and caption_buf:
        captions.append(" ".join(caption_buf))

    return md_lines, captions


# ---------------------------------------------------------------------------
# Embedded-text detection
# ---------------------------------------------------------------------------

def has_embedded_text(pdf_path: pathlib.Path, sample_pages: int = 10,
                      min_avg_chars: int = 200) -> bool:
    """Return True if the PDF contains extractable text (not purely scanned).

    Samples up to `sample_pages` pages starting at page 4 (skip covers).
    """
    doc = fitz.open(str(pdf_path))
    total = 0
    n = 0
    for pi in range(3, min(3 + sample_pages, len(doc))):
        total += len(doc[pi].get_text().strip())
        n += 1
    doc.close()
    avg = total // n if n else 0
    return avg >= min_avg_chars


# ---------------------------------------------------------------------------
# Family detection
# ---------------------------------------------------------------------------

# Matches a standalone family heading: "## Ancistrocladaceae" (no comma after)
_FAM_HEADING_RE = re.compile(
    r"^##\s+([A-Z][a-z]+(?:aceae|eaceae))\s*$",
    re.IGNORECASE,
)


def _normalise_letters(text: str) -> str:
    """Lowercase text with only ASCII letters preserved."""
    return re.sub(r"[^A-Za-z]+", "", text).lower()


def _family_name_from_normalised(norm: str) -> str | None:
    """Return a normalized family name from the front of `norm`, if present."""
    m = re.match(r"^([a-z]{4,30}(?:aceae|eaceae))", norm)
    if not m:
        return None
    fam = m.group(1)
    return fam[0].upper() + fam[1:]


def _toc_family_names(
    page_results: list[tuple[int, list[str], list[str]]],
) -> list[str]:
    """Return family names listed in a table of contents, if one is found."""
    fams: list[str] = []
    seen: set[str] = set()
    in_toc = False

    for _, md_lines, _ in page_results[:20]:
        for line in md_lines:
            plain = re.sub(r"^#+\s*", "", line).strip()
            if not plain:
                continue
            if "table des matières" in plain.lower() or "contents" in plain.lower():
                in_toc = True
                continue
            if not in_toc:
                continue

            m = re.match(r"^([A-Z][A-Za-z-]+(?:aceae|eaceae))\s+\.{3,}\s+\d+\s*$", plain)
            if not m:
                continue

            fam = m.group(1)
            if fam not in seen:
                fams.append(fam)
                seen.add(fam)

    return fams


def _family_heading_name(line: str, known_families: list[str] | None = None) -> str | None:
    """Return family name for a heading line, handling spaced small-caps OCR."""
    plain = re.sub(r"^#+\s*", "", line).strip()
    if not plain:
        return None

    norm = _normalise_letters(plain)
    if not norm:
        return None

    if known_families:
        for fam in known_families:
            fam_norm = _normalise_letters(fam)
            if norm.startswith(fam_norm):
                return fam

    return _family_name_from_normalised(norm)


def detect_families(
    page_results: list[tuple[int, list[str], list[str]]],
) -> list[tuple[str, int]]:
    """Return [(family_name, first_page_1based), …] in document order.

    Scans md_lines for family headings, including spaced small-caps text such as
    "### Ar IS t O l OC h IACEAE Juss. (1789)".
    Consecutive duplicates are collapsed (same family heading on adjacent pages).
    """
    families: list[tuple[str, int]] = []
    toc_families = _toc_family_names(page_results)
    for page_1based, md_lines, _ in page_results:
        for line in md_lines:
            name = None
            m = _FAM_HEADING_RE.match(line)
            if m:
                name = m.group(1).capitalize()
            elif line.startswith("#"):
                name = _family_heading_name(line, toc_families)

            if name:
                if not families or families[-1][0] != name:
                    families.append((name, page_1based))
                break  # only one family heading per page
    return families


def page_family_map(
    families: list[tuple[str, int]],
    n_pages: int,
    fallback: str,
) -> dict[int, str]:
    """Build {page_1based: family_name} for every page."""
    mapping: dict[int, str] = {}
    for i, (fam, start) in enumerate(families):
        end = families[i + 1][1] if i + 1 < len(families) else n_pages + 1
        for p in range(start, end):
            mapping[p] = fam
    # Pages before the first family heading (covers, TOC, preface)
    first_start = families[0][1] if families else n_pages + 1
    for p in range(1, first_start):
        mapping[p] = fallback
    return mapping


# ---------------------------------------------------------------------------
# Figure extraction (PyMuPDF) — routes each figure to its family directory
# ---------------------------------------------------------------------------

def extract_figures_routed(
    pdf_path: pathlib.Path,
    p2fam: dict[int, str],
    fam_dirs: dict[str, pathlib.Path],
    fallback_fam: str,
    page_caption_counts: dict[int, int] | None = None,
) -> list[dict]:
    """Extract all embedded images, writing each to its family's figures/ dir.

    Returns list of dicts: {fig_num, page_1based, family, filename, path, xref}.
    """
    doc = fitz.open(str(pdf_path))
    figures: list[dict] = []
    seen_xrefs: set[int] = set()
    fig_num = 0

    for page_idx in range(len(doc)):
        page_1based = page_idx + 1
        fam       = p2fam.get(page_1based, fallback_fam)
        fig_dir   = fam_dirs[fam] / "figures"
        fig_dir.mkdir(parents=True, exist_ok=True)

        for img_info in doc[page_idx].get_images(full=True):
            xref    = img_info[0]
            w, h    = img_info[2], img_info[3]
            if w < MIN_FIG_PX or h < MIN_FIG_PX or xref in seen_xrefs:
                continue
            if (page_caption_counts is not None
                and len(doc[page_idx].get_text().strip()) >= FULL_PAGE_TEXT_CHAR_THRESHOLD
                and page_caption_counts.get(page_1based, 0) == 0):
                continue
            if _is_probable_page_background(doc[page_idx], xref):
                continue
            seen_xrefs.add(xref)

            filename = _write_figure_bytes(doc, doc[page_idx], xref, fig_dir, fig_num, page_1based)
            if filename is None:
                continue

            figures.append(dict(
                fig_num=fig_num,
                page_1based=page_1based,
                family=fam,
                filename=filename,
                path=str(fig_dir / filename),
                xref=xref,
            ))
            fig_num += 1

    doc.close()
    return figures


def _write_figure_bytes(doc: fitz.Document, page: fitz.Page, xref: int, fig_dir: pathlib.Path,
                        fig_num: int, page_1based: int) -> str | None:
    """Write one extracted figure in a wiki-friendly format.

    Native PNG/JPEG assets are preserved. Other embedded formats such as JBIG2
    are rasterized through PyMuPDF and written as PNG.
    """
    try:
        img_data = doc.extract_image(xref)
    except Exception:
        return None

    ext = img_data.get("ext", "png").lower()
    if _is_image_mask_xref(doc, xref):
        return _write_rendered_figure(page, xref, fig_dir, fig_num, page_1based)

    if ext in WEB_IMAGE_EXTS:
        filename = f"fig_{fig_num:03d}_p{page_1based:04d}.{ext}"
        (fig_dir / filename).write_bytes(img_data["image"])
        return filename

    return _write_rasterized_figure(doc, xref, fig_dir, fig_num, page_1based)


def _write_rasterized_figure(doc: fitz.Document, xref: int, fig_dir: pathlib.Path,
                             fig_num: int, page_1based: int) -> str | None:
    try:
        pix = fitz.Pixmap(doc, xref)
    except Exception:
        return None

    if pix.colorspace is not None and pix.colorspace.n not in (1, 3):
        pix = fitz.Pixmap(fitz.csRGB, pix)

    filename = f"fig_{fig_num:03d}_p{page_1based:04d}.png"
    pix.save(fig_dir / filename)
    return filename


def _write_rendered_figure(page: fitz.Page, xref: int, fig_dir: pathlib.Path,
                           fig_num: int, page_1based: int) -> str | None:
    """Render an image-mask figure from the page so PDF stencil semantics are preserved."""
    try:
        rects = page.get_image_rects(xref)
    except Exception:
        return None
    if not rects:
        return None

    clip = fitz.Rect(rects[0])
    for rect in rects[1:]:
        clip.include_rect(rect)

    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False)
    except Exception:
        return None

    filename = f"fig_{fig_num:03d}_p{page_1based:04d}.png"
    pix.save(fig_dir / filename)
    return filename


def _is_image_mask_xref(doc: fitz.Document, xref: int) -> bool:
    """Return True when the PDF image XObject is a monochrome image mask."""
    try:
        obj = doc.xref_object(xref, compressed=False)
    except Exception:
        return False
    return "/ImageMask true" in obj


def _is_probable_page_background(page: fitz.Page, xref: int) -> bool:
    """Return True if an embedded image is probably a full-page scan/background.

    For born-digital article PDFs, publishers sometimes embed a near full-page
    bitmap behind a searchable text layer. Those should not become wiki figures.
    """
    text_len = len(page.get_text().strip())
    if text_len < FULL_PAGE_TEXT_CHAR_THRESHOLD:
        return False

    page_area = page.rect.width * page.rect.height
    try:
        rects = page.get_image_rects(xref)
    except Exception:
        return False

    for rect in rects:
        area_frac = (rect.width * rect.height) / page_area if page_area else 0
        if area_frac >= FULL_PAGE_IMAGE_AREA_FRAC:
            return True
    return False


# ---------------------------------------------------------------------------
# Main volume processor
# ---------------------------------------------------------------------------

def process_volume(pdf_path: pathlib.Path, vol_label: str,
                   force: bool = False,
                   allow_paddle_fallback: bool = True) -> bool:
    """Parse one volume and write per-family output dirs.

    Each family in the volume gets its own directory:
      ocr_output/{Family}_vol{label}_{SUFFIX}/
        text.md, figures.md, figures/, metadata.json
    """
    t0 = time.time()

    # ---- check for embedded text ----
    if not has_embedded_text(pdf_path):
        print(f"  SKIP: {pdf_path.name} appears to be a scanned PDF (no embedded text).")
        print(f"        Use ocr_with_mineru.py or ocr_paddle.py instead.")
        return False

    # ---- liteparse ----
    print(f"  Running liteparse on {pdf_path.name} …", flush=True)
    try:
        parsed = run_liteparse(pdf_path)
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return False

    pages_data = parsed.get("pages", [])
    n_pages    = len(pages_data)
    print(f"  liteparse: {n_pages} pages", flush=True)

    if allow_paddle_fallback:
        use_paddle, bad_pages = should_fallback_to_paddle(pages_data)
        if use_paddle:
            sample = ", ".join(f"p.{p}({score})" for p, score in bad_pages[:8])
            extra = "" if len(bad_pages) <= 8 else ", …"
            print(
                f"  Embedded text looks corrupted on {len(bad_pages)}/{n_pages} pages "
                f"({sample}{extra})",
                flush=True,
            )
            return run_paddle_fallback(vol_label, force=force)

    # ---- per-page markdown ----
    page_results: list[tuple[int, list[str], list[str]]] = []
    for page_idx, page_data in enumerate(pages_data):
        md_lines, captions = page_to_markdown(page_data)
        page_results.append((page_idx + 1, md_lines, captions))
    page_caption_counts = {page_1based: len(captions) for page_1based, _, captions in page_results}

    # ---- detect families ----
    families = detect_families(page_results)
    fallback  = families[0][0] if families else f"vol{vol_label}"
    if not families:
        families = [(fallback, 1)]
        print(f"  No family headings found — using single folder '{fallback}'")
    else:
        print(f"  Families: {', '.join(f for f, _ in families)}", flush=True)

    p2fam = page_family_map(families, n_pages, fallback)

    # ---- create output directories ----
    fam_dirs: dict[str, pathlib.Path] = {}
    for fam, _ in families:
        d = OUT_DIR / f"{fam}_vol{vol_label}_{SUFFIX}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(d / "figures", ignore_errors=True)
        fam_dirs[fam] = d

    # ---- extract figures → per-family dirs ----
    print(f"  Extracting figures …", flush=True)
    figures = extract_figures_routed(pdf_path, p2fam, fam_dirs, fallback, page_caption_counts)
    print(f"  {len(figures)} figures extracted", flush=True)

    # ---- build per-family accumulators ----
    fam_text:  dict[str, list[str]] = {f: [] for f, _ in families}
    fam_figmd: dict[str, list[str]] = {f: [] for f, _ in families}
    fam_chars: dict[str, int]       = {f: 0  for f, _ in families}

    page_figs: dict[int, list[dict]] = {}
    for fig in figures:
        page_figs.setdefault(fig["page_1based"], []).append(fig)

    for page_1based, md_lines, captions in page_results:
        fam  = p2fam.get(page_1based, fallback)
        tl   = fam_text[fam]
        fm   = fam_figmd[fam]

        tl.append(f"<!-- page {page_1based} -->\n")

        page_figs_list = page_figs.get(page_1based, [])
        for fig in page_figs_list:
            tl.append(
                f"[Figure {fig['fig_num']} (p.{page_1based}) — see figures.md]\n"
            )

        for i, fig in enumerate(page_figs_list):
            caption_text = captions[i] if i < len(captions) else "(no caption)"
            fm.append(
                f"## Figure {fig['fig_num']} (page {page_1based})\n"
                f"![{fig['filename']}](figures/{fig['filename']})\n"
                f"*Caption:* {caption_text}\n\n---"
            )

        for i in range(len(page_figs_list), len(captions)):
            tl.append(f"<!-- caption: {captions[i]} -->\n")

        for line in md_lines:
            tl.append(line + "\n")
            fam_chars[fam] += len(line)

        tl.append("\n---\n\n")

    # ---- write outputs per family ----
    elapsed = time.time() - t0
    for fam, _ in families:
        d       = fam_dirs[fam]
        fam_figs = [f for f in figures if f["family"] == fam]

        (d / "figures.md").write_text(
            "\n\n".join(fam_figmd[fam]) + "\n", encoding="utf-8"
        )
        # text.md written last (sentinel)
        (d / "text.md").write_text(
            "".join(fam_text[fam]), encoding="utf-8"
        )
        (d / "metadata.json").write_text(
            json.dumps({
                "model":        "liteparse+pymupdf",
                "source":       str(pdf_path),
                "vol_label":    vol_label,
                "family":       fam,
                "page_count":   sum(1 for p, f in p2fam.items() if f == fam),
                "figure_count": len(fam_figs),
                "char_count":   fam_chars[fam],
                "elapsed_s":    round(elapsed, 1),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(
            f"  → {d.name}  "
            f"({fam_chars[fam]:,} chars, {len(fam_figs)} figs)"
        )

    return True


def process_article(pdf_path: pathlib.Path, article_id: str) -> bool:
    """Parse one born-digital article PDF into a single article output dir."""
    t0 = time.time()

    if not has_embedded_text(pdf_path):
        print(f"  SKIP: {pdf_path.name} appears to be a scanned PDF (no embedded text).")
        print(f"        Use ocr_with_mineru.py or ocr_paddle.py instead.")
        return False

    print(f"  Running liteparse on {pdf_path.name} …", flush=True)
    try:
        parsed = run_liteparse(pdf_path)
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return False

    pages_data = parsed.get("pages", [])
    n_pages = len(pages_data)
    print(f"  liteparse: {n_pages} pages", flush=True)

    page_results: list[tuple[int, list[str], list[str]]] = []
    for page_idx, page_data in enumerate(pages_data):
        md_lines, captions = page_to_markdown(page_data)
        page_results.append((page_idx + 1, md_lines, captions))
    page_caption_counts = {page_1based: len(captions) for page_1based, _, captions in page_results}

    out_dir = ARTICLE_OUT_DIR / article_id / SUFFIX
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(out_dir / "figures", ignore_errors=True)

    p2fam = {page_idx + 1: article_id for page_idx in range(n_pages)}
    figures = extract_figures_routed(pdf_path, p2fam, {article_id: out_dir}, article_id, page_caption_counts)
    print(f"  {len(figures)} figures extracted", flush=True)

    text_lines: list[str] = []
    fig_lines: list[str] = []
    char_count = 0
    page_figs: dict[int, list[dict]] = {}
    for fig in figures:
        page_figs.setdefault(fig["page_1based"], []).append(fig)

    for page_1based, md_lines, captions in page_results:
        text_lines.append(f"<!-- page {page_1based} -->\n")
        page_figs_list = page_figs.get(page_1based, [])

        for fig in page_figs_list:
            text_lines.append(
                f"[Figure {fig['fig_num']} (p.{page_1based}) — see figures.md]\n"
            )

        for i, fig in enumerate(page_figs_list):
            caption_text = captions[i] if i < len(captions) else "(no caption)"
            fig_lines.append(
                f"## Figure {fig['fig_num']} (page {page_1based})\n"
                f"![{fig['filename']}](figures/{fig['filename']})\n"
                f"*Caption:* {caption_text}\n\n---"
            )

        for i in range(len(page_figs_list), len(captions)):
            text_lines.append(f"<!-- caption: {captions[i]} -->\n")

        for line in md_lines:
            text_lines.append(line + "\n")
            char_count += len(line)

        text_lines.append("\n---\n\n")

    elapsed = time.time() - t0
    (out_dir / "figures.md").write_text(
        "\n\n".join(fig_lines) + ("\n" if fig_lines else ""),
        encoding="utf-8",
    )
    (out_dir / "text.md").write_text("".join(text_lines), encoding="utf-8")
    (out_dir / "metadata.json").write_text(
        json.dumps({
            "model": "liteparse+pymupdf",
            "kind": "article",
            "source": str(pdf_path),
            "article_id": article_id,
            "page_count": n_pages,
            "figure_count": len(figures),
            "char_count": char_count,
            "elapsed_s": round(elapsed, 1),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  → {out_dir}  ({char_count:,} chars, {len(figures)} figs)")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse botanical PDFs with liteparse (embedded text) + PyMuPDF (figures).",
    )
    parser.add_argument("--vol",        help="Volume number (e.g. 60)")
    parser.add_argument("--pdf",        help="Path to one article PDF to parse")
    parser.add_argument("--article-id", help="Output article id for --pdf mode")
    parser.add_argument("--all",        action="store_true", help="Process all volumes")
    parser.add_argument("--start-from", type=int, default=0,
                        help="Skip volumes numerically before this number")
    parser.add_argument("--force",      action="store_true",
                        help="Re-process even if already done")
    parser.add_argument("--no-paddle-fallback", action="store_true",
                        help="Disable automatic fallback to ocr_paddle.py when embedded text looks corrupt")
    add_flora_arg(parser)
    args = parser.parse_args()
    _apply_flora(args.flora)

    if not _LITEPARSE_BIN.exists():
        print(f"ERROR: liteparse not found at {_LITEPARSE_BIN}")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ARTICLE_OUT_DIR.mkdir(parents=True, exist_ok=True)

    selected = sum(bool(x) for x in (args.vol, args.all, args.pdf))
    if selected != 1:
        parser.error("Specify exactly one of --vol, --all, or --pdf.")

    if args.article_id and not args.pdf:
        parser.error("--article-id requires --pdf.")

    if args.pdf:
        pdf_path = pathlib.Path(args.pdf).expanduser()
        if not pdf_path.is_absolute():
            pdf_path = pathlib.Path.cwd() / pdf_path
        if not pdf_path.exists():
            print(f"ERROR: PDF not found: {pdf_path}")
            sys.exit(1)
        article_id = args.article_id or article_id_from_path(pdf_path)
        if not args.force and is_article_processed(article_id):
            print(f"Already processed article {article_id!r}  (use --force to redo)")
            sys.exit(0)
        print(f"Processing article {article_id} …")
        ok = process_article(pdf_path, article_id)
        sys.exit(0 if ok else 1)

    if args.vol:
        vols = discover_volumes()
        match = [v for v in vols if v[0] == args.vol]
        if not match:
            print(f"ERROR: volume {args.vol!r} not found in {PDF_DIR}")
            sys.exit(1)
        label, pdf_path = match[0]
        if not args.force and is_already_processed(label):
            print(f"Already processed vol {label}  (use --force to redo)")
            sys.exit(0)
        print(f"Processing vol {label} …")
        ok = process_volume(
            pdf_path, label,
            force=args.force,
            allow_paddle_fallback=not args.no_paddle_fallback,
        )
        sys.exit(0 if ok else 1)

    if args.all:
        vols = discover_volumes()
        if args.start_from:
            def _num(label):
                return int(label.rstrip("bis")) if label.endswith("bis") else int(label)
            vols = [v for v in vols if _num(v[0]) >= args.start_from]
        print(f"Processing {len(vols)} volumes …")
        ok_count = 0
        for i, (label, pdf_path) in enumerate(vols, 1):
            if not args.force and is_already_processed(label):
                print(f"[{i}/{len(vols)}] vol{label} — already done, skipping")
                ok_count += 1
                continue
            print(f"[{i}/{len(vols)}] vol{label} …")
            if process_volume(
                pdf_path, label,
                force=args.force,
                allow_paddle_fallback=not args.no_paddle_fallback,
            ):
                ok_count += 1
        print(f"Done: {ok_count}/{len(vols)} volumes processed.")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
