#!/usr/bin/env python3
"""
reclassify_headings.py — Reclassify markdown headings to reflect the
taxonomic hierarchy in Flore du Gabon OCR output.

Heading mapping:
  #     (h1)  — Volume title (kept as-is)
  ##    (h2)  — Family   e.g. MYRTACÉES, THYMÉLÉACÉES
  ###   (h3)  — Genus    e.g. 1. PSIDIUM L.
  ####  (h4)  — Species  e.g. 1. Psidium cattleanum Sabine
  #####  (h5) — Infraspecific  e.g. a) var. guineense Keay

Non-taxonomic headings (CLÉ DES ESPÈCES, Matériel étudié, etc.) are
left at their original level.

Outputs (alongside input file):
  <stem>_structured.md   — reclassified markdown
  <stem>_taxa.json       — flat JSON array of all recognized taxa
  <stem>_taxa.tsv        — TSV index (rank, name, family, genus, page, line)

Usage:
  python reclassify_headings.py path/to/text.md
"""

import json
import re
import sys
from pathlib import Path

# ── Detection helpers ──────────────────────────────────────────────────────────

# Accented uppercase letters common in French botanical names
_UC = r'A-ZÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ'
_LC = r'a-zàâæçéèêëîïôœùûüÿ'

# Family: a SINGLE ALL-CAPS word (no spaces) ending in a family suffix.
# Using [^\\s]+ (no space) prevents "INDEX DES MYRTACÉES" from matching.
FAMILY_RE = re.compile(
    rf'^[{_UC}]+(?:ACÉES|ACEAE|ACEES)\s*$'
)

# Genus: optional "N. " then ≥3-char ALL-CAPS word, then author token
# Author must start with uppercase but contain lowercase OR be a short abbreviation
# This distinguishes "1. PSIDIUM L." (genus) from "TAXA NOUVEAUX" (structural)
GENUS_HEAD_RE = re.compile(
    rf'^(?:\d+\.\s+)?([{_UC}]{{3,}})\s+([A-Z]\S*(?:\s+.*)?)\s*$'
)

def _looks_like_author(token: str) -> bool:
    """True for author abbreviations like 'L.', 'DC.', or names like 'Gaertner'."""
    # Short abbreviation: ≤4 chars ending with '.' (L., DC., Oliv., etc.)
    if len(token) <= 4 and token.endswith('.'):
        return True
    # Mixed-case name: has at least one lowercase letter
    alpha = [c for c in token if c.isalpha()]
    return bool(alpha) and any(c.islower() for c in alpha)

def _is_structural_keyword(word: str) -> bool:
    """True if the first word is a known structural (non-taxonomic) keyword."""
    return word.upper() in {
        'CLÉ', 'CLE', 'FLORE', 'GABON', 'TAXA', 'SOMMAIRE', 'INDEX',
        'PUBLIÉE', 'PUBLIEE', 'RÉPARTITION', 'REPARTITION', 'EXEMPLAIRES',
        'MATÉRIEL', 'MATERIEL', 'NOTES', 'NOM', 'NOMS', 'POUR', 'PAR',
        'VOLUME', 'TOME', 'GENRE', 'ESPÈCE', 'ESPECE',
    }

# Species (NUMBERED): "N. Genus epithet ..." — epithet may be cap or lowercase
# (historically, epithets honouring people were capitalised: Gilletii, Klaineana)
# Requiring a number prefix eliminates the bulk of false positives from
# non-taxonomic French sentences that also start Capital lowercase.
SPECIES_NUMBERED_RE = re.compile(
    rf'^(?:(\d+|[IVX]{{1,4}})\.\s+)([{_UC}][{_LC}]{{2,}})\s+'  # "N. Genus"
    rf'([{_UC}]?[{_LC}][{_LC}\-]{{1,}}|sp\.)'                    # epithet or sp.
)

# Species (UNNUMBERED, undetermined): "Genus sp. [letter]"
SPECIES_SP_RE = re.compile(
    rf'^([{_UC}][{_LC}]{{2,}})\s+sp\.'
)

# Infraspecific: letter + ")" + rank keyword
INFRASP_RE = re.compile(
    r'^[a-z]\)\s+(?:var\.|subsp\.|ssp\.|f\.|fo\.|forma)\s'
)

# ── Main classifier ────────────────────────────────────────────────────────────

RANK_LEVEL = {
    'family':       2,
    'genus':        3,
    'species':      4,
    'infraspecific': 5,
}

def classify(heading_text: str):
    """
    Return (rank_str, level_int) for a heading, or (None, None) if not taxonomic.
    rank_str is one of: 'family', 'genus', 'species', 'infraspecific'
    """
    t = heading_text.strip()

    # 1. Infraspecific (most specific pattern, check first)
    if INFRASP_RE.match(t):
        return 'infraspecific', 5

    # 2. Family
    if FAMILY_RE.match(t):
        return 'family', 2

    # 3. Genus (ALL-CAPS name + author)
    m = GENUS_HEAD_RE.match(t)
    if m:
        genus_word = m.group(1)
        rest = m.group(2)
        first_author_token = rest.split()[0] if rest else ''
        if (not _is_structural_keyword(genus_word)
                and _looks_like_author(first_author_token)):
            return 'genus', 3

    # 4. Species — numbered binomial (handles both lower and upper-case epithets)
    if SPECIES_NUMBERED_RE.match(t):
        return 'species', 4

    # 4b. Species — unnumbered undetermined (Genus sp. A)
    if SPECIES_SP_RE.match(t):
        return 'species', 4

    return None, None

# ── Page tracker ───────────────────────────────────────────────────────────────

PAGE_RE = re.compile(r'<!--\s*page\s+(\d+)\s*-->')

# ── Processing ─────────────────────────────────────────────────────────────────

def process(input_path: Path, dry_run: bool = False):
    text = input_path.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)

    out_lines = []
    taxa = []
    current_page = 0
    current_family = ''
    current_genus = ''

    changed = 0
    unchanged = 0

    for lineno, line in enumerate(lines, 1):
        # Track page markers
        pm = PAGE_RE.search(line)
        if pm:
            current_page = int(pm.group(1))

        # Attempt heading match
        hm = re.match(r'^(#{1,6})\s+(.*)', line.rstrip('\n'))
        if not hm:
            out_lines.append(line)
            continue

        orig_hashes, heading_text = hm.group(1), hm.group(2)
        rank, level = classify(heading_text)

        if rank:
            new_hashes = '#' * level
            new_line = new_hashes + ' ' + heading_text + '\n'

            if new_hashes != orig_hashes:
                if not dry_run:
                    pass  # will write new_line below
                print(f"  L{lineno:4d} p{current_page:3d}  {orig_hashes} → {new_hashes}  [{rank}]  {heading_text[:60]}")
                changed += 1
            else:
                unchanged += 1

            out_lines.append(new_line)

            # Update breadcrumb context
            if rank == 'family':
                current_family = heading_text.strip()
                current_genus = ''
            elif rank == 'genus':
                current_genus = heading_text.strip()

            taxa.append({
                'rank':   rank,
                'name':   heading_text.strip(),
                'family': current_family,
                'genus':  current_genus if rank in ('species', 'infraspecific') else '',
                'page':   current_page,
                'line':   lineno,
            })
        else:
            # Non-taxonomic heading: keep as-is
            out_lines.append(line)
            unchanged += 1

    print(f"\nHeadings changed: {changed}  |  kept as-is: {unchanged}")
    print(f"Taxa recognized: {len(taxa)}  "
          f"({sum(1 for t in taxa if t['rank']=='family')} families, "
          f"{sum(1 for t in taxa if t['rank']=='genus')} genera, "
          f"{sum(1 for t in taxa if t['rank']=='species')} species, "
          f"{sum(1 for t in taxa if t['rank']=='infraspecific')} infraspecific)")

    if dry_run:
        print("\nDry run — no files written.")
        return

    # ── Write outputs ──────────────────────────────────────────────────────────
    stem = input_path.stem  # e.g. "text"
    base = input_path.parent

    # Structured markdown
    out_md = base / (stem + '_structured.md')
    out_md.write_text(''.join(out_lines), encoding='utf-8')
    print(f"\nMarkdown → {out_md}")

    # Flat JSON array
    out_json = base / (stem + '_taxa.json')
    out_json.write_text(json.dumps(taxa, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"JSON     → {out_json}")

    # TSV (easy for pandas / spreadsheets)
    tsv_lines = ['rank\tname\tfamily\tgenus\tpage\tline\n']
    for t in taxa:
        tsv_lines.append(
            f"{t['rank']}\t{t['name']}\t{t['family']}\t{t['genus']}\t{t['page']}\t{t['line']}\n"
        )
    out_tsv = base / (stem + '_taxa.tsv')
    out_tsv.write_text(''.join(tsv_lines), encoding='utf-8')
    print(f"TSV      → {out_tsv}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    args = sys.argv[1:]
    dry = '--dry-run' in args
    paths = [a for a in args if not a.startswith('--')]

    if not paths:
        paths = ['/mnt/e/FloreDuGabon/ocr_output/vol11_paddle/text.md']

    for p in paths:
        fp = Path(p)
        if not fp.exists():
            print(f"File not found: {fp}", file=sys.stderr)
            sys.exit(1)
        print(f"\n=== {fp} ===")
        process(fp, dry_run=dry)
