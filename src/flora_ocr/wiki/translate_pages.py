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
import concurrent.futures as cf
import itertools
import re
import sys
import threading
import time
from pathlib import Path

import anthropic

from flora_ocr.flora import REPO_ROOT

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MARKER_RE = re.compile(r"<!-- TODO:translate[^>]*-->\n+")
# Any marked block: from the marker to the next heading or end of page. Genus
# pages carry one (the diagnosis); species pages carry up to four (description,
# ecology, distribution, uses).
BLOCK_RE = re.compile(r"<!-- TODO:translate[^>]*-->\n+(.*?)(?=\n## |\Z)", re.S)

# Sent between blocks so one page costs one request instead of four. If the
# model returns the wrong number of parts the page falls back to a request per
# block, so a mangled separator costs latency, never content.
SENTINEL = "<<<---SECTION-BREAK--->>>"

PROMPT = """\
Translate this botanical text from French to English.

Rules:
1. Translate the French prose to natural English, using standard botanical
   English terminology (imparipinnate, glabrous, pubescent, drupe, etc.).
2. Preserve EXACTLY, character-for-character:
   - Latin scientific names and author citations
   - place names: countries, provinces, localities, collecting sites. Leave
     them in the form the source prints them. "l'Estuaire" is the name of a
     Gabonese province and stays "Estuaire", never "the Estuary";
     "Moyen-Ogooue" never becomes "Middle Ogooue"
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
7. If the text contains lines reading {sentinel}, reproduce each one
   unchanged, on its own line, in the same position and the same number of
   times. They separate independent passages.

Text to translate:

""".replace("{sentinel}", SENTINEL)


def translate(client, model: str, text: str) -> str:
    resp = client.messages.create(
        model=model,
        max_tokens=8000,
        messages=[{"role": "user", "content": PROMPT + text}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def _call(client, model: str, text: str) -> str:
    for attempt in range(2):
        try:
            return translate(client, model, text)
        except anthropic.RateLimitError:
            print("  rate limited — backing off 30 s")
            time.sleep(30)
    return translate(client, model, text)


def translate_blocks(client, model: str, bodies: list[str]) -> list[str]:
    """Translate a page's marked blocks, one request per page where possible."""
    if len(bodies) == 1:
        return [_call(client, model, bodies[0])]
    joined = f"\n\n{SENTINEL}\n\n".join(bodies)
    parts = [x.strip() for x in _call(client, model, joined).split(SENTINEL)]
    if len(parts) == len(bodies):
        return parts
    # the model dropped or invented a separator: pay for one request per block
    print(f"    separator mismatch ({len(parts)} for {len(bodies)}) — per-block")
    return [_call(client, model, b) for b in bodies]


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
    ap.add_argument("--workers", type=int, default=8,
                    help="pages translated concurrently. The work is entirely "
                         "waiting on the API -- a page takes ~35 s, so the "
                         "Leguminosae species tier is 4.6 h serially and about "
                         "half an hour at the default")
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
    counter = itertools.count(1)
    lock = threading.Lock()

    def handle(path: Path) -> bool:
        """Translate one page. Returns False on failure."""
        text = path.read_text(encoding="utf-8")
        blocks = list(BLOCK_RE.finditer(text))
        if not blocks:
            with lock:
                print(f"  SKIP {path.name}: no marked block found")
            return True

        bodies = [m.group(1).strip() for m in blocks]
        if not any(bodies):
            path.write_text(MARKER_RE.sub("", text), encoding="utf-8")
            return True

        try:
            english = translate_blocks(client, args.model, bodies)
        except Exception as exc:                           # noqa: BLE001
            with lock:
                print(f"  FAIL {path.name}: {exc}")
            return False

        # rewrite back to front so earlier offsets stay valid
        out = text
        for m, translated in zip(reversed(blocks), reversed(english)):
            out = out[: m.start(1)] + translated + "\n" + out[m.end(1):]
        path.write_text(MARKER_RE.sub("", out), encoding="utf-8")
        before, after = sum(map(len, bodies)), sum(map(len, english))
        with lock:
            print(f"  [{next(counter)}/{len(todo)}] {path.name} "
                  f"({len(bodies)} block(s), {before} -> {after} chars)")
        return True

    done = failed = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for ok in pool.map(handle, todo):
            if ok:
                done += 1
            else:
                failed += 1

    print(f"translated {done}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
