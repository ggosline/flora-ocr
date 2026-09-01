"""One-off migration: Gabon-shaped frontmatter → region-wide frontmatter.

The wiki began as a Flore du Gabon wiki and its distribution fields privileged
one country:

    distribution_gabon: [Ogooué-Ivindo, Ngounié]
    distribution_other: [Cameroon, DRC, Bas Congo]

The wiki now covers Nigeria to western DRC, with Flore du Gabon as one source
among several, so distribution is recorded uniformly for every country:

    countries: [Cameroon, Gabon]
    subdivisions:
      Gabon: [Ogooué-Ivindo, Ngounié]
    range_note: "Bas Congo"
    in_region: true

This also normalises the values, which had drifted: unaccented province names
(Ogooue-Lolo), abbreviations (DRC), and country-plus-subdivision strings
(Angola (Cabinda)).

Run once:  python -m flora_ocr.wiki.migrate_region --wiki wiki [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

from flora_ocr.flora import REPO_ROOT

# Countries wholly within the wiki's region.
REGION_CORE = {
    "Nigeria", "Cameroon", "Equatorial Guinea", "São Tomé and Príncipe",
    "Gabon", "Republic of the Congo",
}
# Countries only partly within it — the DRC west of the Congo River, and
# Angola's Cabinda exclave.
REGION_PARTIAL = {"Democratic Republic of the Congo", "Angola"}
REGION = REGION_CORE | REGION_PARTIAL

# Value normalisation. Keys are accent-folded and lowercased.
COUNTRY_ALIASES = {
    "drc": "Democratic Republic of the Congo",
    "dr congo": "Democratic Republic of the Congo",
    "d.r. congo": "Democratic Republic of the Congo",
    "zaire": "Democratic Republic of the Congo",
    "congo (kinshasa)": "Democratic Republic of the Congo",
    "democratic republic of congo": "Democratic Republic of the Congo",
    "democratic republic of the congo": "Democratic Republic of the Congo",
    "congo": "Republic of the Congo",
    "congo (brazzaville)": "Republic of the Congo",
    "republic of congo": "Republic of the Congo",
    "republic of the congo": "Republic of the Congo",
    "sao tome and principe": "São Tomé and Príncipe",
    "sao tome & principe": "São Tomé and Príncipe",
    "sao tome": "São Tomé and Príncipe",
    "equatorial guinea": "Equatorial Guinea",
    "rio muni": "Equatorial Guinea",
    "bioko": "Equatorial Guinea",
    "fernando po": "Equatorial Guinea",
    "annobon": "Equatorial Guinea",
    "ivory coast": "Côte d'Ivoire",
    "cote d'ivoire": "Côte d'Ivoire",
    "guinea-conakry": "Guinea",
    "guinea conakry": "Guinea",
}

# Entries that name a country *and* a subdivision.
COMPOUND = {
    "angola (cabinda)": ("Angola", "Cabinda"),
    "cabinda": ("Angola", "Cabinda"),
    "cameroon (bioko)": ("Equatorial Guinea", "Bioko"),
}

# Entries that are regions, not countries: they go to range_note.
NOT_A_COUNTRY = {
    "congo basin", "bas congo", "lower congo", "west africa",
    "west tropical africa", "tropical africa", "central africa",
    "upper guinea", "lower guinea", "guineo-congolian", "africa",
    "gulf of guinea", "mayombe", "east africa",
}

# Canonical spelling of the Gabonese provinces, which had lost their accents.
GABON_PROVINCES = [
    "Estuaire", "Haut-Ogooué", "Moyen-Ogooué", "Ngounié", "Nyanga",
    "Ogooué-Ivindo", "Ogooué-Lolo", "Ogooué-Maritime", "Woleu-Ntem",
]


def _fold(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s)
                if not unicodedata.combining(c))
    return s.strip().strip("'\"").lower()


_PROVINCE_BY_FOLD = {_fold(p): p for p in GABON_PROVINCES}


def canon_country(raw: str) -> tuple[str | None, str | None, str | None]:
    """(country, subdivision, range_note) for one distribution_other entry."""
    key = _fold(raw)
    if not key:
        return None, None, None
    if key in COMPOUND:
        c, sub = COMPOUND[key]
        return c, sub, None
    if key in NOT_A_COUNTRY:
        return None, None, raw.strip().strip("'\"")
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key], None, None
    # Otherwise assume it is already a country name; restore title case only if
    # it looks lowercase, and leave real names (Côte d'Ivoire) alone.
    return raw.strip().strip("'\""), None, None


def canon_province(raw: str) -> str:
    return _PROVINCE_BY_FOLD.get(_fold(raw), raw.strip().strip("'\""))


def _parse_list(value: str) -> list[str]:
    v = value.strip()
    if not (v.startswith("[") and v.endswith("]")):
        return []
    return [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]


def _fmt_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def migrate_page(text: str) -> tuple[str, bool]:
    """Rewrite one page's frontmatter. Returns (new_text, changed)."""
    if not text.startswith("---"):
        return text, False
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text, False
    fm, body = parts[1], parts[2]

    gabon = other = None
    for m in re.finditer(r"^(distribution_gabon|distribution_other):[ \t]*(.*)$",
                         fm, re.M):
        if m.group(1) == "distribution_gabon":
            gabon = _parse_list(m.group(2))
        else:
            other = _parse_list(m.group(2))
    if gabon is None and other is None:
        return text, False

    countries: list[str] = []
    subdivisions: dict[str, list[str]] = {}
    notes: list[str] = []

    if gabon:
        countries.append("Gabon")
        subdivisions["Gabon"] = [canon_province(p) for p in gabon]
    elif gabon is not None:
        # Field present but empty — the taxon is still Gabonese if a Flore du
        # Gabon treatment describes it, but we cannot assert that here.
        pass

    for entry in (other or []):
        c, sub, note = canon_country(entry)
        if note:
            notes.append(note)
        if c:
            if c not in countries:
                countries.append(c)
            if sub:
                subdivisions.setdefault(c, [])
                if sub not in subdivisions[c]:
                    subdivisions[c].append(sub)

    lines = []
    for line in fm.splitlines():
        if re.match(r"^(distribution_gabon|distribution_other):", line):
            continue
        lines.append(line)

    block = []
    if countries:
        block.append(f"countries: {_fmt_list(sorted(set(countries)))}")
    if subdivisions:
        block.append("subdivisions:")
        for c in sorted(subdivisions):
            if subdivisions[c]:
                block.append(f"  {c}: {_fmt_list(subdivisions[c])}")
    if notes:
        block.append(f'range_note: "{"; ".join(dict.fromkeys(notes))}"')
    if countries:
        block.append(f"in_region: {str(any(c in REGION for c in countries)).lower()}")

    # Insert where the old fields were: just before `treatments:` if present.
    out: list[str] = []
    placed = False
    for line in lines:
        if not placed and line.startswith("treatments:"):
            out.extend(block)
            placed = True
        out.append(line)
    if not placed:
        out.extend(block)

    return "---" + "\n".join(out).rstrip() + "\n---" + body, True


SECTION_RENAMES = [
    (re.compile(r"^## Gabonese material examined\s*$", re.M), "## Specimens examined"),
    (re.compile(r"^## Material examined \(Gabon\)\s*$", re.M), "## Specimens examined"),
]


def migrate_file(path: Path, dry: bool) -> bool:
    text = path.read_text(encoding="utf-8")
    new, changed = migrate_page(text)
    for pat, rep in SECTION_RENAMES:
        new2 = pat.sub(rep, new)
        if new2 != new:
            new, changed = new2, True
    if changed and not dry:
        path.write_text(new, encoding="utf-8")
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wiki", default=str(REPO_ROOT / "wiki"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.wiki)
    changed = 0
    for p in sorted(root.rglob("*.md")):
        if ".obsidian" in p.parts:
            continue
        if migrate_file(p, args.dry_run):
            changed += 1
            print(f"  {'would migrate' if args.dry_run else 'migrated'} {p.relative_to(root)}")
    print(f"\n{changed} pages {'would be ' if args.dry_run else ''}migrated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
