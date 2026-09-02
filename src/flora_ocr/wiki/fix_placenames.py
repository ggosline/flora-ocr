"""Restore Gabonese province names the translator rendered into English.

`l'Estuaire` came back as "the Estuary", `Moyen-Ogooue` as "Middle Ogooue",
`Haut-Ogooue` as "Upper Ogooue", and `Ogooue-Maritime` lost its hyphen. These
are proper nouns -- the names of Gabon's provinces -- and translating them
loses the datum: a reader cannot match "the Estuary" to a province, and the
schema's `subdivisions` field cannot be populated from it. 92 pages were
affected for Estuaire alone.

The mapping is exact and one-way, so this is safe to run repeatedly. The
translator prompt now says to leave place names alone, which stops new pages
acquiring the problem.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from flora_ocr.flora import REPO_ROOT

WIKI_DIR = REPO_ROOT / "wiki"

# translated form -> the province as printed in the source
PROVINCES = {
    r"\bthe Estuary\b": "the Estuaire",
    r"\bEstuary Province\b": "Estuaire",
    r"\bMiddle Ogoou[ée]\b": "Moyen-Ogooué",
    r"\bUpper Ogoou[ée]\b": "Haut-Ogooué",
    r"\bLower Ogoou[ée]\b": "Bas-Ogooué",
    r"\bOgoou[ée] Maritime\b": "Ogooué-Maritime",
    r"\bOgoou[ée] Ivindo\b": "Ogooué-Ivindo",
    r"\bOgoou[ée] Lolo\b": "Ogooué-Lolo",
    r"\bWoleu Ntem\b": "Woleu-Ntem",
    r"\bMaritime Ogoou[ée]\b": "Ogooué-Maritime",
}


def fix(text: str) -> str:
    for pattern, replacement in PROVINCES.items():
        text = re.sub(pattern, replacement, text)
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    pages = 0
    for path in sorted(Path(args.dir).rglob("*.md")):
        if ".obsidian" in str(path) or ".trash" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        fixed = fix(text)
        if fixed == text:
            continue
        pages += 1
        if not args.dry_run:
            path.write_text(fixed, encoding="utf-8")

    verb = "would fix" if args.dry_run else "fixed"
    print(f"{verb} province names on {pages} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
