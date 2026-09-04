"""Write a page for a modern family this flora has no treatment for.

APG moves taxa into families the volumes never use as a heading: the old
Flacourtiaceae is Salicaceae and Achariaceae, the old Scrophulariaceae is
Orobanchaceae, Linderniaceae and Plantaginaceae. Those families had no page, so
the placement lines on 143 genus and species pages had nowhere to point.

The mechanical part of each page -- frontmatter, the genus table, the links --
is built from the `modern_family` fields the placement pass wrote, so the
counts cannot drift from the pages they describe. The Notes are supplied per
family in a JSON file, because the reason a family was recircumscribed is world
knowledge, not something in the bundles.

These pages are written without the `generated` tag: they are the same kind of
page as the hand-written family accounts, and nothing should overwrite them.

    python -m flora_ocr.wiki.gen_modern_family --notes notes.json --dry-run
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

from flora_ocr.flora import REPO_ROOT
from flora_ocr.wiki import modern_placement as P

WIKI_DIR = REPO_ROOT / "wiki"


def collect(wiki: Path) -> dict[str, list[dict]]:
    """Genera carrying a modern_family, grouped by that family."""
    rows: dict[str, list[dict]] = collections.defaultdict(list)
    for path in sorted((wiki / "genera").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        modern = P.field(text, "modern_family")
        if not modern:
            continue
        m = P.FAMILY_NAME_RE.match(modern)
        if not m:
            continue
        vol = re.search(r"^\s*- vol:\s*(\S+)", text, re.M)
        rows[m.group(1)].append({
            "genus": P.field(text, "name"),
            "species": int(P.field(text, "species_in_region") or 0),
            "from": P.field(text, "family"),
            "vol": vol.group(1) if vol else "",
            "subfamily": modern[m.end():].strip(),
        })
    return {k: sorted(v, key=lambda r: (-r["species"], r["genus"]))
            for k, v in rows.items()}


def render(family: str, entries: list[dict], note: dict) -> str:
    genera = len(entries)
    species = sum(e["species"] for e in entries)
    sources = sorted({e["from"] for e in entries})
    vols = sorted({e["vol"] for e in entries if e["vol"]})
    subfamilies = sorted({e["subfamily"] for e in entries if e["subfamily"]})

    front = [
        "---",
        "type: family",
        f"name: {family}",
        f"order: {note['order']}",
        "circumscription: modern",
        f"segregated_from: [{', '.join(sources)}]",
        f"genera_in_region: {genera}",
        f"species_in_region: {species}",
        "in_region: true",
        "treatments: []",
        "tags: [family, modern-circumscription]",
        "---",
        "",
    ]

    from_links = ", ".join(f"[[{s}]]" for s in sources)
    body = [
        f"# {family}",
        "",
        f"**Order**: {note['order']} · **Genera in region**: {genera} · "
        f"**Species in region**: {species}",
        f"**Circumscription**: modern (APG); this flora treats these taxa "
        f"under {from_links}",
    ]
    if subfamilies:
        body.append("**Subfamilies represented**: "
                    + ", ".join(f"*{s.replace('subfam. ', '')}*"
                                for s in subfamilies))
    body += [
        "",
        "This family heads no volume of the flora. It is the modern placement "
        "of taxa the source treats elsewhere, and the page exists so those "
        "placements have somewhere to point.",
        "",
        "## Genera in region",
        "",
        "| Genus | Species | Treated in |",
        "|-------|---------|------------|",
    ]
    for e in entries:
        where = f"[[{e['from']}]]" + (f" ([[vol{e['vol']}]])" if e["vol"] else "")
        body.append(f"| [[{e['genus']}]] | {e['species']} | {where} |")

    body += ["", "## Notes", "", note["note"], "", "## See also", ""]
    body += [f"- [[{s}]] — the family this flora treats them under" for s in sources]
    body += [f"- [[vol{v}]]" for v in vols]
    return "\n".join(front + body) + "\n"


def backlink(wiki: Path) -> int:
    """Link the modern families named on the source family pages.

    Those pages named their destination families in plain text, because the
    pages did not exist when they were written. Only the "Modern family" table
    column and the See also line are touched, and only where a page now exists.
    """
    pages = {q.stem for q in wiki.rglob("*.md")}
    changed = 0
    for path in sorted((wiki / "families").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if "Modern family" not in text and "destination families" not in text:
            continue
        out = re.sub(r"^(\|[^|]*\|[^|]*\| )(\*{0,2})([A-Z][a-z]+aceae)(\*{0,2})( \|)$",
                     lambda m: (m.group(1) + (f"[[{m.group(3)}]]"
                                              if m.group(3) in pages
                                              else m.group(2) + m.group(3) + m.group(4))
                                + m.group(5)),
                     text, flags=re.M)
        out = re.sub(r"^- ([A-Z][a-z]+aceae(?:, [A-Z][a-z]+aceae)*) — the destination families$",
                     lambda m: "- " + ", ".join(
                         f"[[{n}]]" if n in pages else n
                         for n in m.group(1).split(", ")) + " — the destination families",
                     out, flags=re.M)
        if out != text:
            path.write_text(out, encoding="utf-8")
            changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki", default=str(WIKI_DIR))
    ap.add_argument("--notes", required=True, help="JSON: family -> {order, note}")
    ap.add_argument("--force", action="store_true",
                    help="rewrite a page that already exists")
    ap.add_argument("--backlink", action="store_true",
                    help="also link these families where the source family "
                         "pages name them in plain text")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    wiki = Path(args.wiki)
    notes = json.loads(Path(args.notes).read_text(encoding="utf-8"))
    rows = collect(wiki)

    written = skipped = 0
    for family, note in sorted(notes.items()):
        entries = rows.get(family)
        if not entries:
            print(f"  {family}: no genera carry this placement, skipped")
            continue
        target = wiki / "families" / f"{family}.md"
        if target.exists() and not args.force:
            skipped += 1
            continue
        page = render(family, entries, note)
        if args.dry_run:
            print(page)
        else:
            target.write_text(page, encoding="utf-8")
        written += 1

    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {written} modern-family pages, skipped {skipped} existing")
    if args.backlink and not args.dry_run:
        print(f"linked destination families on {backlink(wiki)} family pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
