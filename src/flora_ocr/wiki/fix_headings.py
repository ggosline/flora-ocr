"""Demote the stray sub-headings sitting inside a genus diagnosis.

The OCR reads a bolded or letter-spaced opening line as a markdown heading, and
early runs of `gen_genus` carried it through: 118 diagnoses open with a `###`
line, so Sansevieria's whole description renders as a heading. `clean()` strips
these now, but the pages already written are not worth regenerating -- the
translation in them was paid for -- so the marker is removed in place.

Only `## Diagnosis` bodies are touched. `###` is load-bearing elsewhere: the
per-volume blocks under Treatments, the sub-key titles under Key to the
species, and the sections of an "Also treated in vol NN" block.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from flora_ocr.flora import REPO_ROOT

WIKI_DIR = REPO_ROOT / "wiki"
SECTION_RE = re.compile(r"(^## Diagnosis\n)(.*?)(?=^## |\Z)", re.S | re.M)
HEADING_RE = re.compile(r"^#{3,6}\s*", re.M)


def fix(text: str) -> str:
    return SECTION_RE.sub(lambda m: m.group(1) + HEADING_RE.sub("", m.group(2)), text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR / "genera"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    pages = headings = 0
    for path in sorted(Path(args.dir).rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        fixed = fix(text)
        if fixed == text:
            continue
        pages += 1
        headings += len(HEADING_RE.findall(text)) - len(HEADING_RE.findall(fixed))
        if not args.dry_run:
            path.write_text(fixed, encoding="utf-8")

    verb = "would demote" if args.dry_run else "demoted"
    print(f"{verb} {headings} stray headings on {pages} genus pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
