"""Carry the modern family placement down from family pages to genus and species.

Several of the families this flora is organised by no longer exist under APG:
the volume's Ulmaceae is Cannabaceae, its Bombacaceae is Malvaceae subfam.
Bombacoideae, its Flacourtiaceae was dismantled between Salicaceae and
Achariaceae. The hand-written family pages record that -- in a
`modern_placement:` frontmatter field, and for the dismantled ones a per-genus
"Modern family" column -- but nothing carried it down, so a reader arriving at
`Trema` straight from a search saw only `family: Ulmaceae`.

Nothing here is inferred. Every placement is read off a family page, and a
family with no recorded placement is left alone rather than guessed at; the
families still awaiting one need a real APG source, not this script.

    python -m flora_ocr.wiki.modern_placement --dry-run
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from flora_ocr.flora import REPO_ROOT

WIKI_DIR = REPO_ROOT / "wiki"
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
PLACEMENT_RE = re.compile(r'^modern_placement:\s*"?(.+?)"?\s*$', re.M)
# A row of the family page's genus table: | [[Oncoba]] | 9 | Salicaceae |
GENUS_ROW_RE = re.compile(
    r"^\|\s*\[\[([A-Z][A-Za-z\- ]+?)(?:\\?\|[^\]]*)?\]\]\s*\|[^|]*\|\s*\*{0,2}([^|*]+?)\*{0,2}\s*\|",
    re.M)
FIELD_RE = re.compile(r"^{field}:\s*(.+?)\s*$", re.M)
# "Malvaceae subfam. Bombacoideae" links to Malvaceae; a dismantled family has
# no single target and is only ever resolved per genus.
FAMILY_NAME_RE = re.compile(r"^([A-Z][a-z]+aceae|Compositae|Leguminosae|Gramineae"
                            r"|Umbelliferae|Labiatae|Palmae|Cruciferae)\b")
SUPERSEDED_TAG = "superseded-circumscription"


def field(text: str, name: str) -> str | None:
    m = re.search(rf"^{name}:\s*(.+?)\s*$", text, re.M)
    return m.group(1) if m else None


def read_placements(families_dir: Path) -> dict[str, dict[str, str]]:
    """{family: {"*": family-wide placement, genus: per-genus placement}}."""
    placements: dict[str, dict[str, str]] = {}
    for path in sorted(families_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        entry: dict[str, str] = {}
        front = FRONTMATTER_RE.match(text)
        if front:
            m = PLACEMENT_RE.search(front.group(1))
            if m and not m.group(1).lower().startswith("dismantled"):
                entry["*"] = m.group(1)
        if "Modern family" in text:
            for genus, modern in GENUS_ROW_RE.findall(text):
                modern = modern.strip()
                if FAMILY_NAME_RE.match(modern):
                    entry[genus.strip()] = modern
        if entry:
            placements[path.stem] = entry
    return placements


def placement_for(placements: dict[str, dict[str, str]],
                  family: str | None, genus: str | None) -> str | None:
    entry = placements.get(family or "")
    if not entry:
        return None
    modern = entry.get(genus or "") or entry.get("*")
    if not modern or modern.split()[0] == family:
        return None                     # unchanged: nothing to say
    return modern


def link_for(modern: str) -> str:
    m = FAMILY_NAME_RE.match(modern)
    if not m:
        return modern
    target = m.group(1)
    rest = modern[m.end():].strip()
    return f"[[{target}]]" + (f" {rest}" if rest else "")


def patch(text: str, modern: str, family: str) -> str:
    """Add the placement to frontmatter, the header block and See also."""
    if "modern_family:" in text:
        text = re.sub(r"^modern_family:.*$", f"modern_family: {modern}",
                      text, count=1, flags=re.M)
    else:
        text = re.sub(r"^(family:.*)$", rf"\1\nmodern_family: {modern}",
                      text, count=1, flags=re.M)
    text = re.sub(r"^(tags: \[[^\]]*)\]$",
                  lambda m: m.group(0) if SUPERSEDED_TAG in m.group(0)
                  else f"{m.group(1)}, {SUPERSEDED_TAG}]",
                  text, count=1, flags=re.M)

    line = (f"**Modern family**: {link_for(modern)} — "
            f"*{family}* as circumscribed here is superseded; see [[{family}]]")
    if "**Modern family**:" in text:
        text = re.sub(r"^\*\*Modern family\*\*:.*$", line, text, count=1, flags=re.M)
    else:
        text = re.sub(rf"^(\*\*Family\*\*: \[\[{re.escape(family)}\]\])$",
                      rf"\1\n{line}", text, count=1, flags=re.M)

    target = FAMILY_NAME_RE.match(modern)
    if target:
        link = f"- [[{target.group(1)}]] — where this taxon now belongs"
        if f"[[{target.group(1)}]]" not in text.split("## See also")[-1]:
            text = text.rstrip("\n") + "\n" + link + "\n"
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    wiki = Path(args.dir)
    placements = read_placements(wiki / "families")
    counts = {"genera": 0, "species": 0}
    unplaced: set[str] = set()

    for kind, subdir in (("genera", "genera"), ("species", "species")):
        for path in sorted((wiki / subdir).glob("*.md")):
            text = path.read_text(encoding="utf-8")
            family = field(text, "family")
            genus = field(text, "genus") or (
                field(text, "name") if kind == "genera" else None)
            modern = placement_for(placements, family, genus)
            if modern is None:
                if family in placements:
                    unplaced.add(f"{family}/{genus}")
                continue
            patched = patch(text, modern, family)
            if patched == text:
                continue
            counts[kind] += 1
            if not args.dry_run:
                path.write_text(patched, encoding="utf-8")

    verb = "would place" if args.dry_run else "placed"
    print(f"{verb} {counts['genera']} genus and {counts['species']} species pages "
          f"from {len(placements)} family pages")
    if unplaced:
        print(f"no placement recorded for {len(unplaced)}: "
              + ", ".join(sorted(unplaced)[:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
