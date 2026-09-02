"""Volume stub pages, for the volumes that have none.

Genus and species pages link to their volume, so 37 missing volume pages
stranded about 1,500 references -- `[[vol49]]` alone is linked 351 times.

The hand-written volume pages carry year, editors, publisher, DOI and a
suggested citation. None of that is in the OCR metadata, which knows only the
PDF filename, page count and figure count. Those fields are therefore left
empty with a TODO rather than guessed: a wrong DOI or a wrong editor on a
citable reference page is worse than an absent one.

What is derivable is filled in: the families treated, where each came from on
disk, and how many genera and species were ingested from it.

Existing volume pages are never touched.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from flora_ocr.flora import REPO_ROOT
from flora_ocr.wiki.gen_genus import BUNDLE_DIR, resolve_family

WIKI_DIR = REPO_ROOT / "wiki"


def render(label: str, entries: list[dict]) -> str:
    families = sorted({resolve_family(b["treatment"].get("family")) or "?"
                       for b in entries})
    genera = species = 0
    for bundle in entries:
        for block in bundle["blocks"]:
            if block["rank"] == "genus":
                genera += 1
            elif block["rank"] == "species":
                species += 1

    out = ["---", "type: volume", f"vol: {int(label) if label.isdigit() else label}",
           "year:        # TODO: not in the OCR metadata",
           "editors: []  # TODO",
           "publisher: Museum national d'Histoire naturelle, Paris",
           "doi:         # TODO",
           "families: [" + ", ".join(families) + "]",
           "tags: [volume, generated]", "---", ""]
    out.append(f"# Flore du Gabon — Volume {label}")
    out.append("")
    out.append(f"{len(families)} family treatment(s) ingested, "
               f"{genera} genera and {species} species.")
    out.append("")
    out.append("Year, editors and DOI are not recorded in the OCR metadata and "
               "have been left blank rather than guessed.")
    out.append("")
    out.append("## Families treated")
    out.append("")
    out.append("| Family | Source | Pages |")
    out.append("|--------|--------|-------|")
    for bundle in sorted(entries, key=lambda b: str(b["treatment"].get("family"))):
        treatment = bundle["treatment"]
        family = resolve_family(treatment.get("family")) or "?"
        pages = [b.get("page_start") for b in bundle["blocks"]
                 if b.get("page_start") is not None]
        span = f"{min(pages)}–{max(pages)}" if pages else "—"
        out.append(f"| [[{family}]] | `{treatment.get('source')}` | {span} |")
    out.append("")
    out.append("## Notes")
    out.append("")
    out.append("<!-- TODO:notes -->")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(WIKI_DIR / "volumes"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    by_vol: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(BUNDLE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        vol = str(data["treatment"].get("vol") or "").strip()
        if vol:
            by_vol[vol].append(data)

    out_dir = Path(args.out)
    written = existing = 0
    for label, entries in sorted(by_vol.items()):
        slug = f"vol{label.zfill(2)}" if label.isdigit() else f"vol{label}"
        target = out_dir / f"{slug}.md"
        if target.exists():
            existing += 1
            continue
        page = render(label, entries)
        if args.dry_run:
            print(f"  would write {target.name} "
                  f"({len(entries)} family treatment(s))")
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(page, encoding="utf-8")
        written += 1

    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {written} volume pages, left {existing} existing untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
