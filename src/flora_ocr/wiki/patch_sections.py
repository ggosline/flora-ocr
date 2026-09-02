"""Add sections a page is missing, without disturbing what it already has.

Two fixes landed after the pages were written and translated: keys are now
pulled out of the diagnosis whole instead of being cut off by the character
limit, and liteparse figures are recovered from figures.md. Regenerating the
pages would apply both -- and discard every translation, some 5,000 pages of
it, to be paid for again.

So the page is regenerated in memory, its sections compared with the ones on
disk, and only the missing sections are inserted, in the position the fresh
page puts them. Everything already on the page -- translated prose, hand-written
notes, authored tables -- is left untouched.
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

from flora_ocr.flora import REPO_ROOT
from flora_ocr.wiki import gen_genus, gen_species

WIKI_DIR = REPO_ROOT / "wiki"

SECTION_RE = re.compile(r"^## (.+?)$", re.M)

# Sections worth back-filling. Anything else a regenerated page might differ on
# is left alone: the point is to add what is missing, not to relitigate content.
WANTED = ("Key to the species", "Figures")


def split_sections(text: str) -> list[tuple[str, str]]:
    """[(heading, body)] plus a leading ('', preamble) entry."""
    marks = list(SECTION_RE.finditer(text))
    out = [("", text[:marks[0].start()] if marks else text)]
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((m.group(1).strip(), text[m.end():end].strip("\n")))
    return out


def patch(existing: str, fresh: str) -> str | None:
    have = {h for h, _ in split_sections(existing)}
    fresh_sections = split_sections(fresh)
    order = [h for h, _ in fresh_sections]
    additions = [(h, b) for h, b in fresh_sections
                 if h in WANTED and h not in have and b.strip()]
    if not additions:
        return None

    out = existing
    for heading, body in additions:
        block = f"## {heading}\n\n{body.strip()}\n\n"
        # insert before the first section that follows it in the fresh page
        after = order[order.index(heading) + 1:]
        anchor = None
        for nxt in after:
            m = re.search(rf"^## {re.escape(nxt)}$", out, re.M)
            if m:
                anchor = m.start()
                break
        out = (out[:anchor] + block + out[anchor:]) if anchor is not None \
            else out.rstrip("\n") + "\n\n" + block
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rank", choices=["genus", "species"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    bundles = gen_genus.load_bundles(None, None)
    out_dir = WIKI_DIR / ("genera" if args.rank == "genus" else "species")

    fresh_pages: dict[str, str] = {}
    if args.rank == "genus":
        by_genus = defaultdict(list)
        for bundle in bundles:
            for block in bundle["blocks"]:
                if block["rank"] != "genus" or block["name"] in gen_genus.NOT_A_TAXON:
                    continue
                name = gen_genus.NAME_CORRECTIONS.get(block["name"], block["name"])
                by_genus[name].append((bundle, block))
        for name, entries in by_genus.items():
            fresh_pages[name] = gen_genus.render(name, entries)
    else:
        by_name = defaultdict(list)
        for bundle in bundles:
            for block in bundle["blocks"]:
                if block["rank"] != "species":
                    continue
                name = gen_species.canonical_name(block["canonical"])
                if not gen_species.is_taxon(name):
                    continue
                by_name[name].append((bundle, block))
        for name, entries in by_name.items():
            genus = entries[0][1].get("genus")
            genus = gen_genus.NAME_CORRECTIONS.get(genus, genus)
            fresh_pages[name.replace(" ", "_")] = gen_species.render(entries, genus)

    patched = 0
    added: dict[str, int] = defaultdict(int)
    for stem, fresh in sorted(fresh_pages.items()):
        target = out_dir / f"{stem}.md"
        if not target.exists():
            continue
        existing = target.read_text(encoding="utf-8")
        if "generated" not in (existing.split("---")[1] if "---" in existing else ""):
            continue                    # hand-written: leave it alone
        updated = patch(existing, fresh)
        if updated is None:
            continue
        for heading in WANTED:
            if f"## {heading}" in updated and f"## {heading}" not in existing:
                added[heading] += 1
        patched += 1
        if not args.dry_run:
            target.write_text(updated, encoding="utf-8")

    verb = "would patch" if args.dry_run else "patched"
    print(f"{verb} {patched} {args.rank} pages")
    for heading, n in added.items():
        print(f"  +{n} '{heading}' sections")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
