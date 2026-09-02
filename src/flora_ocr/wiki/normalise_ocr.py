"""Normalise full-width punctuation the OCR emits into ASCII.

PaddleOCR sometimes decodes European punctuation as its CJK full-width
equivalent: `I．Feuilles` for `1. Feuilles`, `；` for `;`, `，` for `,`. 124
pages carry 3,158 such characters.

It is not only ugly. Three genus pages came back from the translator byte for
byte unchanged -- Annona, Cleistopholis and Letestudoxa, each carrying a
dichotomous key dense with these characters. The corruption makes the line hard
enough to read that the model echoes it rather than translating it. Cleaning
the text is the fix; asking the translator again is not.

Punctuation only. No letters are touched, so no name can be altered.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from flora_ocr.flora import REPO_ROOT

WIKI_DIR = REPO_ROOT / "wiki"

# full-width -> ASCII. Built explicitly rather than by codepoint arithmetic so
# that every substitution is visible and reviewable.
TRANSLATIONS = {
    "！": "!", "＂": '"', "＃": "#", "＄": "$", "％": "%", "＆": "&",
    "＇": "'", "（": "(", "）": ")", "＊": "*", "＋": "+", "，": ",",
    "－": "-", "．": ".", "／": "/", "：": ":", "；": ";", "＜": "<",
    "＝": "=", "＞": ">", "？": "?", "＠": "@", "［": "[", "＼": "\\",
    "］": "]", "＾": "^", "＿": "_", "｀": "`", "｛": "{", "｜": "|",
    "｝": "}", "～": "~", "　": " ", "、": ",", "。": ".",
}
TABLE = str.maketrans(TRANSLATIONS)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    pages = replaced = 0
    for path in sorted(Path(args.dir).rglob("*.md")):
        if ".obsidian" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        cleaned = text.translate(TABLE)
        if cleaned == text:
            continue
        pages += 1
        replaced += sum(1 for a, b in zip(text, cleaned) if a != b)
        if not args.dry_run:
            path.write_text(cleaned, encoding="utf-8")

    verb = "would clean" if args.dry_run else "cleaned"
    print(f"{verb} {pages} pages, {replaced} characters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
