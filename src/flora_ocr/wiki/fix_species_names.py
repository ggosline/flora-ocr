"""Refile species pages saved under an OCR corruption of their genus.

`fix_links` repointed the wiki links, but the species pages themselves are
still named for the corrupt genus -- `Acoa_bellayana.md` for *Acioa*,
`Beischmiedia_fulva.md` for *Beilschmiedia* -- so the file, its frontmatter and
its title all carry a name that is not a published genus.

This renames the page, corrects `name:` and `genus:` in the frontmatter and the
`# *Genus species*` title, and repoints every inbound link in the wiki.

The corrections come from the curated maps in `fix_links` and `gen_genus`:
nothing here is inferred by string distance, for the reasons set out in
`fix_links` -- *Uvaria*/*Uraria* and *Piptostigma*/*Piliostigma* are pairs of
real genera, and merging them would invent a synonymy.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from flora_ocr.flora import REPO_ROOT
from flora_ocr.wiki.fix_links import LINK_CORRECTIONS
from flora_ocr.wiki.gen_genus import NAME_CORRECTIONS

WIKI_DIR = REPO_ROOT / "wiki"

CORRECTIONS = {**LINK_CORRECTIONS, **NAME_CORRECTIONS}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    wiki = Path(args.dir)
    species_dir = wiki / "species"
    renames: list[tuple[Path, Path, str, str]] = []

    for path in sorted(species_dir.glob("*.md")):
        genus = path.stem.split("_")[0]
        right = CORRECTIONS.get(genus)
        if not right:
            continue
        target = species_dir / (path.name.replace(f"{genus}_", f"{right}_", 1))
        if target.exists():
            print(f"  SKIP {path.name}: {target.name} already exists")
            continue
        renames.append((path, target, genus, right))

    print(f"{len(renames)} species pages to refile")
    if args.dry_run:
        for src, dst, *_ in renames[:15]:
            print(f"  {src.name} -> {dst.name}")
        return 0

    link_map: dict[str, str] = {}
    for src, dst, wrong, right in renames:
        text = src.read_text(encoding="utf-8")
        text = re.sub(rf"^(name:\s*){wrong}\b", rf"\g<1>{right}", text, flags=re.M)
        text = re.sub(rf"^(genus:\s*){wrong}$", rf"\g<1>{right}", text, flags=re.M)
        text = re.sub(rf"^(# \*){wrong}\b", rf"\g<1>{right}", text, flags=re.M)
        text = text.replace(f"[[{wrong}]]", f"[[{right}]]")
        dst.write_text(text, encoding="utf-8")
        src.unlink()
        link_map[src.stem] = dst.stem

    touched = 0
    for path in sorted(wiki.rglob("*.md")):
        if ".obsidian" in str(path) or ".trash" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        updated = text
        for old, new in link_map.items():
            updated = re.sub(rf"\[\[{re.escape(old)}(?=[\]|\\])", f"[[{new}", updated)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            touched += 1

    print(f"refiled {len(renames)} pages, repointed links on {touched} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
