"""Deterministic family-page generator.

The 160 existing family pages were authored: they carry an APG order, the
treatment's authors and year, and synthesised prose. This produces the
mechanical part of a family page for the 20 families that have none -- the 18
fern families of vol. 8, plus Gramineae and Orchidaceae -- so the tier is
complete and no genus page points at a family that does not exist.

Like the other generators it invents nothing. The family description is emitted
verbatim under a translate marker; the genera table is counted from the
bundle's own blocks. `order` is an APG placement, which is world knowledge
rather than anything the source states, so it is left as a TODO rather than
guessed -- a wrong order on a family page is worse than an absent one.

Existing pages are never overwritten unless they carry the `generated` tag.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

from flora_ocr.flora import REPO_ROOT
from flora_ocr.wiki.fix_links import LINK_CORRECTIONS
from flora_ocr.wiki.gen_genus import (
    NAME_CORRECTIONS, NOT_A_TAXON, diagnosis_text, load_bundles,
    resolve_family, split_heading,
)

# Both maps: NAME_CORRECTIONS covers the genus headings, LINK_CORRECTIONS the
# corruptions that reach us through the species blocks (Warnecka -> Warneckea).
def correct(name: str) -> str:
    return NAME_CORRECTIONS.get(name, LINK_CORRECTIONS.get(name, name))

WIKI_DIR = REPO_ROOT / "wiki"


# "XII. ASPLENIACEAE S. F. Gray" -- a roman numeral, the family in capitals,
# then the authority. Only the last part belongs in the authority field.
HEAD_RE = re.compile(
    r"^\s*(?:[IVXLC]+\s*[.·]\s*)?(?:\d+\s*[.)]\s*)?"
    r"(?:FAMILLE\s+DES\s+)?[A-ZÉÈÀÂÎÔÛÇ][A-ZÉÈÀÂÎÔÛÇ\-]{3,}\s*(.*)$")


def family_authority(heading: str) -> str:
    m = HEAD_RE.match(heading.strip())
    if not m:
        return ""
    rest = m.group(1).strip(" .,—–-")
    # a trailing French vernacular in parentheses is not an authority
    rest = re.sub(r"\s*\([^)]*\)\s*$", "", rest).strip()
    return rest if 0 < len(rest) <= 60 else ""


def render(family: str, bundles: list[dict]) -> str:
    genera: dict[str, list] = defaultdict(list)
    species_by_genus: dict[str, int] = defaultdict(int)
    family_blocks = []
    for bundle in bundles:
        for block in bundle["blocks"]:
            if block["rank"] == "family":
                family_blocks.append((bundle, block))
            elif block["rank"] == "genus":
                raw = block["name"]
                if raw in NOT_A_TAXON:
                    continue
                genera[correct(raw)].append(bundle)
            elif block["rank"] == "species":
                raw = block.get("genus")
                if raw:
                    species_by_genus[correct(raw)] += 1

    # a genus may have species but no genus block of its own
    for genus in species_by_genus:
        genera.setdefault(genus, [])

    total_species = sum(species_by_genus.values())
    authority = ""
    if family_blocks:
        head, _ = split_heading(family_blocks[0][1].get("text", ""))
        authority = family_authority(head)

    vols = sorted({str(b["treatment"].get("vol")) for b in bundles})

    fm = ["---", "type: family", f"name: {family}"]
    if authority:
        fm.append(f"authority: {authority}")
    fm.append("order:   # TODO: APG placement, not stated by the source")
    fm.append(f"genera_in_region: {len(genera)}")
    fm.append(f"species_in_region: {total_species}")
    fm.append("treatments:")
    for bundle in bundles:
        t = bundle["treatment"]
        fm.append(f"  - vol: {t.get('vol')}")
        fm.append(f"    source: {t.get('source')}")
    fm.append("tags: [family, generated]")
    fm.append("---")

    out = ["", f"# {family}", ""]
    if authority:
        out.append(f"**Authority**: {authority}")
    out.append(f"**Genera in region**: {len(genera)} · "
               f"**Species in region**: {total_species}")
    out.append("")

    out.append("## Diagnosis")
    out.append("")
    if family_blocks:
        out.append("<!-- TODO:translate — source text below, verbatim and untranslated -->")
        out.append("")
        for _, block in family_blocks:
            _, rest = split_heading(block.get("text", ""))
            text = diagnosis_text(rest)
            if text:
                out.append(text)
                out.append("")
    else:
        out.append("*No family description was segmented from the source.*")
        out.append("")

    out.append("## Genera in region")
    out.append("")
    out.append("| Genus | Species |")
    out.append("|-------|---------|")
    for genus in sorted(genera):
        out.append(f"| [[{genus}]] | {species_by_genus.get(genus, 0)} |")
    out.append("")

    out.append("## Treatments")
    out.append("")
    for bundle in bundles:
        t = bundle["treatment"]
        out.append(f"### Vol {t.get('vol')}")
        out.append("")
        out.append(f"**Source**: `{t.get('source')}`")
        out.append("")

    out.append("## Notes")
    out.append("")
    out.append("<!-- TODO:notes -->")
    out.append("")
    out.append("## See also")
    out.append("")
    for vol in vols:
        out.append(f"- [[vol{vol.zfill(2)}]]")
    out.append("")
    return "\n".join(fm) + "\n".join(out)


GENERA_HEADING_RE = re.compile(r"^## Genera\b.*$", re.M)
ROW_GENUS_RE = re.compile(r"^\|\s*\[\[([^\]|\\]+)")
# where the genera table belongs if the page has none: before the first of
# these, so it sits after the prose and before the apparatus
INSERT_BEFORE = re.compile(r"^## (Treatment|Treatments|Notes|See also)\b", re.M)


def genera_table(pairs: list[tuple[str, int]]) -> list[str]:
    out = ["| Genus | Species |", "|-------|---------|"]
    out += [f"| [[{g}]] | {n} |" for g, n in pairs]
    return out


def patch_genera(text: str, pairs: list[tuple[str, int]]) -> str | None:
    """Add the genera table, or the rows an existing one is missing.

    Authored pages are left alone apart from this: existing rows keep whatever
    the author wrote in them, and only genera absent from the table are added.
    A page that names its genera in prose but never links them -- Aristolochiaceae
    mentions Pararistolochia and links nothing -- gets a table of its own.
    """
    heading = GENERA_HEADING_RE.search(text)
    if heading:
        start = heading.end()
        nxt = re.search(r"^## ", text[start:], re.M)
        end = start + (nxt.start() if nxt else len(text) - start)
        section = text[start:end]
        listed = {m.group(1).strip() for m in
                  (ROW_GENUS_RE.match(l) for l in section.split("\n")) if m}
        missing = [(g, n) for g, n in pairs if g not in listed]
        if not missing:
            return None
        rows = [f"| [[{g}]] | {n} |" for g, n in missing]
        body = section.rstrip("\n").split("\n")
        # append after the last table row, keeping any trailing prose in place
        last = max((i for i, l in enumerate(body) if l.lstrip().startswith("|")),
                   default=-1)
        if last < 0:
            body = genera_table(pairs)
        else:
            body[last + 1:last + 1] = rows
        return text[:start] + "\n".join(body) + "\n\n" + text[end:]

    block = ["## Genera in region", ""] + genera_table(pairs) + [""]
    anchor = INSERT_BEFORE.search(text)
    if not anchor:
        return text.rstrip("\n") + "\n\n" + "\n".join(block) + "\n"
    return text[:anchor.start()] + "\n".join(block) + "\n" + text[anchor.start():]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--family")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=str(WIKI_DIR / "families"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--patch-genera", action="store_true",
                    help="add the genera table, or its missing rows, to pages "
                         "that already exist -- leaving their prose untouched")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not (args.family or args.all):
        ap.error("give --family or --all")

    bundles = load_bundles(args.family, None)
    if not bundles:
        print("no bundles matched", file=sys.stderr)
        return 1

    by_family: dict[str, list] = defaultdict(list)
    for bundle in bundles:
        family = resolve_family(bundle["treatment"].get("family"))
        if family:
            by_family[family].append(bundle)

    out_dir = Path(args.out)

    if args.patch_genera:
        patched = unchanged = absent = 0
        for family, entries in sorted(by_family.items()):
            target = out_dir / f"{family}.md"
            if not target.exists():
                absent += 1
                continue
            # count distinct species, not blocks: a variety is a block of its
            # own, and Pararistolochia's six species were counted as seven
            names: dict[str, set[str]] = defaultdict(set)
            for bundle in entries:
                for block in bundle["blocks"]:
                    if block["rank"] == "genus" and block["name"] not in NOT_A_TAXON:
                        names.setdefault(correct(block["name"]), set())
                    elif block["rank"] == "species" and block.get("genus"):
                        names[correct(block["genus"])].add(block["canonical"])
            counts = {g: len(v) for g, v in names.items()}
            pairs = [(g, n) for g, n in sorted(counts.items())
                     if (WIKI_DIR / "genera" / f"{g}.md").exists()]
            if not pairs:
                unchanged += 1
                continue
            text = target.read_text(encoding="utf-8")
            updated = patch_genera(text, pairs)
            if updated is None or updated == text:
                unchanged += 1
                continue
            added = updated.count("| [[") - text.count("| [[")
            print(f"  {family}: +{added} genera")
            if not args.dry_run:
                target.write_text(updated, encoding="utf-8")
            patched += 1
        verb = "would patch" if args.dry_run else "patched"
        print(f"{verb} {patched} family pages, {unchanged} already complete, "
              f"{absent} with no page")
        return 0

    written = skipped = protected = 0
    for family, entries in sorted(by_family.items()):
        target = out_dir / f"{family}.md"
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            front = existing.split("---")[1] if "---" in existing else ""
            if "generated" not in front:
                protected += 1
                continue
            if not args.force:
                skipped += 1
                continue
        page = render(family, entries)
        if args.dry_run:
            print(f"  would write {family}.md ({len(page)} bytes)")
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(page, encoding="utf-8")
        written += 1

    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {written} family pages, skipped {skipped}")
    if protected:
        print(f"  protected {protected} authored pages (no `generated` tag)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
