"""Deterministic genus-page generator.

Writes the mechanical parts of a genus page straight from the ingest bundles:
frontmatter, authority and protologue, the species list, treatment blocks and
figure references. **No model inference and no invented content** — every field
is either copied from the bundle or left explicitly blank.

The two things a model still has to supply are marked in the output with HTML
comments, so they are greppable and so a half-finished page is never mistaken
for a finished one:

    <!-- TODO:translate -->   the diagnosis, emitted verbatim in the source
                              language beneath the marker
    <!-- TODO:notes -->       the Notes section, which needs world knowledge the
                              source does not contain

A genus page also carries a "Keyed but not treated" section where the source
key separates more species than the volume treats -- see `keyed_species`. It is
rendered from the source, never inferred, and is kept out of the species table
so that table keeps meaning "treated here".

Usage
-----
    python -m flora_ocr.wiki.gen_genus --family Leguminosae --dry-run
    python -m flora_ocr.wiki.gen_genus --family Leguminosae
    python -m flora_ocr.wiki.gen_genus --all --skip-existing

Existing pages are never overwritten without --force: the hand-written pages are
better than anything this can produce, and clobbering them would be a net loss.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from flora_ocr.flora import REPO_ROOT
from flora_ocr.wiki import keyed_species

BUNDLE_DIR = REPO_ROOT / "build" / "wiki_bundles"
WIKI_DIR = REPO_ROOT / "wiki"

# Directory names the family split mangled; the bundle's `family` field inherits
# them, so a page generated straight from it would carry a family that does not
# exist. Mapped rather than guessed.
FAMILY_ALIASES = {
    "Millettiaspeciesfabaceae": "Leguminosae",
    "Labiataeulmaceaeverbenaceae": None,      # composite: needs manual split
    "Boraginaceaebuxaceae": None,
    "Gladiolusmirusvaupeliridaceae": None,
}

# Headings the segmenter reads as genera but which are not taxa at all. These get
# no page: a "Leguminosae" or "Materiel" genus page would be pure noise.
NOT_A_TAXON = {
    "Leguminosae", "Materiel", "Materiel etudie", "Heterostylie",
    "Cl", "St", "Ilomba", "Ossoko", "Ekoune", "Noticealamemoirede",
    # "Ordre I. LYCOPODIALES" -- a French ordinal heading, not a genus
    "Ordre", "Ordres", "Famille", "Familles", "Tribu", "Tribus",
}

# Genus names the scan corrupts, with the correction verified against the source.
# The page is written under the correct name and records the raw form, so the
# name is right in the wiki and the OCR text stays greppable.
NAME_CORRECTIONS = {
    "Lesenera": "Loesenera",
    "Erythropheum": "Erythrophleum",
    "Scorodophleus": "Scorodophloeus",
    "Vrectaria": "Virectaria",
    "Hymenodietyon": "Hymenodictyon",
    "Pausinstalia": "Pausinystalia",
    "Dietyandra": "Dictyandra",
    "Tarena": "Tarenna",
    "Stelecantha": "Stelechantha",
    "Colecaryon": "Coelocaryon",
    "Hypodaphns": "Hypodaphnis",
    "Rhapiostylis": "Rhaphiostylis",
    "Cyrtococcus": "Cyrtococcum",
}

# Epithets the scan corrupted, keyed by the corrected binomial. The genus map
# above cannot reach these: volume 5 prints "Cyrtococcus chaetophorum" for
# *Cyrtococcum chaetophoron*, so fixing the genus still leaves a wrong ending.
# Verified against the published name one at a time; nothing here is inferred
# from shape.
EPITHET_CORRECTIONS = {
    "Cyrtococcum chaetophorum": "chaetophoron",
}


def correct_binomial(name: str) -> str:
    """The published binomial for a species block's name."""
    genus, _, epithet = name.partition(" ")
    genus = NAME_CORRECTIONS.get(genus, genus)
    return f"{genus} {EPITHET_CORRECTIONS.get(f'{genus} {epithet}', epithet)}".strip()

# Names that look wrong but that this module will not presume to correct.
SUSPECT_NAMES: set[str] = set()

# A citation line: a work, a volume/page and a year. The protologue is the first
# such citation; anything after the first em-dash is a list of later references,
# so the line is truncated there rather than carried whole.
CITATION_RE = re.compile(r"^[A-Z].{5,160}?\(\d{4}\)")
PAGE_MARKER_RE = re.compile(r"<!--\s*page\s+\d+\s*-->")
# A markdown heading inside a block body, e.g. "## CLE DES ESPECES". Left as-is
# it becomes a section of the page, outside the diagnosis block, so it never
# gets a translate marker and stays in the source language: that is exactly how
# Annona, Cleistopholis and Letestudoxa kept French keys after a clean run.
INNER_HEADING_RE = re.compile(r"^#{1,6}\s*", re.M)
FIGURE_REF_RE = re.compile(r"\[Figure[^\]]*\]")


def load_bundles(family: str | None, vol: str | None) -> list[dict]:
    out = []
    for path in sorted(BUNDLE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        fam = resolve_family(data["treatment"].get("family"))
        if family and fam != family:
            continue
        if vol and str(data["treatment"].get("vol")) != str(vol):
            continue
        out.append(data)
    return out


def resolve_family(raw: str | None) -> str | None:
    if raw in FAMILY_ALIASES:
        return FAMILY_ALIASES[raw]
    return raw


def clean(text: str) -> str:
    """Strip page markers and figure placeholders; collapse blank runs."""
    text = PAGE_MARKER_RE.sub("", text)
    text = FIGURE_REF_RE.sub("", text)
    text = INNER_HEADING_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_heading(block_text: str) -> tuple[str, str]:
    """Return (heading line, remainder) for a taxon block."""
    lines = block_text.lstrip().split("\n")
    head = lines[0].lstrip("# ").strip() if lines else ""
    return head, "\n".join(lines[1:]).strip()


def is_citation(line: str) -> bool:
    return bool(CITATION_RE.match(line.strip()))


def find_protologue(body: str) -> str:
    """First citation line, truncated before the later-reference chain."""
    for line in body.split("\n")[:6]:
        line = line.strip()
        if is_citation(line):
            return re.split(r"\s+[—–]\s+", line)[0].rstrip(". ")
    return ""


SENTENCE_END_RE = re.compile(r'[.!?:][)"\u201d\u00bb]?$')

# A line that opens a new paragraph even where the previous one ran full width:
# the discussion, uses and bibliography that follow a genus description.
PARA_OPENER_RE = re.compile(
    r"^(?:Genre\b|Genus\b|This genus\b|A genus\b|Ce genre\b"
    r"|Usages?\s*:|Uses?\s*:|Notes?\s*:|Type\b"
    r"|B\s*:|Bi\s*:|Bibliograph|i ?B ?liograph)")


def _paragraphs(lines: list[str]) -> list[str]:
    """Rebuild paragraphs from the source's hard-wrapped lines.

    The bundles keep the PDF's line breaks, so joining the lines with a blank
    line each -- which is what this did -- made every line of the scan its own
    markdown paragraph. Trema's diagnosis came out as fourteen one-line
    paragraphs, several cut mid-sentence. A line ends a paragraph when it stops
    on sentence punctuation *and* falls short of the block's usual line width,
    which is what the last line of a paragraph looks like in a justified
    column; an explicit opener on the next line ends one too.
    """
    if len(lines) < 2:
        return list(lines)
    width = statistics.median(len(x) for x in lines)
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for i, line in enumerate(lines):
        current.append(line)
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        ends = bool(SENTENCE_END_RE.search(line)) and len(line) < 0.85 * width
        if nxt is not None and PARA_OPENER_RE.match(nxt):
            ends = True
        if ends:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    return [_join(p) for p in paragraphs]


def _join(lines: list[str]) -> str:
    """Join wrapped lines, closing up words broken across a line end."""
    out = ""
    for line in lines:
        if not out:
            out = line
        elif out.endswith("-"):
            out = out[:-1] + line          # `late-` + `rales`
        else:
            out += " " + line
    return out.strip()


def diagnosis_text(body: str, limit: int = 2500) -> str:
    """The descriptive prose, minus the citation apparatus at the top."""
    lines = []
    for line in clean(body).split("\n"):
        s = line.strip()
        if not s:
            continue
        # skip the citation and synonymy apparatus that precedes the description
        if not lines and (is_citation(s) or s.startswith(("=", "—", "–"))):
            continue
        lines.append(s)
        if sum(len(x) for x in lines) > limit:
            break
    return "\n\n".join(_paragraphs(lines))


# Where a genus treatment's key begins: an explicit heading, or failing that
# the first numbered lead. The diagnosis is capped at a few thousand characters,
# which silently cut 311 keys in half -- Pararistolochia's stopped at lead 3.
# The key is not prose to be summarised: a half key is useless, so it is pulled
# out whole into its own section, which is what the wiki schema asks for.
# The heading is usually a markdown sub-heading -- "### Key to species" -- so
# the optional #-prefix matters: without it 92 keys stayed inside the diagnosis,
# Diospyros's 38-lead key among them. The wording varies ("Key to species",
# "KEY TO SPECIES AND VARIETIES", "Cle de determination"), so the tail is loose.
KEY_HEADING_RE = re.compile(
    r"^\s*#{0,6}\s*(?:CL[EÉ]F?S?\s+(?:DES\s+(?:ESP[EÈ]CES|SOUS-GENRES|GENRES"
    r"|VARI[EÉ]T[EÉ]S|SECTIONS)|DE\s+D[EÉ]TERMINATION)"
    r"|KEY\s+TO\s+(?:THE\s+)?(?:SPECIES|SUBGENERA|SECTIONS|VARIETIES|GENERA)"
    r"[A-Z ]*)\b.*$", re.I | re.M)
KEY_LEAD_RE = re.compile(r"^\s*1\s*['’]?\s*[.\-)]\s*[-–—]?\s*\S.*\.{4,}", re.M)


def split_key(text: str) -> tuple[str, str]:
    """Return (prose, key). The key is everything from where it starts."""
    m = KEY_HEADING_RE.search(text)
    if m is None:
        m = KEY_LEAD_RE.search(text)
        if m is None:
            return text, ""
        return text[:m.start()].rstrip(), text[m.start():].strip()
    return text[:m.start()].rstrip(), text[m.end():].strip()


def species_rows(blocks: list[dict], genus: str) -> list[dict]:
    """The species blocks of one genus, under either spelling of its name.

    A genus page is filed under the corrected name, but its species blocks
    still carry the corruption the scan printed -- volume 5's species sit under
    `Cyrtococcus` while the page is `Cyrtococcum` -- so matching on the raw name
    alone left the page saying no species were segmented for it.
    """
    return [b for b in blocks
            if b["rank"] == "species"
            and NAME_CORRECTIONS.get(b.get("genus"), b.get("genus")) == genus]


KEYED_HEADING = "## Keyed but not treated"
KEYED_PREAMBLE = (
    "The source key separates these species but the volume gives them no "
    "treatment, usually because they fall outside the area it covers in full. "
    "They are listed for identification; some are extralimital to the region."
)


def keyed_section(entries: list[tuple[dict, dict]], genus: str) -> list[str]:
    """The 'keyed but not treated' block, or [] when the key adds nothing."""
    extra = keyed_species.for_genus(entries, genus)
    if not extra:
        return []
    out = [KEYED_HEADING, "", KEYED_PREAMBLE, ""]
    for epithet, vol in extra:
        out.append(f"- *{genus} {epithet}* — keyed in vol {vol}, not treated")
    out.append("")
    return out


def yaml_list(items) -> str:
    return "[" + ", ".join(items) + "]"


def render(genus: str, entries: list[tuple[dict, dict]]) -> str:
    """entries: [(bundle, genus_block)] — one per treatment the genus appears in."""
    first_block = entries[0][1]
    family = resolve_family(first_block.get("family")) or ""
    authority = (first_block.get("authority") or "").strip()

    total_species = 0
    treatments, species_all, figures = [], [], []
    for bundle, gblock in entries:
        t = bundle["treatment"]
        spp = species_rows(bundle["blocks"], genus)
        total_species += len(spp)
        species_all.extend((s, t) for s in spp)
        figures.extend(gblock.get("figures") or [])
        treatments.append({
            "vol": t.get("vol"),
            "source": t.get("source"),
            "pages": f'{gblock.get("page_start")}-{gblock.get("page_end")}',
            "block": gblock,
        })

    fm = ["---", "type: genus", f"name: {genus}"]
    if authority:
        fm.append(f"authority: {authority}")
    if family:
        fm.append(f"family: {family}")
    fm.append(f"species_in_region: {total_species}")
    fm.append("treatments:")
    for tr in treatments:
        fm.append(f"  - vol: {tr['vol']}")
        fm.append(f"    source: {tr['source']}")
    raw = first_block.get("raw_name")
    if raw:
        fm.append(f"ocr_name: {raw}    # as the scan renders it; corrected above")
    if genus in SUSPECT_NAMES:
        fm.append("needs_review: name may be an OCR corruption or a mis-segmented heading")
    fm.append("tags: [genus, generated]")
    fm.append("---")

    body = [""]
    body.append(f"# *{genus}*" + (f" {authority}" if authority else ""))
    body.append("")
    if family:
        body.append(f"**Family**: [[{family}]]")
    if authority:
        body.append(f"**Authority**: {authority}")
    proto = find_protologue(split_heading(first_block.get("text", ""))[1])
    if proto:
        body.append(f"**Protologue**: {proto}")
    body.append("")

    body.append("## Diagnosis")
    body.append("")
    body.append("<!-- TODO:translate — source text below, verbatim and untranslated -->")
    body.append("")
    keys = []
    for _, gblock in entries:
        _, rest = split_heading(gblock.get("text", ""))
        prose, key = split_key(clean(rest))
        d = diagnosis_text(prose)
        if d:
            body.append(d)
            body.append("")
        if key:
            keys.append(key)

    if keys:
        body.append("## Key to the species")
        body.append("")
        body.append("<!-- TODO:translate — source text below, verbatim and untranslated -->")
        body.append("")
        for key in keys:
            body.append(key)
            body.append("")

    body.append("## Species in region")
    body.append("")
    if species_all:
        body.append("| Species | Vol | Pages |")
        body.append("|---------|-----|-------|")
        seen_rows: set[str] = set()
        for s, t in species_all:
            name = correct_binomial(s["name"])
            if name in seen_rows:
                continue        # a variety is its own block; one row per species
            seen_rows.add(name)
            link = name.replace(" ", "_")
            epithet = name.split(" ", 1)[1] if " " in name else name
            body.append(
                f'| [[{link}\\|*{genus[0]}. {epithet}*]] | {t.get("vol")} '
                f'| {s.get("page_start")}–{s.get("page_end")} |'
            )
    else:
        body.append("*No species blocks were segmented for this genus in the source.*")
    body.append("")

    body.extend(keyed_section(entries, genus))

    body.append("## Treatments")
    body.append("")
    for tr in treatments:
        body.append(f"### Vol {tr['vol']}")
        body.append("")
        body.append(f"**Source**: `{tr['source']}` · **Pages**: {tr['pages']}")
        body.append("")

    if figures:
        body.append("## Figures")
        body.append("")
        for f in figures:
            body.append(f"- {f}")
        body.append("")

    body.append("## Notes")
    body.append("")
    body.append("<!-- TODO:notes -->")
    body.append("")
    body.append("## See also")
    body.append("")
    if family:
        body.append(f"- [[{family}]]")
    for tr in treatments:
        body.append(f"- [[vol{str(tr['vol']).zfill(2)}]]")
    body.append("")

    return "\n".join(fm) + "\n".join(body)


SECTION_RE = re.compile(
    rf"^{re.escape(KEYED_HEADING)}\n.*?(?=^## )", re.M | re.S)


def patch_page(text: str, section: list[str]) -> str | None:
    """Insert or refresh the keyed section in an existing page.

    Returns None when nothing changes. Everything else on the page -- including
    a translated diagnosis and any hand-written Notes -- is left exactly as it
    is, which is the whole point: regenerating a page would throw the
    translation away and cost another pass to get back.
    """
    block = "\n".join(section) + "\n" if section else ""
    if SECTION_RE.search(text):
        updated = SECTION_RE.sub(lambda _: block, text, count=1)
    elif not block:
        return None
    else:
        # the section belongs after the species table and before Treatments
        anchor = re.search(r"^## Treatments$", text, re.M)
        if not anchor:
            return None
        updated = text[:anchor.start()] + block + text[anchor.start():]
    return updated if updated != text else None


def render_stub(genus: str, entries: list[tuple[dict, dict]]) -> str:
    """A genus page built from its species alone.

    Some genus headings are never segmented -- Uvaria, Anthonotha, Artabotrys
    and 74 others have species blocks but no genus block, so 165 species pages
    pointed at a genus that did not exist. The species carry enough to build a
    real index page: family, the species themselves, and which treatments they
    came from. There is no diagnosis, and the page says so rather than
    implying one was not worth writing.
    """
    family = resolve_family(entries[0][1].get("family")) or ""
    species = [(b, bundle["treatment"]) for bundle, b in entries]

    fm = ["---", "type: genus", f"name: {genus}"]
    if family:
        fm.append(f"family: {family}")
    fm.append(f"species_in_region: {len(species)}")
    vols = sorted({str(t.get("vol")) for _, t in species})
    fm.append("treatments:")
    for vol in vols:
        fm.append(f"  - vol: {vol}")
    fm.append("tags: [genus, generated, stub]")
    fm.append("---")

    body = ["", f"# *{genus}*", ""]
    if family:
        body.append(f"**Family**: [[{family}]]")
    body.append("")
    body.append("## Diagnosis")
    body.append("")
    body.append("*No genus description was segmented from the source. This page "
                "is built from the species treatments below.*")
    body.append("")
    body.append("## Species in region")
    body.append("")
    body.append("| Species | Vol | Pages |")
    body.append("|---------|-----|-------|")
    for block, treatment in sorted(species, key=lambda x: x[0]["canonical"]):
        name = block["canonical"]
        epithet = name.split(" ", 1)[1] if " " in name else name
        body.append(
            f'| [[{name.replace(" ", "_")}\\|*{genus[0]}. {epithet}*]] '
            f'| {treatment.get("vol")} '
            f'| {block.get("page_start")}–{block.get("page_end")} |')
    body.append("")
    body.append("## Notes")
    body.append("")
    body.append("<!-- TODO:notes -->")
    body.append("")
    body.append("## See also")
    body.append("")
    if family:
        body.append(f"- [[{family}]]")
    for vol in vols:
        body.append(f"- [[vol{vol.zfill(2)}]]")
    body.append("")
    return "\n".join(fm) + "\n".join(body)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--family", help="only genera of this family")
    ap.add_argument("--vol", help="only this volume")
    ap.add_argument("--all", action="store_true", help="every family")
    ap.add_argument("--out", default=str(WIKI_DIR / "genera"))
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing pages (they are usually better)")
    ap.add_argument("--stubs", action="store_true",
                    help="build pages for genera that have species blocks but "
                         "no genus block of their own")
    ap.add_argument("--patch-keyed", action="store_true",
                    help="only insert/refresh the 'keyed but not treated' "
                         "section on existing pages, leaving all other content "
                         "(translations, notes) untouched")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not (args.family or args.vol or args.all):
        ap.error("give --family, --vol or --all")

    bundles = load_bundles(args.family, args.vol)
    if not bundles:
        print("no bundles matched", file=sys.stderr)
        return 1

    by_genus: dict[str, list] = defaultdict(list)
    dropped: list[str] = []
    corrected: list[tuple[str, str]] = []
    for bundle in bundles:
        for block in bundle["blocks"]:
            if block["rank"] != "genus":
                continue
            raw = block["name"]
            if raw in NOT_A_TAXON:
                dropped.append(raw)
                continue
            name = NAME_CORRECTIONS.get(raw, raw)
            if name != raw:
                corrected.append((raw, name))
                block = {**block, "name": name, "raw_name": raw}
            by_genus[name].append((bundle, block))

    out_dir = Path(args.out)

    if args.stubs:
        by_species: dict[str, list] = defaultdict(list)
        for bundle in bundles:
            for block in bundle["blocks"]:
                if block["rank"] != "species":
                    continue
                raw = block.get("genus")
                if not raw or raw in NOT_A_TAXON:
                    continue
                # the link map catches corruptions the species blocks carry
                # that NAME_CORRECTIONS does not, e.g. Warnecka -> Warneckea
                from flora_ocr.wiki.fix_links import LINK_CORRECTIONS
                fixed = NAME_CORRECTIONS.get(raw, LINK_CORRECTIONS.get(raw, raw))
                by_species[fixed].append((bundle, block))

        written = skipped = 0
        for genus, entries in sorted(by_species.items()):
            if genus in by_genus or (out_dir / f"{genus}.md").exists():
                skipped += 1
                continue
            page = render_stub(genus, entries)
            if args.dry_run:
                print(f"  would write {genus}.md ({len(entries)} species)")
            else:
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"{genus}.md").write_text(page, encoding="utf-8")
            written += 1
        verb = "would write" if args.dry_run else "wrote"
        print(f"{verb} {written} genus stubs, skipped {skipped} that already exist")
        return 0

    if args.patch_keyed:
        patched = unchanged = missing = 0
        for genus, entries in sorted(by_genus.items()):
            target = out_dir / f"{genus}.md"
            if not target.exists():
                missing += 1
                continue
            text = target.read_text(encoding="utf-8")
            updated = patch_page(text, keyed_section(entries, genus))
            if updated is None:
                unchanged += 1
                continue
            n = updated.count("— keyed in vol")
            print(f"  {genus}: {n} keyed-but-untreated species")
            if not args.dry_run:
                target.write_text(updated, encoding="utf-8")
            patched += 1
        verb = "would patch" if args.dry_run else "patched"
        print(f"{verb} {patched} pages, {unchanged} unchanged, "
              f"{missing} with no page yet")
        return 0

    written = skipped = flagged = protected = 0
    for genus, entries in sorted(by_genus.items()):
        target = out_dir / f"{genus}.md"
        if target.exists():
            # --force is for refreshing pages this module wrote. A page without
            # the `generated` tag was written by hand and is better than
            # anything here can produce, so it is never overwritten — I lost the
            # eight hand-written Leguminosae pages to a --force run once, and
            # only got them back because they were committed.
            existing = target.read_text(encoding="utf-8")
            hand_written = "generated" not in existing.split("---")[1] if "---" in existing else True
            if hand_written:
                protected += 1
                continue
            if not args.force:
                skipped += 1
                continue
        page = render(genus, entries)
        if genus in SUSPECT_NAMES:
            flagged += 1
            print(f"  FLAG {genus}: suspect name, review before use")
        if args.dry_run:
            print(f"  would write {target} ({len(page)} bytes)")
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(page, encoding="utf-8")
        written += 1

    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {written} genus pages, skipped {skipped} existing, "
          f"{flagged} flagged for review")
    if protected:
        print(f"  protected {protected} hand-written pages (no `generated` tag)")
    if dropped:
        print(f"  dropped {len(dropped)} non-taxon headings: "
              f"{', '.join(sorted(set(dropped)))}")
    if corrected:
        print("  corrected OCR names: "
              + ", ".join(f"{a}->{b}" for a, b in sorted(set(corrected))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
