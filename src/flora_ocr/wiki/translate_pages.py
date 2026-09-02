"""Translate the source-language diagnosis on generated wiki pages.

gen_genus.py emits the diagnosis verbatim in the source language under a
``<!-- TODO:translate -->`` marker rather than inventing an English one. This
pass replaces that block with an English translation and removes the marker.

Botanical description prose is formulaic and repetitive, so this runs on Haiku —
the same model and the same conventions as ``flora_ocr.pipeline.translate``,
which translates the volume text. Reads ANTHROPIC_API_KEY from the environment.

Idempotent: a page without the marker is skipped, so re-running costs nothing and
cannot double-translate. The original French is preserved in the OCR sources,
which are never written to, so a bad translation is always recoverable by
re-running the generator.

Usage
-----
    python -m flora_ocr.wiki.translate_pages --dir wiki/genera --limit 5 --dry-run
    python -m flora_ocr.wiki.translate_pages --dir wiki/genera --family Leguminosae
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import anthropic

from flora_ocr.flora import REPO_ROOT

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MARKER_RE = re.compile(r"<!-- TODO:translate[^>]*-->\n+")
SECTION_RE = re.compile(
    r"(## Diagnosis\n\n)<!-- TODO:translate[^>]*-->\n+(.*?)(\n## )", re.S
)

PROMPT = """\
Translate this botanical genus description from French to English.

Rules:
1. Translate the French prose to natural English, using standard botanical
   English terminology (imparipinnate, glabrous, pubescent, drupe, etc.).
2. Preserve EXACTLY, character-for-character:
   - Latin scientific names and author citations
   - numbers, measurements and ranges (0,5-2 cm stays as 0.5-2 cm — convert the
     French decimal comma to a point, but change nothing else)
   - markdown structure and any bold or italic markers
3. Keep the paragraph structure. Do not merge or reorder paragraphs.
4. If the text contains a dichotomous key, translate it and keep its numbering
   and indentation exactly.
5. Silently fix obvious OCR artefacts in ordinary words (e.g. "Pétioiules" →
   "Pétiolules" before translating). Do not "fix" scientific names — if a name
   looks corrupt, translate around it and leave it as it stands.
6. Output ONLY the translated text. No preamble, no explanation, no code fences.

Text to translate:

"""


def translate(client, model: str, text: str) -> str:
    resp = client.messages.create(
        model=model,
        max_tokens=8000,
        messages=[{"role": "user", "content": PROMPT + text}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def page_family(text: str) -> str | None:
    m = re.search(r"^family:\s*(\S+)", text, re.M)
    return m.group(1) if m else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(REPO_ROOT / "wiki" / "genera"))
    ap.add_argument("--family", help="only pages whose frontmatter names this family")
    ap.add_argument("--limit", type=int, help="stop after N pages")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--interval", type=float, default=0.5,
                    help="seconds between requests (default 0.5)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    paths = sorted(Path(args.dir).glob("*.md"))
    todo = []
    for p in paths:
        s = p.read_text(encoding="utf-8")
        if "TODO:translate" not in s:
            continue
        if args.family and page_family(s) != args.family:
            continue
        todo.append(p)

    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print("nothing to translate")
        return 0

    print(f"{len(todo)} page(s) to translate with {args.model}")
    if args.dry_run:
        for p in todo:
            print(f"  would translate {p.name}")
        return 0

    client = anthropic.Anthropic()
    done = failed = 0
    for i, p in enumerate(todo, 1):
        s = p.read_text(encoding="utf-8")
        m = SECTION_RE.search(s)
        if not m:
            print(f"  SKIP {p.name}: no diagnosis block found")
            continue
        head, body, tail = m.group(1), m.group(2).strip(), m.group(3)
        if not body:
            p.write_text(MARKER_RE.sub("", s), encoding="utf-8")
            continue
        try:
            english = translate(client, args.model, body)
        except anthropic.RateLimitError:
            print("  rate limited — backing off 30 s")
            time.sleep(30)
            try:
                english = translate(client, args.model, body)
            except Exception as exc:                       # noqa: BLE001
                print(f"  FAIL {p.name}: {exc}")
                failed += 1
                continue
        except Exception as exc:                           # noqa: BLE001
            print(f"  FAIL {p.name}: {exc}")
            failed += 1
            continue

        p.write_text(s[: m.start()] + head + english + "\n" + tail + s[m.end():],
                     encoding="utf-8")
        done += 1
        print(f"  [{i}/{len(todo)}] {p.name} ({len(body)} -> {len(english)} chars)")
        time.sleep(args.interval)

    print(f"translated {done}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
