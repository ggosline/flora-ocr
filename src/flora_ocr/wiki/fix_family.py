"""Correct the family a genus or species page claims, from its bundle.

Five treatments were segmented under the wrong family, because the heading that
should have opened them was missed and a heading printed on the volume's cover
page stood in: volume 30's Capparidaceae under Brassicaceae, Chrysobalanaceae
vol 24 under Scytopetalaceae, Gesneriaceae vol 27 under Bignoniaceae, Rubiaceae
vol 17 under `Familledesrubiaceae`, Salviniaceae vol 8 under Pteridophytes.
233 pages carried the wrong `family:`.

The segmenter is fixed, but regenerating those pages would discard the
translation in them and nothing else about them is wrong, so the family is
corrected in place: the frontmatter field, the `**Family**` line, and the See
also link. The bundles are the authority -- each one's `treatment.family` is
taken from the directory the OCR splitter wrote.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from flora_ocr.flora import REPO_ROOT
from flora_ocr.wiki.gen_genus import FAMILY_ALIASES, resolve_family

# The families the old segmentation put these treatments under. Only a page
# claiming one of these is moved: several families in this wiki were split by
# hand -- Olacaceae s.l. into Aptandraceae and Octoknemaceae -- and those pages
# disagree with their bundle on purpose.
MISSEGMENTED = {
    "sources/Capparidaceae_vol30_paddle": "Brassicaceae",
    "sources/Chrysobalanaceae_vol24_paddle": "Scytopetalaceae",
    "sources/Gesneriaceae_vol27_paddle": "Bignoniaceae",
    "sources/Rubiaceae_vol17_paddle": "Familledesrubiaceae",
    "sources/Salviniaceae_vol08_paddle": "Pteridophytes",
}

BUNDLE_DIR = REPO_ROOT / "build" / "wiki_bundles"
WIKI_DIR = REPO_ROOT / "wiki"


def families_by_source(bundle_dir: Path) -> dict[str, str]:
    out = {}
    for path in sorted(bundle_dir.glob("*.json")):
        treatment = json.loads(path.read_text(encoding="utf-8"))["treatment"]
        family = resolve_family(treatment.get("family"))
        if treatment.get("source") and family:
            # the same aliases gen_genus applies, so a family the split mangled
            # is not written back over the name that was corrected from it
            out[treatment["source"]] = family
    return out


def retarget(text: str, wrong: str, right: str) -> str:
    text = re.sub(rf"^family:\s*{re.escape(wrong)}\s*$", f"family: {right}",
                  text, count=1, flags=re.M)
    text = re.sub(rf"^\*\*Family\*\*: \[\[{re.escape(wrong)}\]\]\s*$",
                  f"**Family**: [[{right}]]", text, count=1, flags=re.M)
    return re.sub(rf"^- \[\[{re.escape(wrong)}\]\]\s*$", f"- [[{right}]]",
                  text, count=1, flags=re.M)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", default=str(WIKI_DIR))
    ap.add_argument("--bundles", default=str(BUNDLE_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    families = families_by_source(Path(args.bundles))
    wiki = Path(args.wiki)
    fixed: dict[tuple[str, str], int] = {}
    for sub in ("genera", "species"):
        for path in sorted((wiki / sub).glob("*.md")):
            text = path.read_text(encoding="utf-8")
            # species pages carry `source:` at the top level, genus pages
            # carry one per treatment, indented under `treatments:`
            source = re.search(r"^\s*source:\s*(\S+)\s*$", text, re.M)
            current = re.search(r"^family:\s*(.+?)\s*$", text, re.M)
            if not (source and current):
                continue
            src = source.group(1)
            right = families.get(src)
            if not right or right == current.group(1):
                continue
            if MISSEGMENTED.get(src) != current.group(1):
                continue                 # a deliberate disagreement, not a bug
            out = retarget(text, current.group(1), right)
            if out == text:
                continue
            fixed[(current.group(1), right)] = fixed.get(
                (current.group(1), right), 0) + 1
            if not args.dry_run:
                path.write_text(out, encoding="utf-8")

    verb = "would move" if args.dry_run else "moved"
    for (wrong, right), n in sorted(fixed.items()):
        print(f"  {wrong} -> {right}: {n} pages")
    print(f"{verb} {sum(fixed.values())} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
