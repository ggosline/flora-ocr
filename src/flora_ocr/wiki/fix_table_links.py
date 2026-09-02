"""Escape the pipe in wiki links that sit inside a markdown table.

`[[Dioncophyllum_thollonii | *D. thollonii*]]` in a table row is not a link.
The table parser sees the bare `|` as a column separator and splits the row
through the middle of the link, so the cell renders as literal text
`[[Dioncophyllum_thollonii` and the target is unreachable. Inside a table the
separator must be escaped: `[[Target\\|label]]`.

45 rows across 18 authored genus pages are affected. The generators already
emit the escaped form, so this is a one-off repair of pages written by hand.

Only table rows are touched. Outside a table `[[Target|label]]` is correct and
is left alone.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from flora_ocr.flora import REPO_ROOT

WIKI_DIR = REPO_ROOT / "wiki"

LINK_RE = re.compile(r"\[\[([^\]\n]*?)(?<!\\)\|([^\]\n]*?)\]\]")


def fix_line(line: str) -> str:
    if not line.lstrip().startswith("|"):
        return line                     # not a table row
    # trim the spaces the hand-written rows padded the separator with, so the
    # link target does not end up with trailing whitespace
    return LINK_RE.sub(lambda m: f"[[{m.group(1).strip()}\\|{m.group(2).strip()}]]",
                       line)


def cells(row: str) -> list[str]:
    """Split a table row on unescaped pipes."""
    return re.split(r"(?<!\\)\|", row.strip())[1:-1]


def drop_phantom_column(lines: list[str]) -> list[str]:
    """Remove the empty trailing column the broken links produced.

    A row split through the middle of a link yielded one cell too many, and the
    header and rule rows were padded to match. Once the link is escaped the
    body rows are one cell short of the header, and the surplus header cell is
    empty. Only that exact shape is touched.
    """
    out = list(lines)
    i = 0
    while i < len(out) - 2:
        header, rule = out[i], out[i + 1]
        if not (header.lstrip().startswith("|") and set(rule.strip()) <= set("|-: ")
                and rule.lstrip().startswith("|")):
            i += 1
            continue
        body = []
        j = i + 2
        while j < len(out) and out[j].lstrip().startswith("|"):
            body.append(j)
            j += 1
        widths = {len(cells(out[k])) for k in body}
        if (body and len(widths) == 1 and len(cells(header)) == widths.pop() + 1
                and not cells(header)[-1].strip()):
            out[i] = "|" + "|".join(cells(header)[:-1]) + "|"
            out[i + 1] = "|" + "|".join(cells(rule)[:-1]) + "|"
        i = j
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    pages = rows = 0
    for path in sorted(Path(args.dir).rglob("*.md")):
        s = str(path)
        if ".obsidian" in s or ".trash" in s or path.name in ("CLAUDE.md", "AGENTS.md"):
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        fixed = drop_phantom_column([fix_line(l) for l in lines])
        changed = sum(1 for a, b in zip(lines, fixed) if a != b)
        if not changed:
            continue
        pages += 1
        rows += changed
        if not args.dry_run:
            path.write_text("\n".join(fixed), encoding="utf-8")

    verb = "would fix" if args.dry_run else "fixed"
    print(f"{verb} {rows} table rows on {pages} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
