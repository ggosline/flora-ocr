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

KEY_HEADING_RE = re.compile(r"^## Key.*$", re.M)
NUMBERED_LEAD_RE = re.compile(r"^\s*(\d+)\s*(['’]?)\s*[.)]\s*[-–—]?\s*(.*)$")
BARE_LEAD_RE = re.compile(r"^\s*[-–—]\s+(.*)$")
LEADER_RE = re.compile(r"\s*\.{3,}\s*")
# what a lead points at: a couplet number, or a species (optionally numbered)
COUPLET_TARGET_RE = re.compile(r"^(\d+)\s*[.']?$")
SPECIES_TARGET_RE = re.compile(
    r"^(?:\d+\s*[.)]\s*)?([A-Z])\.\s*([a-z][a-z\-]{2,})\s*\.?$")
FULL_SPECIES_TARGET_RE = re.compile(
    r"^(?:\d+\s*[.)]\s*)?([A-Z][a-z]+)\s+([a-z][a-z\-]{2,})\s*\.?$")


RULE_RE = re.compile(r"^-{3,}$")


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
        name, label = f"{genus}_{m.group(2)}", f"{genus[0]}. {m.group(2)}"
    else:
        m = FULL_SPECIES_TARGET_RE.match(target)
        if not m:
            return None
        name, label = f"{m.group(1)}_{m.group(2)}", f"{m.group(1)[0]}. {m.group(2)}"
    if not (WIKI_DIR / "species" / f"{name}.md").exists():
        return f"*{label}*"
    return f"[[{name}\\|*{label}*]]"


def format_key(text: str, genus: str) -> str | None:
    leads = _rejoin(text.split("\n"))
    if not leads:
        return None

    rendered: list[str] = []
    anchors: set[str] = set()
    current = ""
    changed = False

    for lead in leads:
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

        parts = LEADER_RE.split(body)
        target = parts[-1].strip() if len(parts) > 1 else ""
        prose = " ".join(p.strip() for p in parts[:-1]).strip() if target else body.strip()

        arrow = ""
        cm = COUPLET_TARGET_RE.match(target)
        if cm:
            arrow = f" → [[#^k{cm.group(1)}|{cm.group(1)}]]"
        elif target:
            link = _species_link(target, genus)
            arrow = f" → {link}" if link else f" → {target}"

        line = f"**{label}** {prose}{arrow}"
        # anchor the first half of each couplet so links to it resolve
        if first_half and number not in anchors:
            anchors.add(number)
            line = f"{line} ^k{number}"
        rendered.append(line)

    return "\n\n".join(rendered) + "\n" if changed else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR / "genera"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args(argv)

    pages = 0
    for path in sorted(Path(args.dir).glob("*.md")):
        if args.only and path.stem != args.only:
            continue
        text = path.read_text(encoding="utf-8")
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
