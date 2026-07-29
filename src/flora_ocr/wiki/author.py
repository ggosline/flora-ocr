"""Bulk-tier page authoring from ingest bundles.

`flora_ocr.wiki.ingest` splits a treatment into per-taxon blocks and marks each
one with a tier: `synthesis` for family and genus pages, which carry
cross-taxon judgement, and `bulk` for species and infraspecific pages, which are
a bounded transform of one ~2 KB block into a fixed template. This module runs
the bulk tier against a cheap model, leaving the synthesis tier to be authored
by hand.

The prompt is assembled from the wiki's own schema — the species template and
the conventions section are read out of `wiki/CLAUDE.md` at run time, so the
contract cannot drift from the vault — plus one already-written page as an
exemplar.

Commands
--------
  plan     what would be generated, with a token and cost estimate. No API call.
  render   write the exact prompt for one taxon to stdout. No API call.
  run      author the pages (batch by default, --mode sync for small runs)
  collect  fetch a submitted batch and write the pages

Nothing is written to the vault until output passes validation: frontmatter
present and well-formed, the declared name matching the taxon that was asked
for, and the required sections present. Existing pages are never overwritten
unless --overwrite is given.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from flora_ocr.flora import REPO_ROOT, add_flora_arg, load_flora
from flora_ocr.wiki.ingest import build_bundle, discover, read_frontmatter, _filter

# Cheap tier. Override with --model.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# USD per million tokens. These are defaults for the estimate only — verify
# against current pricing before relying on the figure. Override with
# --price-in / --price-out.
DEFAULT_PRICE_IN = 1.00
DEFAULT_PRICE_OUT = 5.00

# The Batch API is half price and this work is entirely offline.
BATCH_DISCOUNT = 0.5

MAX_TOKENS = 8000

# Rough characters-per-token for French/English botanical prose. Only used for
# the estimate; the real counts come back with the response.
CHARS_PER_TOKEN = 3.6


# ── Prompt assembly ───────────────────────────────────────────────────────────

SYSTEM_PREAMBLE = """\
You are writing one page for a regional botanical wiki covering the flora of
Lower Guinea — Nigeria to western DR Congo. The source here is a Flore du Gabon
treatment, one of several sources the wiki draws on. You will be given the
source text for a single taxon — one species
or one infraspecific taxon — and you must return that taxon's wiki page.

Return the page and nothing else: no preamble, no commentary, no code fences.
The response must begin with the YAML frontmatter delimiter `---`.

Hard rules:

- The source is French. Translate into English. Botanical descriptions must
  round-trip: a reader comparing your page to the French source must find every
  measurement, every character, every locality. Do not summarise the
  description.
- Keep source units (mm, cm, m). Do not convert.
- Preserve authorities exactly as printed — `(Engl.) Mildbr.`, `J.Léonard` —
  without standardising spacing or punctuation.
- Do not invent data. If the source does not give a section's content, omit the
  section. Never guess a distribution, a habitat, an altitude or a vernacular
  name.
- Distribution uses `countries:` (flat list, the full range) and an optional
  `subdivisions:` map of country → provinces. Record only places **the source
  itself names**. These treatments usually list collecting localities —
  Makokou, Bélinga, Monts de Cristal — and you must NOT infer the province from
  a locality. Where the source gives localities but no province, omit
  `subdivisions`, set `countries_incomplete: true`, and put the localities in
  the Distribution prose.
- Set `in_region: true` if any country is Nigeria, Cameroon, Equatorial Guinea,
  São Tomé and Príncipe, Gabon, Republic of the Congo, western DR Congo or
  Cabinda.
- Omit any frontmatter field you have no value for. Never emit an empty string
  or an empty list as a placeholder.
- An infraspecific taxon (var./subsp./f.) uses `type: species`, and adds
  `infraspecific_rank:` (var, subsp or f) and `parent_species:` (the binomial).
- The frontmatter `name` must be exactly the taxon name you were given,
  including any infraspecific epithet — `Olax subscorpioidea var. durandii`,
  not `Olax subscorpioidea`.
- If the source is ambiguous, damaged by OCR, or self-contradictory, say so in
  an HTML comment `<!-- ... -->` at the relevant point rather than smoothing it
  over.
- No emojis.

Section discipline — keep these separate, do not merge them:

- `## Distribution` — the overall range, then localities by country. Prose
  only. No specimen lists here.
- `## Habitat and ecology` — forest type, soils, altitude, flowering period.
- `## Specimens examined` — the collector lists from the source, one bullet per
  collector. When the source separates them by country (MATÉRIEL CAMEROUNAIS
  ÉTUDIÉ, MATÉRIEL GABONAIS ÉTUDIÉ), keep that grouping under bold country
  subheadings.

This is a **regional** wiki covering Nigeria to western DR Congo. Cameroonian,
Nigerian and Congolese material is fully in scope: transcribe every country's
specimen list, not just one. Do not treat any country as belonging to "another
flora".
"""


def _section(text: str, heading: str) -> str:
    """Return one markdown section of a document, heading included."""
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == heading)
    except StopIteration:
        return ""
    level = len(heading) - len(heading.lstrip("#"))
    for j in range(start + 1, len(lines)):
        s = lines[j].lstrip("#")
        hashes = len(lines[j]) - len(s)
        if lines[j].startswith("#") and 0 < hashes <= level:
            return "\n".join(lines[start:j]).rstrip()
    return "\n".join(lines[start:]).rstrip()


def schema_excerpt(wiki_root: Path) -> str:
    """The parts of wiki/CLAUDE.md that govern a species page."""
    text = (wiki_root / "CLAUDE.md").read_text(encoding="utf-8")
    parts = [
        _section(text, "### Species page"),
        _section(text, "## Naming conventions"),
        _section(text, "## Conventions and preferences"),
    ]
    fm = _section(text, "## YAML frontmatter")
    # Keep only the species frontmatter example, not family/genus/volume.
    m = re.search(r"\*\*Species\*\*\n```yaml\n(.*?)```", fm, re.S)
    if m:
        parts.insert(0, "**Species frontmatter fields**\n```yaml\n" + m.group(1) + "```")
    return "\n\n".join(p for p in parts if p)


def exemplar(wiki_root: Path, stem: str) -> str:
    p = wiki_root / "species" / f"{stem}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def build_system(wiki_root: Path, exemplar_stem: str | None) -> str:
    blocks = [SYSTEM_PREAMBLE, "# Schema\n\n" + schema_excerpt(wiki_root)]
    if exemplar_stem:
        ex = exemplar(wiki_root, exemplar_stem)
        if ex:
            blocks.append(
                "# Worked example\n\nThis is an accepted page in the vault. "
                "Match its structure, depth and tone.\n\n" + ex
            )
    return "\n\n".join(blocks)


def build_user(block: dict, treatment: dict) -> str:
    figs = "\n".join(
        f"- `{f['path']}` (page {f['page']}, matched by {f.get('match','?')})"
        f"{chr(10)}  caption: {f['caption']}" if f.get("caption")
        else f"- `{f['path']}` (page {f['page']}, no caption in source)"
        for f in block.get("figures", [])
    ) or "(none)"

    warn = "\n".join(f"- {w}" for w in block.get("warnings", [])) or "(none)"

    return f"""\
Write the wiki page for this taxon.

## Taxon

- rank: {block['rank']}
- name: {block['name']}
- authority as printed: {block.get('authority') or '(none captured)'}
- genus: {block.get('genus') or '(none)'}
- family: {block.get('family') or treatment.get('family') or '(none)'}
- target file: `{block['wiki_path']}`

## Treatment

- Flore du Gabon, volume {treatment['vol']}
- source directory: `{treatment['source']}`
- pages {block['page_start']}–{block['page_end']}
- source language: {'English (already translated)' if treatment['translated'] else 'French (translate it)'}

Cite the source as:
`Flore du Gabon, Volume {treatment['vol']}: {block['page_start']}–{block['page_end']}.`
Use `{treatment['source']}` as the frontmatter `source` value.

## Figures available

{figs}

Link each figure with a markdown image whose path is exactly the one given
above, with a caption translated or synthesised from the source. If a figure
plainly has no botanical content, omit it.

## Segmenter warnings

{warn}

## Source text

Everything below is the source for this taxon and nothing else.

---

{block['text']}
"""


# ── Planning ──────────────────────────────────────────────────────────────────

@dataclass
class Job:
    custom_id: str
    block: dict
    treatment: dict
    wiki_path: str

    @property
    def name(self) -> str:
        return self.block["name"]


def collect_jobs(bundles: list[dict], wiki_root: Path, *, tier: str = "bulk",
                 overwrite: bool = False,
                 only_family: str | None = None) -> tuple[list[Job], list[str]]:
    """Jobs to run, and the taxa skipped because their page already exists.

    `only_family` filters on each block's own family, not on the directory: a
    single OCR directory can hold several family treatments (see
    Olacaceae_vol20_paddle), and asking for one family should not author the
    others.
    """
    jobs: list[Job] = []
    skipped: list[str] = []
    seen: set[str] = set()
    want = only_family.lower() if only_family else None
    for b in bundles:
        t = b["treatment"]
        for blk in b["blocks"]:
            if blk["tier"] != tier or blk["rank"] not in ("species", "infraspecific"):
                continue
            if want and (blk.get("family") or "").lower() != want:
                continue
            wp = blk["wiki_path"]
            if not wp or wp in seen:
                continue
            seen.add(wp)
            if (wiki_root / wp).exists() and not overwrite:
                skipped.append(blk["name"])
                continue
            jobs.append(Job(
                custom_id=re.sub(r"[^A-Za-z0-9_-]", "_", Path(wp).stem)[:60],
                block=blk, treatment=t, wiki_path=wp,
            ))
    return jobs, skipped


def estimate(jobs: list[Job], system: str, *, price_in: float, price_out: float,
             batch: bool) -> dict:
    sys_tok = len(system) / CHARS_PER_TOKEN
    tok_in = sum(len(build_user(j.block, j.treatment)) / CHARS_PER_TOKEN + sys_tok
                 for j in jobs)
    # Output runs a little longer than the source block: translation plus the
    # frontmatter and section scaffolding.
    tok_out = sum(len(j.block["text"]) / CHARS_PER_TOKEN * 1.15 + 250 for j in jobs)
    mult = BATCH_DISCOUNT if batch else 1.0
    return {
        "jobs": len(jobs),
        "tokens_in": int(tok_in),
        "tokens_out": int(tok_out),
        "cost_usd": (tok_in / 1e6 * price_in + tok_out / 1e6 * price_out) * mult,
        "batch": batch,
    }


# ── Validation ────────────────────────────────────────────────────────────────

# A stub species that only keys out its varieties carries no description of its
# own; requiring one would push the model to invent it.
DESCRIPTION_MIN_SOURCE = 1500


def _strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def validate(page: str, job: Job) -> list[str]:
    """Return a list of problems; empty means the page may be written."""
    problems: list[str] = []
    text = page.strip()

    if not text.startswith("---"):
        problems.append("does not start with YAML frontmatter")
        return problems

    body = text.split("---", 2)
    if len(body) < 3:
        problems.append("frontmatter is not terminated")
        return problems

    fm = {}
    for line in body[1].splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if m and m.group(2).strip():
            fm[m.group(1)] = m.group(2).strip().strip("'\"")

    # Varieties and subspecies live in species/ and are typed as species, with
    # their rank carried in infraspecific_rank. See wiki/CLAUDE.md.
    if fm.get("type") != "species":
        problems.append(f"frontmatter type is {fm.get('type')!r}, expected 'species'")

    if job.block["rank"] == "infraspecific" and not fm.get("infraspecific_rank"):
        problems.append("infraspecific taxon is missing infraspecific_rank")

    got, want = fm.get("name", ""), job.block["name"]
    if got.replace("*", "").strip().lower() != want.lower():
        problems.append(f"frontmatter name is {got!r}, expected {want!r}")

    required = ["## Source"]
    if len(job.block["text"]) >= DESCRIPTION_MIN_SOURCE:
        required.append("## Description")
    for s in required:
        if s not in text:
            problems.append(f"missing section {s!r}")


    if len(text) < 400:
        problems.append(f"page is only {len(text)} chars — probably truncated or refused")

    if "```" in text:
        problems.append("contains a code fence — the model wrapped its output")

    return problems


_EMPTY_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:\s*(\"\"|''|\[\]|~|null)?\s*$")


def strip_empty_frontmatter(page: str) -> str:
    """Drop frontmatter fields left as empty placeholders.

    The model is told to omit fields it has no value for and mostly does, but
    still emits `altitude_m: ""` or `distribution_gabon: []` often enough to be
    worth removing deterministically rather than rejecting the whole page.
    Lines introducing a nested block (`treatments:`) are kept.
    """
    parts = page.split("---", 2)
    if len(parts) < 3:
        return page
    kept = []
    lines = parts[1].splitlines()
    for i, line in enumerate(lines):
        if _EMPTY_FIELD_RE.match(line):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if nxt.startswith((" ", "\t", "-")):
                kept.append(line)      # introduces a nested block
            continue
        kept.append(line)
    return "---" + "\n".join(kept) + "---" + parts[2]


def strip_unsupported_places(page: str, source: str) -> tuple[str, list[str]]:
    """Remove subdivisions the source never names.

    The cheap tier reliably infers a province from a locality even when told not
    to — 'Monts de Cristal' becoming 'Ogooué-Lolo', which is wrong. Rejecting the
    page just retries the same tendency, so correct it instead: drop the
    unsupported entries and report what was dropped. The localities themselves
    survive in the Distribution prose.

    Operates on every country in the `subdivisions:` map. Country names in
    `countries:` are left alone, because they legitimately translate ('Cameroon'
    is supported by 'MATÉRIEL CAMEROUNAIS', 'Democratic Republic of the Congo'
    by 'Zaïre'), whereas subdivision names are proper nouns that appear verbatim.
    """
    parts = page.split("---", 2)
    if len(parts) < 3:
        return page, []

    notes: list[str] = []
    hay = _strip_accents(source).lower()
    out_lines: list[str] = []
    in_subs = False

    for line in parts[1].splitlines():
        if re.match(r"^subdivisions:\s*$", line):
            in_subs = True
            out_lines.append(line)
            continue
        if in_subs:
            m = re.match(r"^\s+([^:]+):\s*\[(.*)\]\s*$", line)
            if m:
                country, keep, drop = m.group(1).strip(), [], []
                for place in m.group(2).split(","):
                    pl = place.strip().strip("'\"")
                    if not pl:
                        continue
                    (keep if _strip_accents(pl).lower() in hay else drop).append(pl)
                if drop:
                    notes.append(f"{country}: dropped unsupported "
                                 f"subdivision(s) {', '.join(drop)}")
                if keep:
                    out_lines.append(f"  {country}: [{', '.join(keep)}]")
                continue
            if not line.startswith((" ", "	")):
                in_subs = False
        out_lines.append(line)

    # A subdivisions: header left with no entries under it is invalid YAML-ish.
    cleaned: list[str] = []
    for i, line in enumerate(out_lines):
        if re.match(r"^subdivisions:\s*$", line):
            nxt = out_lines[i + 1] if i + 1 < len(out_lines) else ""
            if not nxt.startswith((" ", "	")):
                notes.append("removed empty subdivisions block")
                continue
        cleaned.append(line)

    if notes:
        cleaned.append("countries_incomplete: true")

    return "---" + "\n".join(cleaned) + "---" + parts[2], notes


def write_page(page: str, job: Job, wiki_root: Path) -> tuple[Path, list[str]]:
    page, notes = strip_unsupported_places(page.strip(), job.block["text"])
    out = wiki_root / job.wiki_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(strip_empty_frontmatter(page) + "\n", encoding="utf-8")
    return out, notes


# ── API ───────────────────────────────────────────────────────────────────────

def api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        import keyring
        for service, name in (("herbarium-cloud", "anthropic"), ("anthropic", "api_key")):
            k = keyring.get_password(service, name)
            if k:
                return k
    except Exception:
        pass
    raise SystemExit(
        "No Anthropic API key found.\n"
        "  Set ANTHROPIC_API_KEY, or store one with:\n"
        "    keyring set herbarium-cloud anthropic"
    )


def _client():
    try:
        import anthropic
    except ImportError:
        raise SystemExit("anthropic SDK not installed:  uv pip install anthropic")
    return anthropic.Anthropic(api_key=api_key())


class _Fatal(SystemExit):
    pass


def _guard(fn, *a, **kw):
    """Turn SDK errors into a one-line exit instead of a traceback."""
    import anthropic
    try:
        return fn(*a, **kw)
    except anthropic.AuthenticationError:
        raise _Fatal(
            "Anthropic rejected the API key (401).\n"
            "  The stored key may have been rotated or revoked. Replace it with:\n"
            "    .venv\\Scripts\\keyring.exe set herbarium-cloud anthropic\n"
            "  (run that in a real terminal — it needs an interactive prompt)"
        )
    except anthropic.RateLimitError:
        raise _Fatal("Rate limited (429). Retry later, or use --mode batch.")
    except anthropic.APIStatusError as e:
        raise _Fatal(f"Anthropic API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError as e:
        raise _Fatal(f"Could not reach the Anthropic API: {e}")


def run_sync(jobs: list[Job], system: str, model: str, wiki_root: Path,
             *, dry: bool = False) -> tuple[int, list[tuple[str, list[str]]]]:
    client = _client()
    written, rejected = 0, []
    for i, job in enumerate(jobs, 1):
        print(f"  [{i}/{len(jobs)}] {job.name} …", end="", flush=True)
        resp = _guard(
            client.messages.create,
            model=model, max_tokens=MAX_TOKENS, system=system,
            messages=[{"role": "user", "content": build_user(job.block, job.treatment)}],
        )
        page = "".join(b.text for b in resp.content if b.type == "text")
        problems = validate(page, job)
        if problems:
            rejected.append((job.name, problems))
            print(f" REJECTED ({problems[0]})")
            continue
        _, notes = write_page(page, job, wiki_root)
        written += 1
        print(f" ok ({len(page)} chars)" + (f"  [{'; '.join(notes)}]" if notes else ""))
    return written, rejected


def submit_batch(jobs: list[Job], system: str, model: str) -> str:
    client = _client()
    requests = [{
        "custom_id": j.custom_id,
        "params": {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": build_user(j.block, j.treatment)}],
        },
    } for j in jobs]
    batch = _guard(client.messages.batches.create, requests=requests)
    return batch.id


def collect_batch(batch_id: str, jobs: list[Job], wiki_root: Path,
                  *, wait: bool = False) -> tuple[int, list[tuple[str, list[str]]]]:
    client = _client()
    while True:
        batch = _guard(client.messages.batches.retrieve, batch_id)
        if batch.processing_status == "ended":
            break
        if not wait:
            print(f"batch {batch_id} is {batch.processing_status}; "
                  f"re-run `collect` later or pass --wait")
            return 0, []
        print(f"  {batch.processing_status} … {batch.request_counts}")
        time.sleep(30)

    by_id = {j.custom_id: j for j in jobs}
    written, rejected = 0, []
    for result in _guard(client.messages.batches.results, batch_id):
        job = by_id.get(result.custom_id)
        if job is None:
            continue
        if result.result.type != "succeeded":
            rejected.append((result.custom_id, [f"batch result {result.result.type}"]))
            continue
        page = "".join(b.text for b in result.result.message.content if b.type == "text")
        problems = validate(page, job)
        if problems:
            rejected.append((job.name, problems))
            continue
        _, notes = write_page(page, job, wiki_root)
        if notes:
            print(f"  {job.name}: {'; '.join(notes)}")
        written += 1
    return written, rejected


# ── CLI ───────────────────────────────────────────────────────────────────────

def _load_bundles(args, flora) -> list[dict]:
    """Build bundles in memory from the OCR output, honouring the filters.

    `--family` first tries the directory name, which is the fast path. A family
    need not have its own directory though — Octoknemaceae, Opiliaceae and
    Pentadiplandraceae all live inside Olacaceae_vol20_paddle, because OCR
    truncated their headings past the splitter's recognition. So when no
    directory matches, fall back to scanning treatments for a family block.
    """
    wiki_root = Path(args.wiki)
    all_treatments = discover(flora.output_dir)

    treatments = _filter(all_treatments, args)
    if treatments:
        return [build_bundle(t, wiki_root) for t in treatments]

    if not args.family:
        raise SystemExit("no treatments matched --vol")

    # Narrow by volume first if we can, otherwise this scans the whole corpus.
    candidates = [t for t in all_treatments if not args.vol or t.vol == args.vol]
    print(f"no directory named for {args.family}; scanning "
          f"{len(candidates)} treatment(s) for it …", file=sys.stderr)
    want = args.family.lower()
    found = []
    for t in candidates:
        b = build_bundle(t, wiki_root)
        if any((blk.get("family") or "").lower() == want for blk in b["blocks"]):
            found.append(b)
    if not found:
        raise SystemExit(f"no treatment contains family {args.family!r}")
    for b in found:
        print(f"  found in {b['treatment']['slug']}", file=sys.stderr)
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_flora_arg(ap)
    ap.add_argument("--wiki", default=str(REPO_ROOT / "wiki"))
    ap.add_argument("--family")
    ap.add_argument("--vol")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--exemplar", default="Anacolosa_uncifera",
                    help="species page stem to use as the worked example")
    ap.add_argument("--overwrite", action="store_true",
                    help="regenerate pages that already exist")
    ap.add_argument("--price-in", type=float, default=DEFAULT_PRICE_IN)
    ap.add_argument("--price-out", type=float, default=DEFAULT_PRICE_OUT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("plan", help="estimate work and cost; no API call")
    p = sub.add_parser("render", help="print the prompt for one taxon; no API call")
    p.add_argument("taxon", help="taxon name, e.g. 'Coula edulis'")
    p.add_argument("--system", action="store_true", help="print the system prompt too")
    p = sub.add_parser("run", help="author the pages")
    p.add_argument("--mode", choices=["batch", "sync"], default="batch")
    p.add_argument("--wait", action="store_true", help="poll until the batch finishes")
    p.add_argument("--limit", type=int, help="only the first N taxa")
    p.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    p = sub.add_parser("collect", help="fetch a submitted batch and write pages")
    p.add_argument("batch_id")
    p.add_argument("--wait", action="store_true")

    args = ap.parse_args(argv)
    flora = load_flora(args.flora)
    wiki_root = Path(args.wiki)

    bundles = _load_bundles(args, flora)
    system = build_system(wiki_root, args.exemplar)
    jobs, skipped = collect_jobs(bundles, wiki_root, overwrite=args.overwrite,
                                 only_family=args.family)

    if args.cmd == "render":
        job = next((j for j in jobs if j.name.lower() == args.taxon.lower()), None)
        if job is None:
            avail = ", ".join(sorted(j.name for j in jobs)[:12])
            raise SystemExit(f"taxon {args.taxon!r} not among the pending jobs.\n  {avail} …")
        if args.system:
            print(system)
            print("\n" + "=" * 78 + "\n")
        print(build_user(job.block, job.treatment))
        return 0

    if args.cmd == "plan":
        for batch in (True, False):
            e = estimate(jobs, system, price_in=args.price_in,
                         price_out=args.price_out, batch=batch)
            label = "batch (50% off)" if batch else "sync"
            print(f"{label:<18} {e['jobs']:>4} pages  "
                  f"~{e['tokens_in']:>9,} in  ~{e['tokens_out']:>9,} out  "
                  f"≈ ${e['cost_usd']:.2f}")
        print(f"\nmodel: {args.model}")
        print(f"system prompt: {len(system):,} chars "
              f"(~{int(len(system)/CHARS_PER_TOKEN):,} tokens, sent per request)")
        print(f"already written, skipped: {len(skipped)}")
        print("\nCost is an estimate from a chars-per-token heuristic, at "
              f"${args.price_in}/${args.price_out} per Mtok. Verify current pricing.")
        if jobs:
            print("\npending:")
            for j in jobs[:20]:
                print(f"  {j.name:<48} {len(j.block['text']):>7} chars  → {j.wiki_path}")
            if len(jobs) > 20:
                print(f"  … and {len(jobs) - 20} more")
        return 0

    if args.cmd == "collect":
        written, rejected = collect_batch(args.batch_id, jobs, wiki_root, wait=args.wait)
        _report(written, rejected)
        return 0

    # run
    if args.limit:
        jobs = jobs[:args.limit]
    if not jobs:
        print("nothing to do — every page already exists (use --overwrite to redo)")
        return 0

    e = estimate(jobs, system, price_in=args.price_in, price_out=args.price_out,
                 batch=args.mode == "batch")
    print(f"{len(jobs)} pages, {args.mode} mode, model {args.model}, "
          f"estimated ≈ ${e['cost_usd']:.2f}")
    if not args.yes:
        reply = input("proceed? [y/N] ").strip().lower()
        if reply != "y":
            print("aborted")
            return 1

    if args.mode == "sync":
        written, rejected = run_sync(jobs, system, args.model, wiki_root)
        _report(written, rejected)
        return 0

    batch_id = submit_batch(jobs, system, args.model)
    print(f"submitted batch {batch_id}")
    if args.wait:
        written, rejected = collect_batch(batch_id, jobs, wiki_root, wait=True)
        _report(written, rejected)
    else:
        print(f"collect it with:\n"
              f"  python -m flora_ocr.wiki.author --family {args.family or '<family>'} "
              f"collect {batch_id}")
    return 0


def _report(written: int, rejected: list[tuple[str, list[str]]]) -> None:
    print(f"\nwritten: {written}   rejected: {len(rejected)}")
    for name, problems in rejected:
        print(f"  {name}")
        for p in problems:
            print(f"    - {p}")
    if rejected:
        print("\nRejected pages were NOT written. Re-run to retry them.")


if __name__ == "__main__":
    sys.exit(main())
