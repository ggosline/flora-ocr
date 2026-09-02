"""Repoint wiki links that name a genus by an OCR corruption.

Species pages link to their genus by whatever the scan made of the name, so a
misread heading strands every species under it: `[[Chestis]]` for *Cnestis*,
`[[Warnecka]]` for *Warneckea*. 137 link targets had no page; 37 were within
edit distance 2 of a real genus page.

Edit distance alone is not safe to act on. *Uvaria* and *Uraria* are both
published genera, as are *Exellia*/*Ruellia*, *Piptostigma*/*Piliostigma*,
*Samanea*/*Amanoa*, *Tecoma*/*Ecpoma* and *Drynaria*/*Drymaria*; merging any of
those pairs would invent a synonymy that does not exist. And the error is
sometimes on the page rather than the link -- *Anthephora*, *Diplazium*,
*Eriocoelum* and *Rhytachne* are correct as linked, and it is the generated
page whose title is corrupt.

So the map below is curated, not computed: each entry is a name that is not a
published genus, pointed at the one it is a misreading of. Names left out are
listed in SKIPPED with the reason, so the next person does not re-derive it.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from flora_ocr.flora import REPO_ROOT

WIKI_DIR = REPO_ROOT / "wiki"

# link form -> the genus it misreads. Every key was checked against the genus
# pages actually present, and none is itself a published genus name.
LINK_CORRECTIONS = {
    "Acoa": "Acioa",
    "Amphibemma": "Amphiblemma",
    "Beischmiedia": "Beilschmiedia",
    "Chestis": "Cnestis",
    "Cynomectra": "Cynometra",
    "Cyrtococcus": "Cyrtococcum",
    "Diceranolepis": "Dicranolepis",
    "Dicipltera": "Dicliptera",
    "Heteranthocia": "Heteranthoecia",
    "Hyperrhenia": "Hyparrhenia",
    "Leonardoa": "Leonardoxa",
    "Loesnera": "Loesenera",
    "Lvchnodiscus": "Lychnodiscus",
    "Manikara": "Manilkara",
    "Marantachloa": "Marantochloa",
    "Maranthochloa": "Marantochloa",
    "Microsodium": "Microsorium",
    "Pyrosiia": "Pyrrosia",
    "Renalmia": "Renealmia",
    "Stachyothyrus": "Stachyothyrsus",
    "Telecantha": "Stelechantha",
    "Tiegemella": "Tieghemella",
    "Warnecka": "Warneckea",
}

# Deliberately not corrected, with the reason.
SKIPPED = {
    "Uvaria": "a real Annonaceae genus, not a misreading of Uraria",
    "Exellia": "a real Annonaceae genus, not a misreading of Ruellia",
    "Piptostigma": "a real Annonaceae genus; Piliostigma is Leguminosae",
    "Samanea": "a real Leguminosae genus; Amanoa is Euphorbiaceae",
    "Tecoma": "a real Bignoniaceae genus; Ecpoma is Rubiaceae",
    "Tecomaria": "a real Bignoniaceae genus; Tectaria is a fern",
    "Drynaria": "a real fern genus; Drymaria is Caryophyllaceae",
    "Neprangis": "misreads Nephrangis, which has no page, not Aerangis",
    "Elionurus": "Elionurus and Elyonurus are both used for the same grass; "
                 "which spelling the page should carry is a call for the editor",
    "Anthephora": "correct as linked -- the page title Antephora is the error",
    "Diplazium": "correct as linked -- the page title Diplaziuum is the error",
    "Eriocoelum": "correct as linked -- the page title Eriocelum is the error",
    "Rhytachne": "correct as linked -- the page title Rytachne is the error",
    "Histopteris": "misreads Histiopteris; the page is correct, the link is not, "
                   "but so is the species epithet -- check the source first",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(WIKI_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    patterns = {wrong: re.compile(r"\[\[" + re.escape(wrong) + r"(?=[\]|\\])")
                for wrong in LINK_CORRECTIONS}

    changed = total = 0
    for path in sorted(Path(args.dir).rglob("*.md")):
        if ".obsidian" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        updated = text
        for wrong, right in LINK_CORRECTIONS.items():
            updated = patterns[wrong].sub(f"[[{right}", updated)
        if updated == text:
            continue
        total += len(re.findall(r"\[\[", text)) and 1
        changed += 1
        if not args.dry_run:
            path.write_text(updated, encoding="utf-8")

    verb = "would repoint" if args.dry_run else "repointed"
    print(f"{verb} links on {changed} pages "
          f"({len(LINK_CORRECTIONS)} corrections, {len(SKIPPED)} left alone)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
