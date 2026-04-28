"""Build a portable JSON data file from a botanical key markdown source.

The JSON file can be loaded by key_app.py (or any other consumer) without
needing the original markdown or the reformat_keys parser at runtime.

Usage
-----
  python build_key_data.py \\
      --source  ocr_output/vol11_paddle/text_en.md \\
      --figures ocr_output/vol11_paddle/figures.md \\
      --fig-dir ocr_output/vol11_paddle/figures \\
      --title   "Flore du Gabon Vol. 11" \\
      --default-key genera_myrtaceae \\
      --genus-links "Eugenia:species_eugenia,Syzygium:species_syzygium,Psidium:species_psidium" \\
      --patches patches_vol11.json \\
      --output  vol11.keys.json

Patches file (optional JSON):
  {
    "genera_myrtaceae": {
      "nodes": {
        "2": {"is_terminal": true, "terminal_name": "Psidium", "leads_to": null}
      }
    }
  }

Key format
----------
Currently supports the "vol11 flat-ladder" style parsed by reformat_keys.py.
Future formats can be added as additional --format choices.
"""

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from reformat_keys import parse_couplets, collect_segments


# ---------------------------------------------------------------------------
# Terminal-name extraction (same logic as key_app.py)
# ---------------------------------------------------------------------------

def _extract_terminal_name(text: str) -> str:
    m = re.search(r'\.{2,}\s*(.+?)\.?\s*$', text)
    if m:
        name = m.group(1).strip().rstrip('.')
        name = re.sub(r'^\d+[\'"]?\.\s+', '', name).strip()
        return name
    m2 = re.search(r'([A-Z][a-z]*\.?\s+[A-Z]?[a-z]+[a-z.]*)\.?\s*$', text)
    if m2:
        return m2.group(1).strip().rstrip('.')
    return text.strip()


def _build_nodes(couplets: list) -> dict:
    lc_list = [lc for lc in couplets if lc.num >= 0]
    nodes = {}
    for i, lc in enumerate(lc_list):
        node_key = f"{lc.num}{lc.prime}"
        is_terminal = lc.is_terminal
        terminal_name = _extract_terminal_name(lc.text) if is_terminal else None
        leads_to = None

        # In vol-60 dash format, internal couplet references end with '.. N'
        # (dotted leaders + bare integer).  The parser marks these as terminal
        # because of the dots, but the "name" is really a couplet number.
        if is_terminal and terminal_name and re.fullmatch(r'\d+', terminal_name.strip()):
            leads_to = int(terminal_name.strip())
            is_terminal = False
            terminal_name = None

        if not is_terminal and leads_to is None:
            for j in range(i + 1, len(lc_list)):
                if not lc_list[j].prime:
                    leads_to = lc_list[j].num
                    break
        if node_key not in nodes:   # keep first occurrence; later duplicates are sub-key noise
            nodes[node_key] = dict(
                num=lc.num, prime=lc.prime, text=lc.text,
                is_terminal=is_terminal,
                terminal_name=terminal_name,
                leads_to=leads_to,
            )
    return nodes


# ---------------------------------------------------------------------------
# Key parsing (flat-ladder format via reformat_keys)
# ---------------------------------------------------------------------------

def parse_keys_from_markdown(source: pathlib.Path) -> dict:
    """Parse all KEY sections from a markdown file.

    Returns a dict of {key_id: key_dict}.
    key_dict keys: id, display, name, start, genus, family, nodes.
    """
    lines = source.read_text(encoding='utf-8').splitlines(keepends=True)
    segments = collect_segments(lines)

    genus_re  = re.compile(r'^#{1,6}\s+\d+\.\s+([A-Z]{2,})\b')
    family_re = re.compile(r'^#{1,4}\s+([A-Z]{4,}(?:ACEAE|EACEAE))', re.IGNORECASE)

    keys = {}
    current_genus  = None
    current_family = None

    for start, end, is_key in segments:
        heading = lines[start].strip()

        fm = family_re.match(heading)
        if fm:
            current_family = fm.group(1).title()
            current_genus  = None
        gm = genus_re.match(heading)
        if gm:
            current_genus = gm.group(1).title()

        if not is_key:
            continue

        body = [l.rstrip('\n') for l in lines[start + 1:end]]
        couplets = parse_couplets(body)
        if not any(lc.num >= 0 for lc in couplets):
            continue

        nodes = _build_nodes(couplets)
        heading_text = heading.lstrip('#').strip()
        hu = heading_text.upper()

        # Assign a stable key_id
        if 'GENERA' in hu and current_family:
            fam_slug = re.sub(r'\W+', '_', current_family.lower()).strip('_')
            key_id  = f'genera_{fam_slug}'
            display = f'Key to {current_family} Genera'
        elif current_genus:
            key_id  = f'species_{current_genus.lower()}'
            display = f'Key to {current_genus}'
        else:
            key_id  = re.sub(r'\W+', '_', heading_text.lower()).strip('_')
            display = heading_text

        if key_id in keys:   # keep first occurrence
            continue

        start_num = min(
            (lc.num for lc in couplets if lc.num >= 0 and not lc.prime),
            default=1,
        )

        # Derive genus context: for species_X keys, X is the genus
        genus = None
        if key_id.startswith('species_'):
            genus = key_id[len('species_'):]

        keys[key_id] = dict(
            id=key_id, display=display, name=heading_text,
            start=start_num, genus=genus, family=current_family,
            nodes=nodes,
        )

    return keys


# ---------------------------------------------------------------------------
# Figure parsing
# ---------------------------------------------------------------------------

_SKIP_WORDS = {'De', 'Le', 'La', 'Les', 'Du', 'Des', 'Et', 'Par', 'En', 'Un',
               'Sur', 'Avec', 'Dans', 'Pour'}

# Matches Roman-numeral plate labels: PL. I, PL.II, PL. XIV, etc.
_PLATE_RE = re.compile(r'\bPL\.?\s*([IVXLC]+)\b', re.IGNORECASE)

# Matches a species binomial in caption text (genus + epithet, possible capitals)
_BINOMIAL_RE = re.compile(r'\b([A-Z][a-z]+)\s+([A-Za-z][a-z]{2,})\b')

# Matches species headings: "### N. Genus epithet ... (PL. X)"
# Also catches running-text refs like "Genus epithet Auct. (PL. X)"
_HEADING_SPECIES_PLATE = re.compile(
    r'(?:^#{1,6}\s+\d+\.\s+)?'           # optional "### N. "
    r'([A-Z][a-z]+)\s+'                   # Genus
    r'([A-Za-z][a-z]{2,})'               # epithet
    r'[^()\n]*'                           # anything up to the plate ref
    r'\(PL\.?\s*([IVXLC]+)\)',            # (PL. X)
    re.IGNORECASE,
)


def _roman_to_int(s: str) -> int:
    vals = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100}
    s = s.upper()
    total = 0
    for i, ch in enumerate(s):
        v = vals.get(ch, 0)
        if i + 1 < len(s) and vals.get(s[i + 1], 0) > v:
            total -= v
        else:
            total += v
    return total


def parse_figures(figures_md: pathlib.Path, fig_dir: pathlib.Path) -> tuple[dict, dict]:
    """Parse figures.md.

    Returns:
        by_plate  : {"I": {"path": ..., "caption": ...}, "IX": {...}, ...}
        by_species: {"genus:epithet": {"path": ..., "caption": ...}, ...}
                    populated from binomials found in captions (supplementary).
    """
    if not figures_md.exists():
        return {}, {}

    text     = figures_md.read_text(encoding='utf-8')
    fig_re   = re.compile(r'!\[.*?\]\(figures/(fig_\S+?)\)')
    by_plate  = {}
    by_species = {}

    for block in re.split(r'\n---\n', text):
        fm = fig_re.search(block)
        if not fm:
            continue
        fig_path = str(fig_dir / fm.group(1))

        cap_m = re.search(r'\*Caption:\*\s*(.+)', block)
        caption = cap_m.group(1).strip() if cap_m and '(no caption' not in cap_m.group(1) else ''

        # Index by plate number from caption (e.g. "PL.I. —" or "PL. IX.")
        pm = _PLATE_RE.search(caption)
        if pm:
            roman = pm.group(1).upper()
            by_plate[roman] = {'path': fig_path, 'caption': caption}

        if not caption:
            continue

        # Supplement: index by every binomial found in caption
        for genus, epithet in _BINOMIAL_RE.findall(caption):
            if genus in _SKIP_WORDS:
                continue
            fig_key = f"{genus.lower()}:{epithet.lower()}"
            if fig_key not in by_species:
                by_species[fig_key] = {'path': fig_path, 'caption': caption}

    return by_plate, by_species


def parse_species_plate_refs(source: pathlib.Path) -> list[tuple[str, str, str]]:
    """Scan flora text for species headings and running-text refs with plate numbers.

    Returns list of (genus_lower, epithet_lower, roman_numeral) triples.
    """
    text = source.read_text(encoding='utf-8')
    hits = []
    seen = set()
    for m in _HEADING_SPECIES_PLATE.finditer(text):
        genus  = m.group(1).lower()
        epithet = m.group(2).lower()
        roman  = m.group(3).upper()
        key = (genus, epithet)
        if key not in seen:
            seen.add(key)
            hits.append((genus, epithet, roman))
    return hits


def build_figures(
    figures_md: pathlib.Path,
    fig_dir: pathlib.Path,
    source: pathlib.Path,
) -> dict:
    """Build the final figures dict: {"genus:epithet": {"path": ..., "caption": ...}}.

    Priority order:
    1. Flora-text species headings with explicit (PL. X) reference  ← most reliable
    2. Binomials extracted from figure captions                      ← supplementary
    """
    by_plate, by_species_from_caption = parse_figures(figures_md, fig_dir)

    # Start from caption-derived entries (lower priority)
    figures = dict(by_species_from_caption)

    # Override/supplement with heading-derived entries (higher priority)
    for genus, epithet, roman in parse_species_plate_refs(source):
        if roman in by_plate:
            figures[f"{genus}:{epithet}"] = by_plate[roman]

    return figures


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------

def apply_patches(keys: dict, patches: dict) -> None:
    """Apply manual corrections to parsed keys (in-place).

    Patch structure mirrors the key/node dicts, e.g.:
      {
        "genera_myrtaceae": {
          "nodes": {
            "2": {"is_terminal": true, "terminal_name": "Psidium", "leads_to": null}
          }
        }
      }
    """
    for key_id, key_patch in patches.items():
        if key_id not in keys:
            print(f"  [warn] patch target '{key_id}' not found — skipping")
            continue
        node_patches = key_patch.get('nodes', {})
        for node_key, np in node_patches.items():
            if node_key not in keys[key_id]['nodes']:
                print(f"  [warn] patch node '{key_id}/{node_key}' not found — skipping")
                continue
            keys[key_id]['nodes'][node_key].update(np)
        # Allow patching top-level key fields (display, start, genus, etc.)
        for field, val in key_patch.items():
            if field != 'nodes':
                keys[key_id][field] = val


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build a portable JSON key data file from a botanical markdown source.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python build_key_data.py \\\n"
            "      --source ocr_output/vol11_paddle/text_en.md \\\n"
            "      --figures ocr_output/vol11_paddle/figures.md \\\n"
            "      --fig-dir ocr_output/vol11_paddle/figures \\\n"
            "      --title 'Flore du Gabon Vol. 11' \\\n"
            "      --genus-links 'Eugenia:species_eugenia,Syzygium:species_syzygium,Psidium:species_psidium' \\\n"
            "      --patches patches_vol11.json \\\n"
            "      --output vol11.keys.json\n"
        ),
    )
    parser.add_argument('--source',      required=True, help='Input markdown file')
    parser.add_argument('--figures',     help='figures.md with plate captions')
    parser.add_argument('--fig-dir',     help='Directory containing figure image files')
    parser.add_argument('--title',       default='', help='Human-readable title for this dataset')
    parser.add_argument('--family',      default='', help='Filter to keys belonging to this family (e.g. Myrtaceae)')
    parser.add_argument('--default-key', default='', help='key_id to show first in the app')
    parser.add_argument(
        '--genus-links',
        default='',
        help='Comma-separated Genus:key_id pairs, e.g. "Eugenia:species_eugenia,Syzygium:species_syzygium"',
    )
    parser.add_argument('--patches', help='JSON file with manual node corrections')
    parser.add_argument('--output', required=True, help='Output .keys.json path')
    args = parser.parse_args()

    source = pathlib.Path(args.source)
    if not source.exists():
        print(f"Error: source file not found: {source}")
        sys.exit(1)

    print(f"Parsing keys from {source} ...")
    keys = parse_keys_from_markdown(source)
    print(f"  Found {len(keys)} key sections")

    # Filter by family if requested
    if args.family:
        family_filter = args.family.strip().lower()
        keys = {k: v for k, v in keys.items()
                if (v.get('family') or '').lower() == family_filter}
        print(f"  After family filter '{args.family}': {len(keys)} keys")

    # Figures
    figures = {}
    if args.figures and args.fig_dir:
        fig_md  = pathlib.Path(args.figures)
        fig_dir = pathlib.Path(args.fig_dir)
        print(f"Parsing figures from {fig_md} ...")
        figures = build_figures(fig_md, fig_dir, source)
        print(f"  Found {len(figures)} species→figure mappings")
    elif args.figures or args.fig_dir:
        print("Warning: both --figures and --fig-dir are needed together; skipping figures.")

    # Genus links
    genus_links = {}
    if args.genus_links:
        for pair in args.genus_links.split(','):
            pair = pair.strip()
            if ':' in pair:
                genus, kid = pair.split(':', 1)
                genus_links[genus.strip()] = kid.strip()
    print(f"Genus links: {genus_links}")

    # Patches
    if args.patches:
        patch_path = pathlib.Path(args.patches)
        if patch_path.exists():
            patches = json.loads(patch_path.read_text(encoding='utf-8'))
            print(f"Applying patches from {patch_path} ...")
            apply_patches(keys, patches)
        else:
            print(f"Warning: patches file not found: {patch_path}")

    # Default key
    default_key = args.default_key
    if not default_key and keys:
        default_key = next(iter(keys))

    # Assemble output
    data = {
        'meta': {
            'title':        args.title or source.stem,
            'family':       args.family or None,
            'source':       str(source),
            'generated_at': datetime.now(timezone.utc).isoformat(),
        },
        'default_key':  default_key,
        'genus_links':  genus_links,
        'figures':      figures,
        'keys':         keys,
    }

    out = pathlib.Path(args.output)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    key_count  = len(keys)
    node_count = sum(len(k['nodes']) for k in keys.values())
    print(f"Written {key_count} keys ({node_count} nodes) + {len(figures)} figures → {out}")


if __name__ == '__main__':
    main()
