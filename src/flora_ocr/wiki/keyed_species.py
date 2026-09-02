"""Species that a source key separates but never treats.

A volume's key often runs out one more lead than the volume has treatments.
Aubreville keys four *Afzelia* but treats three: *A. africana* is keyed for
identification and then dropped, because it belongs to the northern peripheral
domain rather than the volume's Cameroon-Gabon core. The bundle builder derives
the species table from numbered treatment blocks, so it finds three, and the
page then carries a key naming four -- self-contradictory, and wrong for a
regional wiki, since *A. africana* is squarely inside Nigeria-to-western-DRC.

This module recovers those names so a genus page can list them separately from
the treated species. It never invents a species: every name here was printed in
a key lead in the source.

The hard part is not finding the names but rejecting the OCR wreckage around
them. A raw scan of the corpus turns up 356 keyed-but-untreated names, of which
roughly a third are fragments -- `A. africanu`, `Magnistipula zenker`,
`Acioa brazze`. Four filters cut those out:

  recurrence   a real name is printed more than once in the volume; a garbled
               one usually appears exactly where it was garbled
  morphology   the epithet must end like a Latin epithet
  proximity    an epithet within edit distance 2 of one the genus already
               treats is a misreading of it, not a new species
  vocabulary   French running words (`dont`, `pour`, `sensu`) reach the scan
               through `A. ...` patterns and are not epithets at all
  synonymy     a name the volume itself reduces to synonymy is not an untreated
               species -- *Acioa brazze* is published, but the volume says
               plainly that it belongs under *A. dewevrei*
  truncation   an epithet that is the tail of a longer name in the same volume
               is that name with its head chewed off (`condere` <- `icondere`)

The filters are deliberately strict. A name this module drops is only missing
from a page; a name it wrongly admits is a fabricated species in a botanical
reference, which is far worse.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from flora_ocr.flora import REPO_ROOT

OCR_DIR = REPO_ROOT / "ocr_output"

# A numbered key lead: "1.", "2'.", "12.". The period form only: the volumes use
# "1)" for discussion footnotes, and those are running prose full of species
# names that are cited, not keyed. Synonymy lines start with '=' and are skipped.
LEAD_RE = re.compile(r"^\s*\d+['’]?\.")

# Latin epithet endings. Not exhaustive Latin grammar: enough to reject the
# truncations the scan produces, which characteristically lose their final
# syllable (`zenker`, `brazze`, `gabor`).
LATIN_END_RE = re.compile(
    r"(us|a|um|is|e|ii|i|ense|ensis|oides|ana|anum|anus|ata|atum|atus"
    r"|ifolia|ifolium|iana|ianum|ianus|osa|osum|osus|ica|icum|icus)$"
)

# Words the `A. xxx` pattern picks up out of French running text, plus the
# taxonomic connectives that are never epithets.
NOT_AN_EPITHET = {
    "dont", "pour", "sont", "comme", "sect", "sensu", "versus", "contra",
    "cette", "cette", "dans", "avec", "chez", "elle", "elles", "leur",
    "leurs", "mais", "plus", "moins", "aussi", "ainsi", "entre", "selon",
    "subsp", "subgen", "series", "sensu", "auct", "nomen", "ibid",
    "espece", "especes", "genre", "planche", "figure", "voir",
}

MIN_MENTIONS = 2
MAX_TYPO_DISTANCE = 2


def _distance(a: str, b: str, cap: int = 3) -> int:
    """Levenshtein distance, abandoned once it exceeds `cap`."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


# "= Acioa brazze De Wild." -- the volume placing a name in synonymy.
SYNONYM_RE = re.compile(r"^\s*=\s*([A-Z][a-z]+)\s+([a-z][a-z\-]+)", re.M)

DIACRITICS = str.maketrans("áàâäãéèêë"
                           "íìîïóòôöõ"
                           "úùûüçñ",
                           "aaaaaeeeeiiiiooooouuuucn")


def _fold(text: str) -> str:
    """Lowercase and strip diacritics, so `A. icondere` and `A. ícondere` agree."""
    return text.lower().translate(DIACRITICS)


def synonymised(raw_text: str, genus: str) -> set[str]:
    """Epithets the volume explicitly reduces to synonymy under this genus."""
    return {ep.lower() for gen, ep in SYNONYM_RE.findall(raw_text) if gen == genus}


def _truncation_of(epithet: str, raw_text: str) -> bool:
    """True if a longer word ending in `epithet` is commoner in the volume."""
    longer = re.findall(rf"\b[a-z]{{1,4}}{re.escape(epithet)}\b", raw_text, re.I)
    return len(longer) >= len(re.findall(rf"\b{re.escape(epithet)}\b", raw_text, re.I))


def _source_text(source: str) -> str | None:
    path = OCR_DIR / source / "text.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def keyed_names(block: dict, source_lines: list[str], genus: str) -> set[str]:
    """Epithets printed in a numbered key lead inside this genus block."""
    # lines are folded to lowercase before matching, so the pattern is too
    folded_genus = _fold(genus)
    pattern = re.compile(
        rf"\b(?:{re.escape(folded_genus[0])}\.|{re.escape(folded_genus)})"
        rf"\s*([a-z][a-z\-]{{4,}})\b")
    found: set[str] = set()
    span = source_lines[block["line_start"] - 1: block.get("span_end")
                        or block["line_end"]]
    for line in span:
        stripped = line.lstrip()
        if not LEAD_RE.match(line) or stripped.startswith("="):
            continue
        found.update(m.group(1) for m in pattern.finditer(_fold(line)))
    return found


def untreated(candidates: set[str], treated: set[str], raw_text: str,
              synonyms: set[str] = frozenset()) -> list[str]:
    """Filter keyed epithets down to those that plausibly name a real species."""
    folded = _fold(raw_text)
    kept = []
    for epithet in sorted(candidates):
        if epithet in treated or epithet in NOT_AN_EPITHET or epithet in synonyms:
            continue
        if _truncation_of(epithet, folded):
            continue
        if not LATIN_END_RE.search(epithet):
            continue
        if len(re.findall(rf"\b{re.escape(epithet)}\b", folded)) < MIN_MENTIONS:
            continue
        # a near-miss on a treated epithet is a misreading of it
        if any(_distance(epithet, t, MAX_TYPO_DISTANCE) <= MAX_TYPO_DISTANCE
               for t in treated):
            continue
        kept.append(epithet)
    # collapse pairs that are misreadings of each other, keeping the commoner
    collapsed: list[str] = []
    for epithet in kept:
        twin = next((c for c in collapsed
                     if _distance(epithet, c, MAX_TYPO_DISTANCE) <= MAX_TYPO_DISTANCE),
                    None)
        if twin is None:
            collapsed.append(epithet)
            continue
        here = len(re.findall(rf"\b{re.escape(epithet)}\b", folded))
        there = len(re.findall(rf"\b{re.escape(twin)}\b", folded))
        if here > there:
            collapsed[collapsed.index(twin)] = epithet
    return sorted(collapsed)


def for_genus(entries: list[tuple[dict, dict]], genus: str) -> list[tuple[str, str]]:
    """[(epithet, vol)] keyed but untreated across every treatment of a genus."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for bundle, block in entries:
        treatment = bundle["treatment"]
        raw = _source_text(treatment.get("source", "").replace("sources/", ""))
        if raw is None:
            continue
        lines = raw.splitlines()
        treated = {_fold(b["canonical"].split()[-1])
                   for b in bundle["blocks"]
                   if b["rank"] == "species" and b.get("genus") == genus}
        if not treated:
            continue        # nothing segmented: the gap is a parse failure, not a key
        keyed = keyed_names(block, lines, genus)
        for epithet in untreated(keyed, treated, raw, synonymised(raw, genus)):
            if epithet in seen:
                continue
            seen.add(epithet)
            out.append((epithet, str(treatment.get("vol"))))
    return out
