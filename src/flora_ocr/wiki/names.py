"""Canonical taxon names and wiki filenames.

The OCR headings carry numbering, French family names, authorities and the
occasional scanning artefact. The wiki schema (wiki/CLAUDE.md) wants ASCII
CapCase families, bare genera, and `Genus_species` species filenames. This
module does that conversion deterministically so no LLM tokens are spent on it.

Nothing here reads the filesystem — it is pure string work and cheap to test.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ── Family ────────────────────────────────────────────────────────────────────

# Pre-Linnaean/French vernacular family names that do not end in -aceae.
# Keys are accent-stripped uppercase. Mirrors the table in ocr/paddle.py; kept
# separate so this module stays import-light (paddle.py pulls in paddleocr).
_LEGACY_FAMILIES = {
    "GRAMINEES": "Gramineae",
    "LEGUMINEUSES": "Leguminosae",
    "COMPOSEES": "Compositae",
    "OMBELLIFERES": "Umbelliferae",
    "CRUCIFERES": "Cruciferae",
    "LABIEES": "Labiatae",
    "GUTTIFERES": "Guttiferae",
    "PALMIERS": "Palmae",
    "PTERIDOPHYTES": "Pteridophytes",
}

# French -ACÉES / -ACEES / -ACÉE all map to the modern -aceae ending.
_FR_FAMILY_SUFFIX_RE = re.compile(r"ACEE?S?$")


def strip_accents(s: str) -> str:
    """NFKD-fold and drop combining marks: 'MYRTACÉES' → 'MYRTACEES'."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def canonical_family(raw: str) -> str | None:
    """'MYRTACÉES' → 'Myrtaceae'. 'LÉGUMINEUSES' → 'Leguminosae'.

    Returns None when the string does not look like a family name at all.
    """
    if not raw:
        return None
    word = strip_accents(raw).strip().strip(".").upper()
    # Drop a leading enumerator ("I.", "1.") and the "FAMILLE DES " prefix.
    word = re.sub(r"^(?:[IVXLC]+|\d+)\s*[.)]\s*", "", word)
    word = re.sub(r"^FAMILLES?\s+DES?\s+", "", word)
    word = word.strip()
    if not word or not word.isalpha():
        return None

    if word in _LEGACY_FAMILIES:
        return _LEGACY_FAMILIES[word]

    if _FR_FAMILY_SUFFIX_RE.search(word):
        word = _FR_FAMILY_SUFFIX_RE.sub("ACEAE", word)

    if not word.endswith("ACEAE"):
        return None
    return word.capitalize()


# ── Genus / species ───────────────────────────────────────────────────────────

_UC = r"A-ZÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ"
_LC = r"a-zàâæçéèêëîïôœùûüÿ"

# Leading "12. " or "IV. " or "a) " enumerators on a heading.
_ENUMERATOR_RE = re.compile(r"^\s*(?:\d+|[IVXLC]{1,5}|[a-z])\s*[.)]\s*")

# "PSIDIUM L." / "Psidium L." → name + authority
_GENUS_RE = re.compile(rf"^([{_UC}][{_UC}{_LC}\-]{{2,}})\s*(.*)$")

# "Rourea coccinea (Thonn. ex Schum.) Benth." → genus, epithet, authority
_SPECIES_RE = re.compile(
    rf"^([{_UC}][{_LC}\-]{{2,}})\s+"                 # Genus
    rf"([{_UC}]?[{_LC}][{_LC}\-]+|sp\.|spp\.)"        # epithet (or sp.)
    rf"\s*(.*)$"                                      # authority + remainder
)

_INFRASP_RE = re.compile(
    r"^(var\.|subsp\.|ssp\.|f\.|fo\.|forma)\s+"
    rf"([{_LC}][{_LC}\-]+)\s*(.*)$"
)

_RANK_ABBREV = {"var.": "var", "subsp.": "subsp", "ssp.": "subsp",
                "f.": "f", "fo.": "f", "forma": "f"}


def _ascii_slug(s: str) -> str:
    """ASCII, non-alphanumerics collapsed to underscore."""
    s = strip_accents(s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s)
    return s.strip("_")


@dataclass
class ParsedName:
    rank: str                      # family | genus | species | infraspecific
    name: str                      # display name, e.g. "Rourea coccinea"
    canonical: str                 # same, normalised for keys
    authority: str = ""
    family: str = ""
    genus: str = ""
    epithet: str = ""
    infra_rank: str = ""           # var / subsp / f
    infra_epithet: str = ""
    raw: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def wiki_stem(self) -> str:
        """Filename stem (no .md) per wiki/CLAUDE.md naming conventions."""
        if self.rank == "family":
            return _ascii_slug(self.name)
        if self.rank == "genus":
            return _ascii_slug(self.genus or self.name)
        if self.rank == "species":
            return f"{_ascii_slug(self.genus)}_{_ascii_slug(self.epithet)}"
        # infraspecific: Genus_species_var_epithet
        return "_".join(
            p for p in (
                _ascii_slug(self.genus), _ascii_slug(self.epithet),
                self.infra_rank, _ascii_slug(self.infra_epithet),
            ) if p
        )

    @property
    def wiki_dir(self) -> str:
        return {
            "family": "families",
            "genus": "genera",
            "species": "species",
            "infraspecific": "species",
        }[self.rank]

    @property
    def wiki_path(self) -> str:
        return f"{self.wiki_dir}/{self.wiki_stem}.md"


def parse_heading(raw: str, rank: str, *, family: str = "",
                  genus: str = "", species_epithet: str = "") -> ParsedName | None:
    """Parse a taxonomic heading of known `rank` into its parts.

    `family`/`genus`/`species_epithet` supply breadcrumb context for the ranks
    that do not repeat it (a `var.` heading names only the variety).
    Returns None when the heading cannot be parsed at that rank.
    """
    text = raw.strip()
    warnings: list[str] = []

    if rank == "family":
        fam = canonical_family(text)
        if not fam:
            return None
        return ParsedName(rank="family", name=fam, canonical=fam,
                          family=fam, raw=raw)

    body = _ENUMERATOR_RE.sub("", text).strip()

    if rank == "genus":
        m = _GENUS_RE.match(body)
        if not m:
            return None
        g_raw, authority = m.group(1), m.group(2).strip()
        # Headings are often ALL CAPS in scanned volumes.
        g = strip_accents(g_raw).capitalize()
        return ParsedName(rank="genus", name=g, canonical=g, authority=authority,
                          family=family, genus=g, raw=raw)

    if rank == "species":
        m = _SPECIES_RE.match(body)
        if not m:
            return None
        g_raw, epithet, authority = m.group(1), m.group(2), m.group(3).strip()
        g = strip_accents(g_raw).capitalize()
        ep = strip_accents(epithet).lower().rstrip(".")
        if epithet[0].isupper():
            warnings.append(
                f"epithet '{epithet}' capitalised in source; lowercased")
        if genus and g.lower() != genus.lower():
            warnings.append(f"genus '{g}' differs from enclosing genus '{genus}'")
        name = f"{g} {ep}"
        return ParsedName(rank="species", name=name, canonical=name,
                          authority=authority, family=family, genus=g,
                          epithet=ep, raw=raw, warnings=warnings)

    if rank == "infraspecific":
        m = _INFRASP_RE.match(body)
        if not m:
            return None
        rank_word, infra_ep, authority = m.group(1), m.group(2), m.group(3).strip()
        infra = _RANK_ABBREV.get(rank_word.lower(), "var")
        if not (genus and species_epithet):
            warnings.append("infraspecific heading without enclosing species")
        name = f"{genus} {species_epithet} {rank_word} {infra_ep}".strip()
        return ParsedName(rank="infraspecific", name=name, canonical=name,
                          authority=authority, family=family, genus=genus,
                          epithet=species_epithet, infra_rank=infra,
                          infra_epithet=strip_accents(infra_ep).lower(),
                          raw=raw, warnings=warnings)

    return None


# ── Binomial detection (used to attach figures to species) ────────────────────

_BINOMIAL_RE = re.compile(
    rf"\b([{_UC}][{_LC}\-]{{2,}})\s+([{_LC}][{_LC}\-]{{2,}})\b"
)


def edit_distance_le_1(a: str, b: str) -> bool:
    """True when `a` and `b` differ by at most one edit.

    Scanned volumes routinely mangle a single letter in a genus heading
    ('Cnestis' → 'Chestis'); this lets the segmenter repair it against the
    enclosing genus instead of minting a bogus page.
    """
    a, b = a.lower(), b.lower()
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b)) == 1
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = 0
    while i < la and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def find_binomials(text: str) -> list[tuple[str, str]]:
    """Return [(Genus, epithet)] mentioned in free text such as a plate caption."""
    out: list[tuple[str, str]] = []
    for m in _BINOMIAL_RE.finditer(text or ""):
        g = strip_accents(m.group(1)).capitalize()
        ep = strip_accents(m.group(2)).lower()
        out.append((g, ep))
    return out
