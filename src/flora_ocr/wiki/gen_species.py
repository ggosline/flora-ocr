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

# Epithets that do not name a species. "Eugenia sp" is a real entry in the
# source -- an indeterminate collection -- but it is not a taxon that deserves
# a page, and the same string recurs in several volumes, so the pages would
# collide. These are recorded on the genus page instead, by the species count.
NOT_AN_EPITHET = {"sp", "spp", "sp.", "cf", "aff", "el", "le-", "var", "subsp",
                  "avec", "et", "sans", "pour", "dans"}

# The scan renders the ae/oe ligatures literally, so `Bertiera sphaerica` comes
# through as `Bertiera sphærica`. Fixed here rather than in NAME_CORRECTIONS:
# it is a mechanical transliteration, not a judgement about a particular name.
LIGATURES = str.maketrans({"æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe"})


def canonical_name(name: str) -> str:
    return name.translate(LIGATURES)


# "### 5. Whitfieldia Le-Testui R. Benoist" -- the name parser stops at the
# internal capital of `Le-Testui` and yields the epithet `le-`, so 24 species
# named for Le Testu arrived truncated. The heading still holds the whole word.
HEADING_NAME_RE = re.compile(
    r"^#{1,6}\s*(?:\d+\s*[.)]\s*)?([A-Z][a-z\-]+)\s+([A-Za-z][A-Za-z\-]{2,})")


def epithet_from_heading(text: str) -> str | None:
    """Recover a truncated epithet from the block's own heading."""
    m = HEADING_NAME_RE.match(text.lstrip().split("\n")[0])
    if not m:
        return None
    epithet = m.group(2).lower()
    return epithet if re.fullmatch(r"[a-z][a-z\-]{2,}", epithet) else None


def is_taxon(name: str) -> bool:
    genus, _, epithet = canonical_name(name).partition(" ")
    if not re.fullmatch(r"[A-Z][a-z\-]{2,}", genus):
        return False
    return bool(re.fullmatch(r"[a-z][a-z\-]{2,}", epithet)) \
        and epithet not in NOT_AN_EPITHET

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

# A markdown heading inside a block body -- segmentation noise, usually a
# contents list that landed in a treatment. Left as-is it becomes a section of
# the page, so the marker is dropped and the text kept as prose.
INNER_HEADING_RE = re.compile(r"^#{1,6}\s*")

# Volume back matter. The last treatment in a liteparse volume runs to the end
# of the file, so it swallows the bibliography, the index and the OCR debris
# after it: Rhizophora racemosa carried 27 KB of it, and 19 pages were affected.
# Worse, that material was being sent to the translator.
#
# Three signatures, any one of which ends the treatment:
INDEX_LEADER_RE = re.compile(r"\.{4,}\s*\d")          # "Aloe ......... 2, 3"
BIB_ENTRY_RE = re.compile(                              # "KEAY R.W.J. 1953."
    r"[A-Z][A-Z\-']{2,}(?:\s+[A-Z]\.){1,4}\s*\d{4}[a-z]?\.")
BIB_HEADING_RE = re.compile(r"^(BIBLIOGRAPHIE|INDEX|R[ÉE]F[ÉE]RENCES)\b", re.I)


def _is_back_matter(para: str) -> bool:
    if BIB_HEADING_RE.match(para.strip()):
        return True
    if len(INDEX_LEADER_RE.findall(para)) >= 3:
        return True
    if len(BIB_ENTRY_RE.findall(para)) >= 2:
        return True
    # OCR debris: a long run that is mostly not letters
    if len(para) > 200:
        letters = sum(c.isalpha() or c.isspace() for c in para)
        if letters / len(para) < 0.6:
            return True
    return False

# A vernacular name standing alone under the heading: a short line, no digits,
# no citation punctuation.
VERNACULAR_RE = re.compile(r"^[^\d(),.:;]{2,40}$")

def _paragraph_split(body: str) -> list[list[str]]:
    """Group body lines into paragraphs.

    Blank lines separate paragraphs, but the liteparse volumes emit none at
    all: a whole treatment arrives as one run of wrapped lines. Splitting on
    blank lines alone left those as a single paragraph, the label regex never
    fired, and 130 of 474 Leguminosae pages came out with no description while
    the text sat in the bundle all along. So a labelled line also opens a
    paragraph, which costs nothing on sources that do have blank lines.
    """
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(current)
                current = []
            continue
        if is_synonym_line(stripped):
            # a synonym stands alone: split before *and* after it, or the
            # description's opening line is swept in with the last of the chain
            if current:
                paragraphs.append(current)
            paragraphs.append([stripped])
            current = []
            continue
        if LABEL_RE.match(stripped) and current:
            paragraphs.append(current)
            current = []
        current.append(stripped)
    if current:
        paragraphs.append(current)
    return paragraphs


def _join(lines: list[str]) -> str:
    """Join wrapped lines, closing up words broken across a line end."""
    out = ""
    for line in lines:
        if not out:
            out = line
        elif out.endswith("-"):
            out = out[:-1] + line          # `par-` + `tiellement`
        else:
            out += " " + line
    return out.strip()


# A leading citation chain: "Bull. Jard. Bot. Natl. Belg. 66 : 20 (1997). --
# Pellegrin, Leg. Gabon : 78 (1948)." Where the source has no blank lines the
# protologue and the description arrive as one paragraph, so dropping any
# paragraph that starts like a citation throws the description away with it.
# The chain is stripped from the front instead, and what follows is kept.
LEADING_CITATION_RE = re.compile(
    r"^\s*(?:[—–-]\s*)?[A-Z][^()]{5,160}?\(\d{4}\)\s*[.;]?\s*")


def _strip_leading_citations(text: str) -> str:
    while True:
        stripped = LEADING_CITATION_RE.sub("", text, count=1)
        if stripped == text:
            return text
        text = stripped


SYNONYM_RE = re.compile(r"^\s*=\s*(.+)$")

# A synonym printed without a leading "=", one per line, as the born-digital
# volumes do: "Aristolochia talbotii S.Moore, Cat. pl. Oban : 93 (1913)."
# liteparse writes no blank lines, so these merged into the description
# paragraph and the leading-citation stripper stopped part-way through the
# chain, leaving ", nom. inval. Pararistolochia talbotii ..." at the head of
# Pararistolochia promissa's description.
SYNONYM_LINE_RE = re.compile(
    r"^[A-Z][a-z]+(?:\s+[a-z][a-z\-]+){1,2}\s+.{0,120}?\(\d{4}\)\s*[.,]?"
    r"(?:\s*,?\s*nom\.\s*(?:inval|nud|illeg|cons)\s*\.?)?\s*$")

# A synonym is also introduced by a dash, and its citation chain can run long
# past the first year -- "— Microdesmis puberula auct. non Hook.f. ex PLANCHON,
# ...: De Wildeman, Ann. Mus. Congo ... (1906); l.c. 2: 287 (1908), p.p.; ..."
# -- so the end-anchored form above never matched and 179 of these opened their
# species' description instead.
SYNONYM_OPEN_RE = re.compile(r"^\s*[—–=-]?\s*[A-Z][a-z]+\s+[a-z][a-z\-]{2,}\b")
SYNONYM_EVIDENCE_RE = re.compile(r"\(\d{4}\)|\bauct\.|\bnom\.|\bsensu\b|\bp\.p\.")


def is_synonym_line(line: str) -> bool:
    """True for a line that names another name for this taxon, not description."""
    if SYNONYM_LINE_RE.match(line.strip()):
        return True
    stripped = line.strip()
    return bool(SYNONYM_OPEN_RE.match(stripped)
                and SYNONYM_EVIDENCE_RE.search(stripped))


def strip_synonym_marker(line: str) -> str:
    return re.sub(r"^\s*[—–=-]\s*", "", line).strip()
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
    body = "\n".join(INNER_HEADING_RE.sub("", line)
                     for line in clean(body).split("\n")
                     if not RULE_RE.match(line.strip()))
    paragraphs = []
    for para in (_join(p) for p in _paragraph_split(body)):
        if _is_back_matter(para):
            break               # the treatment ends where the volume's does
        paragraphs.append(para)

    synonyms: list[str] = []
    description: list[str] = []
    vernacular: list[str] = []
    sections: list[tuple[str, bool, list[str]]] = []
    current: tuple[str, bool, list[str]] | None = None

    for one_line in paragraphs:
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
        if not description and is_synonym_line(one_line):
            synonyms.append(strip_synonym_marker(one_line))
            continue
        if is_citation(one_line):
            remainder = _strip_leading_citations(one_line)
            if len(remainder) < 60:
                continue                # the paragraph was citation and nothing else
            one_line = remainder        # keep the description that followed it
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
    name = canonical_name(block["canonical"])
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
            name = canonical_name(block["canonical"])
            if not is_taxon(name):
                # a truncated epithet is recoverable from the heading
                genus_part = name.split(" ")[0]
                recovered = epithet_from_heading(block.get("text", ""))
                if recovered and is_taxon(f"{genus_part} {recovered}"):
                    name = f"{genus_part} {recovered}"
                else:
                    empty += 1
                    continue
            by_name[name].append((bundle, block))

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
        print(f"  skipped {empty} blocks that are not a binomial "
              f"(indeterminate 'sp', truncations, non-taxon headings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
