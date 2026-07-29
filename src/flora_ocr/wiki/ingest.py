"""Deterministic pre-pass for wiki ingest.

The wiki (see wiki/CLAUDE.md) is authored by an LLM, but most of the per-family
work is not judgement: locating the source, splitting the treatment into taxon
blocks, working out which plate belongs to which species, deciding filenames,
and rebuilding index.md. Doing that by reading a 365 KB treatment into context
and re-reading it once per species is where the cost goes.

This module does all of it with no model calls, and emits one *bundle* per
treatment: a JSON file holding a pre-scoped payload per taxon. The LLM tier then
only ever sees the ~2 KB block for the page it is writing.

Commands
--------
  discover   list treatments found in the OCR output, with engine preference
  segment    show the taxon blocks for one treatment (diagnostic)
  bundle     write bundle JSON for one/all treatments
  status     which treatments are ingested into the wiki, which are pending
  reindex    regenerate wiki/index.md from page frontmatter

Sources under ocr_output/ are treated as immutable — nothing here writes to
them. Bundles land in build/wiki_bundles/ by default.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

from flora_ocr.flora import REPO_ROOT, add_flora_arg, load_flora
from flora_ocr.pipeline.reclassify_headings import PAGE_RE, classify
from flora_ocr.wiki import names as N

# Engines in the preference order given by wiki/CLAUDE.md.
ENGINE_PRIORITY = {"paddle": 0, "liteparse": 1, "mineru": 2, "marker": 3}

DIR_RE = re.compile(
    r"^(?:(?P<family>[A-Za-z][A-Za-z\-]*)_)?"
    r"vol(?P<vol>\d+(?:bis)?)_"
    r"(?P<engine>paddle|liteparse|mineru|marker)$"
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# Headings that end the taxonomic body. Without these the final species block
# runs on through the index and swallows tens of KB of back matter.
BACK_MATTER_RE = re.compile(
    r"^(INDEX|SOMMAIRE|TABLE\b|BIBLIOGRAPHIE|REFERENCES|ADDENDA|ERRATA"
    r"|LISTE DES|NOMS VERNACULAIRES|ACHEVE D)"
)

# Front-matter pages of modern Margraf volumes: covers, mastheads, title pages.
COVER_PAGES = 7

RANK_LEVEL = {"family": 2, "genus": 3, "species": 4, "infraspecific": 5}

# Which model tier should author each page type. Species/infraspecific pages are
# bounded, formulaic and numerous; family/genus pages carry the synthesis.
TIER = {
    "family": "synthesis",
    "genus": "synthesis",
    "species": "bulk",
    "infraspecific": "bulk",
    "front_matter": "synthesis",
}


# ── Treatment discovery ───────────────────────────────────────────────────────

@dataclass
class Treatment:
    path: Path
    vol: str
    engine: str
    family: str | None          # None for whole-volume (unsplit) dirs
    text_path: Path
    translated: bool            # True when text_en.md was used
    page_count: int = 0
    figure_count: int = 0
    char_count: int = 0

    @property
    def key(self) -> tuple[str, str]:
        return (self.family or "*", self.vol)

    @property
    def slug(self) -> str:
        return self.path.name


def _source_text(d: Path) -> tuple[Path | None, bool]:
    """Prefer the translated text; fall back to the source language."""
    en = d / "text_en.md"
    if en.exists():
        return en, True
    raw = d / "text.md"
    if raw.exists():
        return raw, False
    return None, False


def discover(output_dir: Path) -> list[Treatment]:
    """Find treatment dirs, keeping the best engine for each (family, vol)."""
    found: dict[tuple[str, str], Treatment] = {}
    for d in sorted(output_dir.iterdir()):
        if not d.is_dir():
            continue
        m = DIR_RE.match(d.name)
        if not m:
            continue
        text_path, translated = _source_text(d)
        if text_path is None:
            continue

        family = m.group("family")
        t = Treatment(
            path=d, vol=m.group("vol"), engine=m.group("engine"),
            family=N.canonical_family(family) or family if family else None,
            text_path=text_path, translated=translated,
            char_count=text_path.stat().st_size,
        )

        meta = d / "metadata.json"
        if meta.exists():
            try:
                md = json.loads(meta.read_text(encoding="utf-8"))
                t.page_count = md.get("page_count", 0)
                t.figure_count = md.get("figure_count", 0)
            except (json.JSONDecodeError, OSError):
                pass

        prev = found.get(t.key)
        if prev is None or ENGINE_PRIORITY[t.engine] < ENGINE_PRIORITY[prev.engine]:
            found[t.key] = t

    return sorted(found.values(), key=lambda t: (_vol_sort(t.vol), t.family or ""))


def _vol_sort(label: str):
    m = re.match(r"(\d+)(bis)?", label)
    return (int(m.group(1)), 1 if m.group(2) else 0) if m else (10**9, 0)


# ── Segmentation ──────────────────────────────────────────────────────────────

@dataclass
class Block:
    rank: str
    name: str
    canonical: str
    authority: str = ""
    family: str = ""
    genus: str = ""
    wiki_path: str = ""
    line_start: int = 0          # 1-indexed, the heading line
    line_end: int = 0            # last line of this block's own text
    span_end: int = 0            # last line including descendants
    page_start: int = 0
    page_end: int = 0
    text: str = ""
    figures: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tier: str = "bulk"

    @property
    def char_count(self) -> int:
        return len(self.text)


def _heads_a_family(heading: str) -> bool:
    """True when a heading's first word is a family name.

    '### APIACEAE Lindl. (1836) nom. cons.' satisfies the genus pattern (caps
    word + authority), so the family name has to be excluded explicitly.
    """
    first = heading.strip().split()
    return bool(first) and N.canonical_family(first[0].strip(".,:;")) is not None


def _known_genera(lines: list[str]) -> set[str]:
    """Genus names this treatment covers.

    Drawn from genus headings *and* from the numbered species headings, because
    a genus heading is often just '### 1. COMBRETUM' with no author and so does
    not match the genus pattern at all.
    """
    out: set[str] = set()
    for line in lines:
        hm = HEADING_RE.match(line.rstrip("\n"))
        if not hm:
            continue
        heading = hm.group(2)
        rank, _ = classify(heading)
        if rank == "genus" and not _heads_a_family(heading):
            parsed = N.parse_heading(heading, "genus")
            if parsed:
                out.add(parsed.genus)
        elif rank == "species":
            parsed = N.parse_heading(heading, "species")
            if parsed and parsed.genus:
                out.add(parsed.genus)
        elif rank is None and _BARE_GENUS_RE.match(heading.strip()):
            parsed = N.parse_heading(heading, "genus")
            if parsed:
                out.add(parsed.genus)
    return out


def _headings(lines: list[str], only_family: str | None = None) -> tuple[list[dict], list[int]]:
    """Locate every taxonomic heading, plus the lines where back matter starts.

    Runs a genus pre-pass so a species heading can be validated against the
    genera the treatment actually contains. Without that check, French prose in
    the morphology chapter ("Hétérostylie entre ...") parses as a binomial and
    mints a phantom species page.
    """
    known = _known_genera(lines)
    out: list[dict] = []
    terminators: list[int] = []
    seen_families: set[str] = set()
    seen_genera: set[str] = set()
    page = 0
    family = genus = species_ep = ""

    for i, line in enumerate(lines, 1):
        pm = PAGE_RE.search(line)
        if pm:
            page = int(pm.group(1))

        hm = HEADING_RE.match(line.rstrip("\n"))
        if not hm:
            continue

        heading = hm.group(2)
        if BACK_MATTER_RE.match(N.strip_accents(heading).strip().upper()):
            terminators.append(i)
            continue

        rank, _level = classify(heading)
        if rank == "genus" and _heads_a_family(heading):
            rank = "family"               # 'APIACEAE Lindl. (1836)' is a family
        confident = rank is not None      # matched a strict, numbered pattern
        if not rank:
            rank = _fallback_rank(heading, known)
        if not rank:
            continue

        parsed = N.parse_heading(
            heading, rank,
            family=family, genus=genus, species_epithet=species_ep,
        )
        if parsed is None:
            continue

        if rank == "family":
            # Running page headers and "(voir Rubiaceae, vol. 12)" cross-refs
            # repeat the family name; only the first occurrence opens a block.
            # In a family-split dir, a different family can only be a cross-ref.
            if parsed.name in seen_families:
                continue
            if only_family and parsed.name != only_family:
                continue
            seen_families.add(parsed.name)

        if rank == "genus":
            # The back index re-lists every genus; only the first occurrence is
            # the treatment itself.
            if parsed.genus in seen_genera:
                continue
            seen_genera.add(parsed.genus)

        if rank == "species":
            if confident:
                # A numbered binomial stands on its own; the genus lookup is
                # only used to repair OCR damage.
                resolved = _resolve_genus(parsed.genus, genus, known) or parsed.genus
            else:
                # An unnumbered binomial is only a species heading when it sits
                # under its own genus. Otherwise it is prose, or a mention in
                # the front matter's list of new taxa.
                if not genus:
                    continue
                resolved = _resolve_genus(parsed.genus, genus, set())
                if resolved is None:
                    continue
            if resolved != parsed.genus:
                parsed.warnings.append(
                    f"genus '{parsed.genus}' corrected to '{resolved}' (OCR)")
                parsed.genus = resolved
                parsed.name = parsed.canonical = f"{resolved} {parsed.epithet}"

        if rank == "family":
            family, genus, species_ep = parsed.family, "", ""
        elif rank == "genus":
            genus, species_ep = parsed.genus, ""
            parsed.family = family
        elif rank == "species":
            species_ep = parsed.epithet
            parsed.family = family

        out.append({"line": i, "page": page, "rank": rank, "parsed": parsed})

    return out, terminators


_INFRASP_LEAD_RE = re.compile(r"^(?:var\.|subsp\.|ssp\.|f\.|fo\.|forma)\s+[a-z]")
_AUTHORITY_LEAD_RE = re.compile(r"^[A-Z][A-Za-zÀ-ÿ.\-]*\.?(?:\s|$)")

# '1. COMBRETUM' — a genus heading with the authority omitted, which the
# author-requiring genus pattern in reclassify_headings cannot match.
#
# The number is required. Without it this also matches the all-caps morphology
# headings that fill the front of a treatment (PUBESCENCE, FEUILLES, FLEURS,
# CARYOLOGIE ...), and no blocklist can enumerate those; the enumerator is what
# actually distinguishes a genus heading from a section heading.
_BARE_GENUS_RE = re.compile(r"^\d+\s*[.)]\s*([A-ZÀ-Ý]{3,})\s*$")

# All-caps words that head a section rather than name a genus.
_STRUCTURAL_WORDS = {
    "CLE", "CLES", "FLORE", "GABON", "INDEX", "SOMMAIRE", "TAXA", "NOTES",
    "MORPHOLOGIE", "ECOLOGIE", "DESCRIPTION", "REMARQUES", "GENRE", "GENRES",
    "ESPECE", "ESPECES", "FAMILLE", "BIBLIOGRAPHIE", "INTRODUCTION",
    "REPARTITION", "DISTRIBUTION", "ABREVIATIONS", "REMERCIEMENTS",
}


def _fallback_rank(heading: str, known: set[str]) -> str | None:
    """Classify headings that reclassify_headings.classify() rejects.

    Born-digital volumes (liteparse) do not number their taxa — a species is
    just '### Centella asiatica (L.) Urb.' and the family is '### APIACEAE
    Lindl. (1836) nom. cons.'. Both fail the scanned-volume patterns, which
    require a leading number and a bare family word respectively.

    Everything here is guarded: an unnumbered binomial is only accepted when
    its genus is one this treatment actually treats, so French prose headings
    cannot slip through.
    """
    text = heading.strip()
    if not text:
        return None

    if _INFRASP_LEAD_RE.match(text):
        return "infraspecific"

    first = text.split()[0].strip(".,:;")
    if N.canonical_family(first):
        return "family"

    m = _BARE_GENUS_RE.match(text)
    if m and N.strip_accents(m.group(1)).upper() not in _STRUCTURAL_WORDS:
        return "genus"

    m = N._SPECIES_RE.match(text)
    if m:
        g = N.strip_accents(m.group(1)).capitalize()
        epithet, rest = m.group(2), m.group(3).strip()
        # Require a plausible authority (or nothing) after the binomial, so
        # sentence fragments beginning with two words do not qualify.
        if epithet[0].islower() and (not rest or _AUTHORITY_LEAD_RE.match(rest) or rest.startswith("(")):
            if _resolve_genus(g, "", known):
                return "species"
    return None


def _resolve_genus(found: str, enclosing: str, known: set[str]) -> str | None:
    """Map the genus word of a species heading onto a genus of this treatment.

    Returns the corrected genus, or None when the heading does not belong to
    any genus here and should not become a species page.
    """
    if found in known:
        return found
    if enclosing and N.edit_distance_le_1(found, enclosing):
        return enclosing
    for g in known:
        if N.edit_distance_le_1(found, g):
            return g
    return None


def segment(treatment: Treatment) -> list[Block]:
    """Split a treatment into per-taxon blocks.

    A block's `text` is its *own* prose — from its heading down to its first
    child heading — so a family block carries the family description and key
    without duplicating every species underneath it. `span_end` records where
    the whole subtree ends, for callers that want the full slice.
    """
    lines = treatment.text_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    heads, all_terminators = _headings(lines, treatment.family)
    blocks: list[Block] = []

    # Only back matter *after* the last taxon counts. A "SOMMAIRE" on page 5 is
    # front matter inside the treatment, not the end of the taxonomic body.
    last_taxon = heads[-1]["line"] if heads else 0
    terminators = [t for t in all_terminators if t > last_taxon]

    def capped(start: int, end: int) -> int:
        """Pull `end` back to the first back-matter heading after `start`."""
        for t in terminators:
            if start < t <= end:
                return t - 1
        return end

    has_family = any(h["rank"] == "family" for h in heads)

    if not has_family and treatment.family:
        # The dir name records the family even when OCR mangled or dropped the
        # heading. Everything above the first genus is the family's own text.
        end = capped(1, heads[0]["line"] - 1 if heads else len(lines))
        blocks.append(Block(
            rank="family", name=treatment.family, canonical=treatment.family,
            family=treatment.family,
            wiki_path=f"families/{N._ascii_slug(treatment.family)}.md",
            line_start=1, line_end=end, span_end=len(lines),
            page_start=1, page_end=_page_at(lines, len(lines)),
            text="".join(lines[:end]), tier=TIER["family"],
            warnings=["family heading not detected; taken from directory name"],
        ))
    elif heads and heads[0]["line"] > 1:
        # Anything before the first taxonomic heading is volume front matter:
        # suggested citation, editors, DOI, abbreviations. Worth one entry.
        end = heads[0]["line"] - 1
        blocks.append(Block(
            rank="front_matter", name=f"vol{treatment.vol} front matter",
            canonical=f"vol{treatment.vol}", wiki_path=f"volumes/vol{treatment.vol}.md",
            line_start=1, line_end=end, span_end=end,
            page_start=1, page_end=_page_at(lines, end),
            text="".join(lines[:end]), tier=TIER["front_matter"],
        ))

    for idx, h in enumerate(heads):
        p: N.ParsedName = h["parsed"]
        level = RANK_LEVEL[h["rank"]]

        own_end = len(lines)
        span_end = len(lines)
        for later in heads[idx + 1:]:
            own_end = later["line"] - 1
            break
        for later in heads[idx + 1:]:
            if RANK_LEVEL[later["rank"]] <= level:
                span_end = later["line"] - 1
                break
        own_end = capped(h["line"], own_end)
        span_end = capped(h["line"], span_end)

        blocks.append(Block(
            rank=h["rank"], name=p.name, canonical=p.canonical,
            authority=p.authority, family=p.family, genus=p.genus,
            wiki_path=p.wiki_path,
            line_start=h["line"], line_end=own_end, span_end=span_end,
            page_start=h["page"], page_end=_page_at(lines, span_end),
            text="".join(lines[h["line"] - 1:own_end]),
            warnings=list(p.warnings), tier=TIER[h["rank"]],
        ))

    _flag_oversized(blocks)
    return blocks


# A species block far larger than its peers means a heading between it and the
# next taxon went undetected, so it has absorbed material belonging elsewhere.
OVERSIZE_FACTOR = 5
OVERSIZE_FLOOR = 20_000


def _flag_oversized(blocks: list[Block]) -> None:
    sizes = sorted(b.char_count for b in blocks if b.rank in ("species", "infraspecific"))
    if len(sizes) < 4:
        return
    median = sizes[len(sizes) // 2]
    limit = max(median * OVERSIZE_FACTOR, OVERSIZE_FLOOR)
    for b in blocks:
        if b.rank in ("species", "infraspecific") and b.char_count > limit:
            b.warnings.append(
                f"block is {b.char_count} chars vs {median} median for this "
                f"treatment — a heading was probably missed; review before use"
            )
            b.tier = "synthesis"


def _page_at(lines: list[str], lineno: int) -> int:
    """Page number in force at a given 1-indexed line."""
    page = 0
    for line in lines[:lineno]:
        m = PAGE_RE.search(line)
        if m:
            page = int(m.group(1))
    return page


# ── Figure assignment ─────────────────────────────────────────────────────────

_FIG_PAGE_RE = re.compile(r"_p(\d+)\.")


def _page_from_name(filename: str) -> int:
    """fig_025_p0011.png → 11. Returns 0 when the name carries no page."""
    m = _FIG_PAGE_RE.search(filename or "")
    return int(m.group(1)) if m else 0


def assign_figures(treatment: Treatment, blocks: list[Block]) -> list[dict]:
    """Attach each figure to the taxa it depicts.

    Plate captions in Flore du Gabon name their subject ("PL. 22. – Connarus
    gabonensis Lemmens : 1, rameau ..."), so caption binomials give an exact
    match. Page containment is the fallback. Returns the figures that could not
    be placed on any species page.
    """
    meta_path = treatment.path / "metadata.json"
    if not meta_path.exists():
        return []
    try:
        figures = json.loads(meta_path.read_text(encoding="utf-8")).get("figures", [])
    except (json.JSONDecodeError, OSError):
        return []

    # MinerU records figures as bare filenames; paddle/liteparse as objects with
    # page and caption. Normalise to the richer shape.
    figures = [
        f if isinstance(f, dict) else {"filename": f, "page": _page_from_name(f), "caption": None}
        for f in figures
    ]

    by_binomial: dict[tuple[str, str], list[Block]] = {}
    for b in blocks:
        if b.rank in ("species", "infraspecific") and b.genus:
            ep = b.name.split()[1] if len(b.name.split()) > 1 else ""
            by_binomial.setdefault((b.genus, ep), []).append(b)

    family_block = next((b for b in blocks if b.rank == "family"), None)
    unplaced: list[dict] = []

    for fig in figures:
        page = fig.get("page", 0)
        caption = fig.get("caption") or ""
        entry = {
            "filename": fig.get("filename"),
            "page": page,
            "caption": caption or None,
            "path": f"../sources/{treatment.slug}/figures/{fig.get('filename')}",
        }

        # Cover art and mastheads: flagged, never linked.
        if not caption and page <= COVER_PAGES:
            entry["decorative"] = True
            unplaced.append(entry)
            continue

        targets: list[Block] = []
        seen: set[int] = set()
        for binom in N.find_binomials(caption):
            for b in by_binomial.get(binom, []):
                if id(b) not in seen:
                    seen.add(id(b))
                    targets.append(b)

        if targets:
            entry["match"] = "caption"
        else:
            for b in blocks:
                if b.rank in ("species", "infraspecific") and b.page_start <= page <= b.page_end:
                    targets = [b]
                    entry["match"] = "page"
                    break

        if not targets and family_block is not None:
            targets = [family_block]
            entry["match"] = "family-fallback"

        if targets:
            for b in targets:
                b.figures.append(entry)
        else:
            unplaced.append(entry)

    return unplaced


# ── Bundles ───────────────────────────────────────────────────────────────────

def build_bundle(treatment: Treatment, wiki_root: Path) -> dict:
    blocks = segment(treatment)
    unplaced = assign_figures(treatment, blocks)

    payload_blocks = []
    for b in blocks:
        d = asdict(b)
        d["char_count"] = b.char_count
        d["exists"] = (wiki_root / b.wiki_path).exists() if b.wiki_path else False
        payload_blocks.append(d)

    counts: dict[str, int] = {}
    for b in blocks:
        counts[b.rank] = counts.get(b.rank, 0) + 1

    return {
        "treatment": {
            "slug": treatment.slug,
            "vol": treatment.vol,
            "engine": treatment.engine,
            "family": treatment.family,
            "source": f"sources/{treatment.slug}",
            "text_file": treatment.text_path.name,
            "translated": treatment.translated,
            "page_count": treatment.page_count,
            "figure_count": treatment.figure_count,
            "char_count": treatment.char_count,
        },
        "counts": counts,
        "blocks": payload_blocks,
        "unplaced_figures": unplaced,
    }


def write_bundle(treatment: Treatment, wiki_root: Path, out_dir: Path) -> Path:
    bundle = build_bundle(treatment, wiki_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{treatment.slug}.json"
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ── Frontmatter / index ───────────────────────────────────────────────────────

_SCALAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")


def read_frontmatter(path: Path) -> dict:
    """Parse the top-level scalar and inline-list fields of a page's frontmatter.

    Deliberately minimal — enough for index/status, no YAML dependency. Nested
    blocks (`treatments:`) are skipped.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    _, _, rest = text.partition("---")
    body, sep, _ = rest.partition("\n---")
    if not sep:
        return {}

    out: dict = {}
    for line in body.splitlines():
        if not line.strip() or line.startswith((" ", "\t", "-", "#")):
            continue
        m = _SCALAR_RE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if not val:
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            out[key] = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
        else:
            out[key] = val.strip("'\"")
    return out


def wiki_pages(wiki_root: Path) -> dict[str, list[tuple[Path, dict]]]:
    """All wiki pages grouped by their frontmatter `type`."""
    groups: dict[str, list[tuple[Path, dict]]] = {}
    for sub in ("families", "genera", "species", "volumes", "topics"):
        d = wiki_root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            fm = read_frontmatter(p)
            groups.setdefault(fm.get("type", sub.rstrip("s")), []).append((p, fm))
    return groups


def reindex(wiki_root: Path) -> str:
    """Regenerate index.md from page frontmatter."""
    groups = wiki_pages(wiki_root)
    lines = [
        "# Index",
        "",
        "<!-- Generated by flora_ocr.wiki.ingest reindex — do not hand-edit. -->",
        "",
    ]

    def section(title: str, kind: str, describe) -> None:
        entries = groups.get(kind, [])
        lines.append(f"## {title} ({len(entries)})")
        lines.append("")
        for path, fm in entries:
            name = fm.get("name") or path.stem
            lines.append(f"- [[{path.stem}|{name}]] — {describe(fm)}")
        lines.append("")

    def _count(n: str, singular: str, plural: str) -> str:
        return f"{n} {singular if n.strip() == '1' else plural}"

    def fam_desc(fm: dict) -> str:
        bits = []
        if fm.get("genera_in_gabon"):
            bits.append(_count(str(fm["genera_in_gabon"]), "genus", "genera"))
        if fm.get("species_in_gabon"):
            bits.append(_count(str(fm["species_in_gabon"]), "species", "species")
                        + " in Gabon")
        return ", ".join(bits) or "family page"

    def gen_desc(fm: dict) -> str:
        fam = fm.get("family", "")
        n = fm.get("species_in_gabon")
        s = f"[[{fam}]]" if fam else ""
        return f"{s}{f', {n} species in Gabon' if n else ''}" or "genus page"

    def sp_desc(fm: dict) -> str:
        bits = [b for b in (fm.get("habit"), ", ".join(fm.get("distribution_gabon", []) or [])) if b]
        return " — ".join(bits) or "species page"

    def vol_desc(fm: dict) -> str:
        fams = ", ".join(fm.get("families", []) or [])
        year = fm.get("year", "")
        return f"{year}{': ' if year and fams else ''}{fams}" or "volume page"

    section("Families", "family", fam_desc)
    section("Genera", "genus", gen_desc)
    section("Species", "species", sp_desc)
    section("Volumes", "volume", vol_desc)
    section("Topics", "topic", lambda fm: fm.get("summary", "topic page"))

    return "\n".join(lines) + "\n"


# ── CLI ───────────────────────────────────────────────────────────────────────

def _filter(treatments: list[Treatment], args) -> list[Treatment]:
    out = treatments
    if getattr(args, "family", None):
        want = args.family.lower()
        out = [t for t in out if (t.family or "").lower() == want]
    if getattr(args, "vol", None):
        out = [t for t in out if t.vol == args.vol]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_flora_arg(ap)
    ap.add_argument("--wiki", default=str(REPO_ROOT / "wiki"),
                    help="wiki vault root (default: <repo>/wiki)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("discover", help="list treatments in the OCR output")
    p.add_argument("--family"); p.add_argument("--vol")

    p = sub.add_parser("segment", help="show taxon blocks for one treatment")
    p.add_argument("--family"); p.add_argument("--vol")
    p.add_argument("--show-warnings", action="store_true")

    p = sub.add_parser("bundle", help="write bundle JSON")
    p.add_argument("--family"); p.add_argument("--vol")
    p.add_argument("--out", default=str(REPO_ROOT / "build" / "wiki_bundles"))
    p.add_argument("--all", action="store_true", help="bundle every treatment")

    p = sub.add_parser("status", help="ingested vs pending treatments")
    p.add_argument("--family"); p.add_argument("--vol")

    sub.add_parser("reindex", help="regenerate wiki/index.md")

    args = ap.parse_args(argv)
    flora = load_flora(args.flora)
    wiki_root = Path(args.wiki)

    if args.cmd == "reindex":
        out = wiki_root / "index.md"
        out.write_text(reindex(wiki_root), encoding="utf-8")
        print(f"index.md regenerated → {out}")
        return 0

    treatments = _filter(discover(flora.output_dir), args)

    if args.cmd == "discover":
        print(f"{len(treatments)} treatments in {flora.output_dir}\n")
        print(f"{'family':<24} {'vol':>5} {'engine':<10} {'pages':>6} {'figs':>5} {'KB':>7}  text")
        for t in treatments:
            print(f"{(t.family or '(unsplit)'):<24} {t.vol:>5} {t.engine:<10} "
                  f"{t.page_count:>6} {t.figure_count:>5} {t.char_count // 1024:>7}  "
                  f"{t.text_path.name}{'' if t.translated else '  [untranslated]'}")
        return 0

    if args.cmd == "segment":
        for t in treatments:
            blocks = segment(t)
            unplaced = assign_figures(t, blocks)
            print(f"\n=== {t.slug} ===")
            for b in blocks:
                warn = f"  ⚠ {'; '.join(b.warnings)}" if b.warnings and args.show_warnings else ""
                print(f"  {b.rank:<14} {b.name:<44} p{b.page_start}-{b.page_end} "
                      f"{b.char_count:>7}c {len(b.figures):>3}fig  {b.wiki_path}{warn}")
            print(f"  -- {len(blocks)} blocks, {len(unplaced)} unplaced figures")
        return 0

    if args.cmd == "bundle":
        if not (args.all or args.family or args.vol):
            print("refusing to bundle everything without --all", file=sys.stderr)
            return 2
        out_dir = Path(args.out)
        total = 0
        for t in treatments:
            path = write_bundle(t, wiki_root, out_dir)
            total += 1
            print(f"  → {path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path}")
        print(f"{total} bundles written to {out_dir}")
        return 0

    if args.cmd == "status":
        pages = wiki_pages(wiki_root)
        have = {fm.get("name", p.stem) for p, fm in pages.get("family", [])}
        done = [t for t in treatments if t.family in have]
        todo = [t for t in treatments if t.family not in have]
        print(f"ingested: {len(done)}    pending: {len(todo)}\n")
        print("pending treatments:")
        for t in todo:
            print(f"  {(t.family or '(unsplit)'):<24} vol {t.vol:<5} "
                  f"{t.char_count // 1024:>6} KB  {t.slug}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
