"""Deterministic species-page generator.

The species tier is the most mechanical of the three and by far the largest:
roughly 3,000 blocks corpus-wide, ~470 in Leguminosae alone. Like `gen_genus`
this invents nothing. Every field is copied from the ingest bundle or left
explicitly blank, and the two things needing a model are marked in the output:

    <!-- TODO:translate -->   descriptive prose, emitted verbatim in the source
                              language beneath the marker
    <!-- TODO:notes -->       the Notes section, which needs world knowledge

The volumes label their sections, so the page can be structured rather than
dumped as one prose block. Only the descriptive sections are marked for
translation: a specimen citation is collector, number and herbarium code, and a
type citation is a proper noun with a locality -- running those through a
translator would corrupt data to no purpose while multiplying the cost.

Usage
-----
    python -m flora_ocr.wiki.gen_species --family Leguminosae --dry-run
    python -m flora_ocr.wiki.gen_species --family Leguminosae
    python -m flora_ocr.wiki.gen_species --all

Existing pages are never overwritten unless they carry the `generated` tag,
for the same reason as in `gen_genus`: a hand-written page is better than
anything this can produce.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from flora_ocr.flora import REPO_ROOT
from flora_ocr.wiki.gen_genus import (
    NAME_CORRECTIONS, clean, find_protologue, is_citation, load_bundles,
    resolve_family, split_heading,
)

WIKI_DIR = REPO_ROOT / "wiki"

# Source section labels -> wiki heading, and whether the body is prose that
# wants translating. Order matters only for readability of the output page.
SECTIONS: list[tuple[str, str, bool]] = [
    (r"NOMS?\s+VERN(?:AC\.?|ACULAIRES?)",         "Vernacular names", False),
    (r"(?:HOLO|LECTO|ISO|SYN)?TYPES?",           "Type",             False),
    (r"ÉCOLOGIE|ECOLOGIE",                       "Ecology",          True),
    (r"R[ÉE]PARTITION(?:\s+G[ÉE]OGRAPHIQUE)?"
     r"|DISTRIBUTION",                           "Distribution",     True),
    (r"(?:PROPRI[ÉE]T[ÉE]S\s+ET\s+)?USAGES",     "Uses",             True),
    (r"MAT[ÉE]RIEL[A-ZÉÈ À-Ü]*",       "Specimens examined", False),
    (r"BIBLIOGRAPHIE",                           "Bibliography",     False),
    (r"NOTES?",                                  "Source note",      True),
]
LABEL_RE = re.compile(
    r"^\s*(?:(" + "|".join(p for p, _, _ in SECTIONS) + r")\s*[:.]"
    r"|(Type|Types|Holotype|Lectotype|Isotype|Syntypes?|Distribution|"
    r"Nom vernac\.?|Noms? vernaculaires?|Mat[ée]riel [ée]tudi[ée])\s*:)",
    re.I)

# Sections that are a single paragraph. The volumes run straight on from a type
# citation into ecology and distribution prose with no further label, so
# without this the Type section swallows the rest of the treatment.
SINGLE_PARAGRAPH = {"Type", "Vernacular names", "Bibliography"}

# Unlabelled prose appearing after a labelled section: notes on habit, timber,
# range. It has nowhere else to go and is worth keeping.
TRAILING_HEADING = "Discussion"

RULE_RE = re.compile(r"^-{3,}$")

# A vernacular name standing alone under the heading: a short line, no digits,
# no citation punctuation.
VERNACULAR_RE = re.compile(r"^[^\d(),.:;]{2,40}$")

SYNONYM_RE = re.compile(r"^\s*=\s*(.+)$")
# "### 1. Afzelia pachyloba Harms (PL. 24, p. 113)" -> drop number and plate
HEAD_CLEAN_RE = re.compile(r"^\d+\s*[.)]\s*|\s*\((?:PL|Pl|pl)\.[^)]*\)\s*$")


def heading_of(label: str) -> tuple[str, bool]:
    for pattern, heading, translate in SECTIONS:
        if re.fullmatch(pattern, label, re.I):
            return heading, translate
    return label.title(), False


def parse_block(text: str) -> dict:
    """Split a species block into description plus its labelled sections."""
    head, body = split_heading(text)
    body = "\n".join(line for line in clean(body).split("\n")
                     if not RULE_RE.match(line.strip()))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]

    synonyms: list[str] = []
    description: list[str] = []
    vernacular: list[str] = []
    sections: list[tuple[str, bool, list[str]]] = []
    current: tuple[str, bool, list[str]] | None = None

    for para in paragraphs:
        one_line = " ".join(para.split("\n")).strip()
        match = LABEL_RE.match(one_line)
        if match:
            label = (match.group(1) or match.group(2)).strip()
            heading, translate = heading_of(label)
            rest = one_line[match.end():].strip(" :.")
            if heading in SINGLE_PARAGRAPH:
                sections.append((heading, translate, [rest] if rest else []))
                current = None          # the next paragraph starts afresh
            else:
                current = (heading, translate, [rest] if rest else [])
                sections.append(current)
            continue
        if current is not None:
            current[2].append(one_line)
            continue

        synonym = SYNONYM_RE.match(one_line)
        if synonym:
            synonyms.append(synonym.group(1).strip())
            continue
        if is_citation(one_line):
            continue                    # protologue and later-reference chain
        if not description and not sections and VERNACULAR_RE.match(one_line):
            vernacular.append(one_line)  # a bare name under the heading
            continue
        if sections:
            # unlabelled prose after a labelled section
            if not (sections and sections[-1][0] == TRAILING_HEADING):
                current = (TRAILING_HEADING, True, [])
                sections.append(current)
            current[2].append(one_line)
            continue
        description.append(one_line)

    if vernacular:
        sections.insert(0, ("Vernacular names", False, vernacular))

    merged: list[tuple[str, bool, list[str]]] = []
    for heading, translate, lines in sections:
        for i, (h, t, existing) in enumerate(merged):
            if h == heading:
                merged[i] = (h, t or translate, existing + lines)
                break
        else:
            merged.append((heading, translate, lines))
    sections = merged

    return {
        "heading": HEAD_CLEAN_RE.sub("", head).strip(),
        "synonyms": synonyms,
        "description": description,
        "sections": [(h, t, [x for x in b if x]) for h, t, b in sections],
        "protologue": find_protologue(body),
    }


def render(entries: list[tuple[dict, dict]], genus: str) -> str:
    """entries: [(bundle, species block)] -- one per treatment of this species.

    Twenty-three names in the corpus are treated in more than one volume.
    Rendering only the first would silently drop the others, so the extra
    treatments follow the primary one under their own headings.
    """
    bundle, block = entries[0]
    treatment = bundle["treatment"]
    parsed = parse_block(block.get("text", ""))
    name = block["canonical"]
    epithet = name.split(" ", 1)[1] if " " in name else ""
    family = resolve_family(block.get("family")) or ""
    authority = re.sub(r"\s*\((?:PL|Pl|pl)\.[^)]*\)", "",
                       block.get("authority") or "").strip()

    fm = ["---", "type: species", f"name: {name}"]
    if authority:
        fm.append(f"authority: {authority}")
    if genus:
        fm.append(f"genus: {genus}")
    if family:
        fm.append(f"family: {family}")
    fm.append(f"vol: {treatment.get('vol')}")
    fm.append(f"pages: {block.get('page_start')}-{block.get('page_end')}")
    fm.append(f"source: {treatment.get('source')}")
    if len(entries) > 1:
        others = ", ".join(str(b["treatment"].get("vol")) for b, _ in entries[1:])
        fm.append(f"also_in_vols: [{others}]")
    fm.append("tags: [species, generated]")
    fm.append("---")

    out = [""]
    out.append(f"# *{name}*" + (f" {authority}" if authority else ""))
    out.append("")
    if genus:
        out.append(f"**Genus**: [[{genus}]]")
    if family:
        out.append(f"**Family**: [[{family}]]")
    if parsed["protologue"]:
        out.append(f"**Protologue**: {parsed['protologue']}")
    out.append("")

    if parsed["synonyms"]:
        out.append("## Synonyms")
        out.append("")
        for synonym in parsed["synonyms"]:
            out.append(f"- {synonym}")
        out.append("")

    out.append("## Description")
    out.append("")
    if parsed["description"]:
        out.append("<!-- TODO:translate — source text below, verbatim and untranslated -->")
        out.append("")
        out.extend(_paragraphs(parsed["description"]))
    else:
        out.append("*No descriptive text was segmented for this species.*")
        out.append("")

    for heading, translate, body in parsed["sections"]:
        if not body:
            continue
        out.append(f"## {heading}")
        out.append("")
        if translate:
            out.append("<!-- TODO:translate — source text below, verbatim and untranslated -->")
            out.append("")
        out.extend(_paragraphs(body))

    figures = block.get("figures") or []
    if figures:
        out.append("## Figures")
        out.append("")
        for figure in figures:
            path = figure.get("path") or ""
            caption = (figure.get("caption") or "").strip()
            out.append(f"![{name}]({path})")
            if caption:
                out.append("")
                out.append(f"*{caption}*")
            out.append("")

    for extra_bundle, extra_block in entries[1:]:
        extra = parse_block(extra_block.get("text", ""))
        vol = extra_bundle["treatment"].get("vol")
        out.append(f"## Also treated in vol {vol}")
        out.append("")
        out.append(f"**Pages**: {extra_block.get('page_start')}-"
                   f"{extra_block.get('page_end')} · "
                   f"**Source**: `{extra_bundle['treatment'].get('source')}`")
        out.append("")
        if extra["description"]:
            out.append("<!-- TODO:translate — source text below, verbatim and untranslated -->")
            out.append("")
            out.extend(_paragraphs(extra["description"]))
        for heading, _, body in extra["sections"]:
            if not body:
                continue
            out.append(f"### {heading}")
            out.append("")
            out.extend(_paragraphs(body))

    out.append("## Notes")
    out.append("")
    out.append("<!-- TODO:notes -->")
    out.append("")
    out.append("## See also")
    out.append("")
    if genus:
        out.append(f"- [[{genus}]]")
    if family:
        out.append(f"- [[{family}]]")
    out.append(f"- [[vol{str(treatment.get('vol')).zfill(2)}]]")
    out.append("")
    return "\n".join(fm) + "\n".join(out)


def _paragraphs(lines: list[str]) -> list[str]:
    out = []
    for line in lines:
        out.append(line)
        out.append("")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--family")
    ap.add_argument("--vol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=str(WIKI_DIR / "species"))
    ap.add_argument("--force", action="store_true",
                    help="refresh pages this module wrote (never hand-written ones)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not (args.family or args.vol or args.all):
        ap.error("give --family, --vol or --all")

    bundles = load_bundles(args.family, args.vol)
    if not bundles:
        print("no bundles matched", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    by_name: dict[str, list] = defaultdict(list)
    empty = 0
    for bundle in bundles:
        for block in bundle["blocks"]:
            if block["rank"] != "species":
                continue
            if " " not in block["canonical"]:
                empty += 1
                continue
            by_name[block["canonical"]].append((bundle, block))

    written = skipped = protected = 0
    for name, entries in sorted(by_name.items()):
        genus_raw = entries[0][1].get("genus")
        genus = NAME_CORRECTIONS.get(genus_raw, genus_raw)
        target = out_dir / (name.replace(" ", "_") + ".md")
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            front = existing.split("---")[1] if "---" in existing else ""
            if "generated" not in front:
                protected += 1
                continue
            if not args.force:
                skipped += 1
                continue
        page = render(entries, genus)
        if args.dry_run:
            print(f"  would write {target.name} ({len(page)} bytes)")
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(page, encoding="utf-8")
        written += 1

    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {written} species pages, skipped {skipped} existing")
    if protected:
        print(f"  protected {protected} hand-written pages (no `generated` tag)")
    if empty:
        print(f"  skipped {empty} blocks with no binomial")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
