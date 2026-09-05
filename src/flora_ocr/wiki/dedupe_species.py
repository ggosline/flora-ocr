"""Clear species pages left behind under a corrupted genus name.

`fix_species_names` refiles a page saved under an OCR corruption of its genus,
but it skips the pair where the correct page already exists -- 62 of them, so
*Acioa bellayana* had an `Acoa_bellayana.md` beside it and *Warneckea* six
`Warnecka_*` pages. They are not exact duplicates: the two were translated
separately, so the prose differs in wording, and ten of them carry a section
the surviving page does not.

Those sections are moved across before the duplicate goes -- Type, Specimens
examined, Vernacular names, Synonyms, Discussion, the parts that are primary
record rather than a second rendering of the same description. Eight of the
survivors are hand-written pages, which are only ever added to.

    python -m flora_ocr.wiki.dedupe_species --dry-run
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from flora_ocr.flora import REPO_ROOT
from flora_ocr.wiki.fix_links import LINK_CORRECTIONS
from flora_ocr.wiki.gen_genus import EPITHET_CORRECTIONS, NAME_CORRECTIONS

WIKI_DIR = REPO_ROOT / "wiki"
CORRECTIONS = {**LINK_CORRECTIONS, **NAME_CORRECTIONS}

# Sections worth rescuing: primary record the other page does not hold. The
# description is not among them -- both pages describe the same treatment, so
# the second is a second translation, not new content.
CARRY = ("Synonyms", "Type", "Discussion", "Specimens examined",
         "Vernacular names")
# The surviving page's closing sections; a rescued one goes in before them.
CLOSERS = ("Notes", "Source", "See also")


def sections(text: str) -> dict[str, str]:
    return {m.group(1).strip(): m.group(2).strip()
            for m in re.finditer(r"^## (.+?)\n(.*?)(?=^## |\Z)", text, re.S | re.M)}


def insert(text: str, heading: str, body: str) -> str:
    block = f"## {heading}\n\n{body}\n\n"
    for closer in CLOSERS:
        m = re.search(rf"^## {re.escape(closer)}$", text, re.M)
        if m:
            return text[:m.start()] + block + text[m.start():]
    return text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n"


def pairs(species_dir: Path) -> list[tuple[Path, Path]]:
    out = []
    for path in sorted(species_dir.glob("*.md")):
        genus, _, epithet = path.stem.partition("_")
        right = CORRECTIONS.get(genus)
        if not right:
            continue
        epithet = EPITHET_CORRECTIONS.get(f"{right} {epithet}", epithet)
        target = species_dir / f"{right}_{epithet}.md"
        if target.exists():
            out.append((path, target))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    wiki = Path(args.dir)
    carried = deleted = 0
    for stale, keep in pairs(wiki / "species"):
        stale_text = stale.read_text(encoding="utf-8")
        keep_text = keep.read_text(encoding="utf-8")
        have, add = sections(keep_text), sections(stale_text)
        moved = []
        for heading in CARRY:
            body = add.get(heading, "")
            if not body or heading in have:
                continue
            if heading == "Synonyms" and re.search(r"^synonyms:", keep_text, re.M):
                continue                 # already recorded in the frontmatter
            keep_text = insert(keep_text, heading, body)
            moved.append(heading)
        if moved:
            carried += len(moved)
            print(f"  {stale.name} -> {keep.name}: {', '.join(moved)}")
        if not args.dry_run:
            if moved:
                keep.write_text(keep_text, encoding="utf-8")
            stale.unlink()
        deleted += 1

    verb = "would clear" if args.dry_run else "cleared"
    print(f"{verb} {deleted} duplicate pages, carrying {carried} sections across")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
