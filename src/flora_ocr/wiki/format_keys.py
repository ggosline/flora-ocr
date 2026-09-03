"""Render dichotomous keys as readable, navigable couplets.

As transcribed, a key looks like this:

    1. - Flower with 3 distinct lobes ................................ 2
    - Flower with 2 distinct lobes ................................... 5

Markdown reads the first line as an ordered-list item containing a bullet and
the second as an unrelated top-level bullet, so the two halves of one couplet
render at different indents with a washed-out number on only one of them. The
targets are inert: "..... 2" tells the reader to go to couplet 2 and gives them
no way to get there, and a species lead names a page it does not link to.

This rewrites each key so that:

  both halves of a couplet are formatted alike, as `**1.**` and `**1'.**`,
  bolded rather than numbered so markdown cannot re-list them;
  the leader dots become a single arrow;
  a couplet target links to that couplet, via an Obsidian block anchor placed
  on the line it points at;
  a species target links to the species page when one exists.

Wrapped lines are rejoined first, since a lead may run across several lines.
The text of the lead is never altered.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from flora_ocr.flora import REPO_ROOT

WIKI_DIR = REPO_ROOT / "wiki"

# "## Key" or "## Key to the species", but never "## Keyed but not treated",
# which is a list of species and not a key at all.
KEY_HEADING_RE = re.compile(r"^## Key(?:\s+to\b.*)?$", re.M)
# "1.", "1'.", "1)" and also "1 -", which some volumes use unpunctuated
NUMBERED_LEAD_RE = re.compile(
    r"^\s*(\d+)\s*(['’]?)\s*(?:[.)]\s*[-–—]?|[-–—])\s*(.*)$")
BARE_LEAD_RE = re.compile(r"^\s*[-–—]\s+(.*)$")
LEADER_RE = re.compile(r"\s*\.{3,}\s*")
# what a lead points at: a couplet number, or a species (optionally numbered)
COUPLET_TARGET_RE = re.compile(r"^(\d+)\s*[.']?$")
# The older volumes capitalise the epithet -- "16. D. Hoyleana." -- so the
# epithet pattern must accept either case and lowercase it for the page name.
SPECIES_TARGET_RE = re.compile(
    r"^(?:\d+\s*[.)]\s*)?([A-Z])\.\s*([A-Za-z][a-z\-]{2,})\s*\.?$")
FULL_SPECIES_TARGET_RE = re.compile(
    r"^(?:\d+\s*[.)]\s*)?([A-Z][a-z]+)\s+([A-Za-z][a-z\-]{2,})\s*\.?$")


RULE_RE = re.compile(r"^-{3,}$")
# "I. D'apres les caracteres FOLIAIRES ..." -- a volume with several keys heads
# each one like this. Left as prose it merges into the paragraph above it.
SUBKEY_RE = re.compile(r"^\s*([IVX]{1,4})\s*[.)]\s*(.+)$")


def _is_bare_target(text: str) -> bool:
    """True when a line is only a species name, i.e. the previous lead's target.

    Some scans lose the leader dots and put the target on its own line:

        2. Blade oblique-rhombic, 3-5 x 1.5-2.5 cm ...
        16. D. Hoyleana.

    Read as a lead, that mints a phantom couplet 16.
    """
    body = NUMBERED_LEAD_RE.match(text)
    candidate = body.group(3).strip() if body else text.strip()
    return bool(SPECIES_TARGET_RE.match(candidate)
                or FULL_SPECIES_TARGET_RE.match(candidate))


def _rejoin(lines: list[str]) -> list[str]:
    """Merge continuation lines into the lead they belong to."""
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or RULE_RE.match(stripped):
            continue
        if out and _is_bare_target(stripped) and not LEADER_RE.search(out[-1]):
            m = NUMBERED_LEAD_RE.match(stripped)
            target = (m.group(3) if m else stripped).strip()
            out[-1] = f"{out[-1].rstrip('. ')} ..... {target}"
            continue
        if NUMBERED_LEAD_RE.match(line) or BARE_LEAD_RE.match(line) or not out:
            out.append(stripped)
        else:
            out[-1] = f"{out[-1]} {stripped}"
    return out


def _species_link(target: str, genus: str) -> str | None:
    m = SPECIES_TARGET_RE.match(target)
    if m and genus and m.group(1) == genus[0]:
        epithet = m.group(2).lower()
        name, label = f"{genus}_{epithet}", f"{genus[0]}. {epithet}"
    else:
        m = FULL_SPECIES_TARGET_RE.match(target)
        if not m:
            return None
        epithet = m.group(2).lower()
        name, label = f"{m.group(1)}_{epithet}", f"{m.group(1)[0]}. {epithet}"
    if not (WIKI_DIR / "species" / f"{name}.md").exists():
        return f"*{label}*"
    return f"[[{name}\\|*{label}*]]"


def format_key(text: str, genus: str) -> str | None:
    leads = _rejoin(text.split("\n"))
    if not leads:
        return None

    rendered: list[str] = []
    current = ""
    changed = False

    for lead in leads:
        sub = SUBKEY_RE.match(lead)
        if sub and len(sub.group(2)) > 12:
            rendered.append(f"### {sub.group(1)}. {sub.group(2).strip()}")
            changed = True
            continue
        number = ""
        first_half = False
        m = NUMBERED_LEAD_RE.match(lead)
        if m:
            number, prime, body = m.group(1), m.group(2), m.group(3)
            first_half = not prime
            label = f"{number}{'′' if prime else ''}."
            if not prime:
                current = number
            changed = True
        else:
            m = BARE_LEAD_RE.match(lead)
            if not m:
                rendered.append(lead)       # a note or caption inside the key
                continue
            body, label = m.group(1), f"{current}′." if current else "—"
            changed = True

        parts = [p.strip() for p in LEADER_RE.split(body)]
        target = parts[-1] if len(parts) > 1 else ""
        if target:
            prose = " ".join(parts[:-1]).strip()
        else:
            # the scan lost the target after the leader dots; drop the dots
            # rather than leaving a row of them dangling at the end of a lead
            prose = " ".join(p for p in parts if p).strip()

        arrow = ""
        cm = COUPLET_TARGET_RE.match(target)
        if cm:
            # A plain bold number, not a link. Obsidian block anchors rendered
            # as stray "^k4" superscripts and the references did not resolve,
            # which is worse than an honest pointer.
            arrow = f" → couplet **{cm.group(1)}**"
        elif target:
            link = _species_link(target, genus)
            arrow = f" → {link}" if link else f" → {target}"

        rendered.append(f"**{label}** {prose}{arrow}")

    return "\n\n".join(rendered) + "\n" if changed else None


# A row of leader dots left at the end of an already-formatted lead, where the
# scan lost the target that should have followed them. Formatting is one-way --
# a formatted lead no longer matches the lead patterns -- so this tidies the
# rendered page rather than re-rendering it from source.
LEAD_LINE_RE = re.compile(r"^\*\*.+$", re.M)
LEADER_RUN_RE = re.compile(r"\s*\.{4,}\s*")


def tidy(text: str) -> str:
    """Tidy only inside the key section: elsewhere a `**...**` line is a page
    header like `**Protologue**: Fam. pl. 2 : 327 (1763)`, and appending a stop
    to those corrupted 997 pages on the first attempt."""
    m = KEY_HEADING_RE.search(text)
    if not m:
        return text
    end = text.find("\n## ", m.end())
    end = end if end > 0 else len(text)
    return text[:m.end()] + _tidy_section(text[m.end():end]) + text[end:]


def _tidy_section(text: str) -> str:
    def fix_line(m: re.Match) -> str:
        line = LEADER_RUN_RE.sub(" ", m.group(0))
        line = re.sub(r"\s{2,}", " ", line).rstrip()
        # a lead that lost its target ends mid-sentence; close it with a stop
        if not re.search(r"[.;:]$|\]\]$|\^k\d+$", line):
            line += "."
        return line
    return LEAD_LINE_RE.sub(fix_line, text)


KEY_CACHE = REPO_ROOT / "build" / "key_translations.json"


def rebuild(dry_run: bool, model: str, workers: int) -> int:
    """Re-render every key from the source text, not from the rendered page.

    Formatting is one-way: once a lead reads `**1.** ... -> couplet 2` it no
    longer matches the lead patterns, so a later fix to the formatter cannot
    reach the pages already formatted. That is why Diospyros kept a key with
    bare targets on their own lines after the formatter had learned to merge
    them. Rebuilding from the bundle makes formatting repeatable; the
    translations are cached by content hash, so only genuinely new text is
    paid for.
    """
    import hashlib
    import json

    import anthropic

    from flora_ocr.wiki import gen_genus
    from flora_ocr.wiki.fix_mathmarkup import fix as unmath
    from flora_ocr.wiki.translate_pages import translate as translate_text

    keys: dict[str, str] = {}
    for bundle in gen_genus.load_bundles(None, None):
        for block in bundle["blocks"]:
            if block["rank"] != "genus" or block["name"] in gen_genus.NOT_A_TAXON:
                continue
            name = gen_genus.NAME_CORRECTIONS.get(block["name"], block["name"])
            _, rest = gen_genus.split_heading(block.get("text", ""))
            key = gen_genus.split_key(gen_genus.clean(rest))[1]
            if key and len(key) > len(keys.get(name, "")):
                keys[name] = unmath(key)

    cache = json.loads(KEY_CACHE.read_text()) if KEY_CACHE.exists() else {}
    todo = {n: k for n, k in keys.items()
            if hashlib.sha1(k.encode()).hexdigest() not in cache
            and (WIKI_DIR / "genera" / f"{n}.md").exists()}
    print(f"{len(keys)} keys; {len(todo)} need translating "
          f"({sum(len(v) for v in todo.values()):,} chars)")

    if todo and not dry_run:
        import concurrent.futures as cf
        import threading
        client = anthropic.Anthropic()
        lock = threading.Lock()

        def run(item):
            name, raw = item
            try:
                english = translate_text(client, model, raw)
            except Exception as exc:                       # noqa: BLE001
                print(f"  FAIL {name}: {exc}")
                return None
            with lock:
                cache[hashlib.sha1(raw.encode()).hexdigest()] = english
                print(f"  {name} ({len(raw):,} -> {len(english):,} chars)")
            return name

        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(run, sorted(todo.items())))
        KEY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        KEY_CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False),
                             encoding="utf-8")

    written = 0
    for name, raw in sorted(keys.items()):
        path = WIKI_DIR / "genera" / f"{name}.md"
        english = cache.get(hashlib.sha1(raw.encode()).hexdigest())
        if not path.exists() or not english:
            continue
        formatted = format_key(english, name)
        if formatted is None:
            continue
        text = path.read_text(encoding="utf-8")
        block = f"## Key to the species\n\n{formatted}\n"
        m = KEY_HEADING_RE.search(text)
        if m:
            end = text.find("\n## ", m.end())
            end = end if end > 0 else len(text)
            new = text[:m.start()] + block + text[end + 1:] if end < len(text) \
                else text[:m.start()] + block
        else:
            anchor = re.search(r"^## (Species|Treatments|Notes|See also)", text, re.M)
            if not anchor:
                continue
            new = text[:anchor.start()] + block + "\n" + text[anchor.start():]
        if new == text:
            continue
        written += 1
        if not dry_run:
            path.write_text(new, encoding="utf-8")
    print(f"{'would rewrite' if dry_run else 'rewrote'} {written} key sections")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR / "genera"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--rebuild", action="store_true",
                    help="re-render every key from the source text, translating "
                         "through a content-hash cache")
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--tidy", action="store_true",
                    help="strip leader dots dangling on already-formatted leads")
    args = ap.parse_args(argv)

    if args.rebuild:
        return rebuild(args.dry_run, args.model, args.workers)

    pages = 0
    for path in sorted(Path(args.dir).glob("*.md")):
        if args.only and path.stem != args.only:
            continue
        text = path.read_text(encoding="utf-8")
        if args.tidy:
            tidied = tidy(text)
            if tidied != text:
                pages += 1
                if not args.dry_run:
                    path.write_text(tidied, encoding="utf-8")
            continue
        m = KEY_HEADING_RE.search(text)
        if not m:
            continue
        end = text.find("\n## ", m.end())
        end = end if end > 0 else len(text)
        body = text[m.end():end]
        marker = ""
        if "TODO:" in body:                  # keep any marker line in place
            head, _, rest = body.partition("-->")
            marker, body = head + "-->", rest
        formatted = format_key(body, path.stem)
        if formatted is None:
            continue
        new = f"{m.group(0)}\n{marker}\n\n{formatted}\n"
        if new == text[m.start():end]:
            continue
        pages += 1
        if not args.dry_run:
            path.write_text(text[:m.start()] + new + text[end:], encoding="utf-8")

    verb = "would reformat" if args.dry_run else "reformatted"
    print(f"{verb} keys on {pages} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
