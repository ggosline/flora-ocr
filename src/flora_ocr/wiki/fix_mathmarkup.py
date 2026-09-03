"""Turn the LaTeX-style markup PaddleOCR emits back into plain text.

PaddleOCR wraps measurements and symbols it reads as mathematics:
`$ 3-5 \\times 1,5-2,5 $` for "3-5 × 1,5-2,5", `$ \\pm $` for +/-, `$ ^{1} $`
for a footnote marker. 660 spans across 275 pages. In a botanical description
these are measurements and symbols, not equations, so they are unwrapped rather
than rendered.

Only the constructs actually seen in this corpus are handled, and anything
inside `$...$` that is not recognised is left wrapped rather than guessed at,
so nothing is silently mangled.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from flora_ocr.flora import REPO_ROOT

WIKI_DIR = REPO_ROOT / "wiki"

COMMANDS = {
    r"\times": "×", r"\pm": "±", r"\equiv": "≡", r"\neq": "≠",
    r"\leq": "≤", r"\geq": "≥", r"\approx": "≈", r"\to": "→",
    r"\cdot": "·", r"\circ": "°", r"\infty": "∞", r"\alpha": "α",
    r"\beta": "β", r"\gamma": "γ", r"\mu": "μ", r"\sim": "~",
    r"\prime": "'", r"\ldots": "…", r"\%": "%",
}
SUPERSCRIPTS = str.maketrans("0123456789aeionx", "⁰¹²³⁴⁵⁶⁷⁸⁹ᵃᵉⁱᵒⁿˣ")
MATH_RE = re.compile(r"\$\s*([^$]{1,200}?)\s*\$")
SUP_RE = re.compile(r"\^\{([^}]{1,4})\}")
SUB_RE = re.compile(r"_\{([^}]{1,4})\}")


def unwrap(body: str) -> str | None:
    """Plain-text form of one math span, or None if it is not recognised."""
    out = SUP_RE.sub(lambda m: m.group(1).translate(SUPERSCRIPTS), body)
    out = SUB_RE.sub(lambda m: m.group(1), out)
    for command, replacement in COMMANDS.items():
        out = out.replace(command, replacement)
    if "\\" in out or "{" in out or "}" in out:
        return None                     # something unhandled: leave it wrapped
    return re.sub(r"\s+", " ", out).strip()


def fix(text: str) -> str:
    def repl(m: re.Match) -> str:
        out = unwrap(m.group(1))
        return m.group(0) if out is None else out
    return MATH_RE.sub(repl, text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    pages = spans = 0
    for path in sorted(Path(args.dir).rglob("*.md")):
        s = str(path)
        if ".obsidian" in s or ".trash" in s or path.name in ("CLAUDE.md", "AGENTS.md"):
            continue
        text = path.read_text(encoding="utf-8")
        fixed = fix(text)
        if fixed == text:
            continue
        pages += 1
        spans += len(MATH_RE.findall(text)) - len(MATH_RE.findall(fixed))
        if not args.dry_run:
            path.write_text(fixed, encoding="utf-8")

    verb = "would unwrap" if args.dry_run else "unwrapped"
    print(f"{verb} {spans} math spans on {pages} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
