"""Translate the French plate captions on wiki pages.

Figure captions were deliberately left out of the page translator: they sit in
a Figures section that carries no translate marker. But unlike a specimen
citation, a caption is descriptive content worth reading --

    PL. XXIV. — Afzelia pachyloba Harms : 1, feuille × 2/3; 2, fruit 16 × 11 cm

-- so it wants translating, with the plate number, the item numbering, the
magnifications and the Latin names left exactly as they stand.

Two things make this much cheaper than the page translator. Captions are short,
so many fit in one request; and a plate shared between species appears on every
one of their pages, so translating the distinct set covers 1,086 instances with
950 requests' worth of text. Both are handled here: distinct captions are
batched, then written back to every page that carries them.

Only captions that still read as French are sent, so the pass is idempotent and
a second run costs nothing.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import anthropic

from flora_ocr.flora import REPO_ROOT
from flora_ocr.wiki.translate_pages import DEFAULT_MODEL, SENTINEL

WIKI_DIR = REPO_ROOT / "wiki"
CACHE = REPO_ROOT / "build" / "caption_translations.json"

CAPTION_RE = re.compile(r"^\*((?:PL|Pl|FIG|Fig)\..*)\*$", re.M)

# Words that mark a caption as still French. Deliberately common botanical
# vocabulary: a caption already in English matches none of them and is skipped.
FRENCH_RE = re.compile(
    r"\b(feuille|feuilles|fleur|fleurs|coupe|rameau|rameaux|graine|graines|"
    r"détail|aspect|général|générale|inférieure|supérieure|étamine|étamines|"
    r"pétiole|foliole|folioles|bouton|boutons|jeune|jeunes|face|fruit|fruits|"
    r"ovaire|pistil|calice|corolle|gousse|infrutescence|port|tige|racine|"
    r"écaille|écailles|poil|poils|sommet|base|entier|entière|vue|longitudinale|"
    r"transversale|ouvert|ouverte|isolé|isolée)\b", re.I)

PROMPT = """\
Translate these botanical plate captions from French to English.

Rules:
1. Preserve EXACTLY, character-for-character:
   - the plate or figure number and its punctuation (PL. XXIV. —, Fig. 3. -)
   - every Latin name and author citation
   - every item number and its punctuation (1, 2, 3 ...)
   - every magnification and measurement (× 2/3, × 8, 16 × 11 cm)
2. Translate only the descriptive words: feuille -> leaf, coupe longitudinale
   -> longitudinal section, face inférieure -> lower surface, and so on.
3. Keep each caption on ONE line. Do not merge or split captions.
4. Reproduce each {sentinel} line unchanged, on its own line, in the same
   position. They separate independent captions.
5. Output ONLY the translated captions. No preamble, no numbering of your own.

Captions:

""".replace("{sentinel}", SENTINEL)


def translate_batch(client, model: str, captions: list[str]) -> list[str]:
    joined = f"\n{SENTINEL}\n".join(captions)
    resp = client.messages.create(
        model=model, max_tokens=8000,
        messages=[{"role": "user", "content": PROMPT + joined}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    parts = [p.strip() for p in text.split(SENTINEL)]
    if len(parts) != len(captions):
        raise ValueError(f"got {len(parts)} captions for {len(captions)}")
    return parts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    pages = defaultdict(list)
    for path in sorted(Path(args.dir).rglob("*.md")):
        if ".obsidian" in str(path):
            continue
        for caption in CAPTION_RE.findall(path.read_text(encoding="utf-8")):
            pages[caption].append(path)

    todo = sorted(c for c in pages if FRENCH_RE.search(c))
    print(f"{len(pages)} distinct captions, {len(todo)} still French "
          f"across {len({p for c in todo for p in pages[c]})} pages")
    if args.dry_run or not todo:
        return 0

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [c for c in todo if c not in cache]

    batches = [todo[i:i + args.batch] for i in range(0, len(todo), args.batch)]
    client = anthropic.Anthropic()

    def run(batch: list[str]) -> dict[str, str]:
        try:
            return dict(zip(batch, translate_batch(client, args.model, batch)))
        except Exception as exc:                           # noqa: BLE001
            print(f"  batch failed ({exc}) — falling back to one at a time")
            out = {}
            for caption in batch:
                try:
                    out[caption] = translate_batch(client, args.model, [caption])[0]
                except Exception as inner:                 # noqa: BLE001
                    print(f"  FAIL {caption[:60]}: {inner}")
            return out

    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, result in enumerate(pool.map(run, batches), 1):
            cache.update(result)
            print(f"  batch {i}/{len(batches)}: {len(result)} captions")

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False),
                     encoding="utf-8")

    touched = 0
    for caption, paths in pages.items():
        english = cache.get(caption)
        if not english or english == caption:
            continue
        for path in set(paths):
            text = path.read_text(encoding="utf-8")
            updated = text.replace(f"*{caption}*", f"*{english}*")
            if updated != text:
                path.write_text(updated, encoding="utf-8")
                touched += 1

    print(f"translated {len(cache)} captions, rewrote {touched} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
