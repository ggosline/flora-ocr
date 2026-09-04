"""Rejoin genus diagnoses that were written one source line per paragraph.

`gen_genus.diagnosis_text` joined the bundle's hard-wrapped lines with a blank
line each, so every line of the scan became its own markdown paragraph -- 444
genus pages, 1116 of the breaks falling mid-sentence. The generator is fixed,
but regenerating these pages would throw away the paid translation sitting in
them, so the rendered text is repaired in place instead.

The paragraph rule is the generator's: a line ends a paragraph when it stops on
sentence punctuation *and* falls short of the block's usual line width -- the
shape of a last line in a justified column -- or when the next line is an
explicit opener (`Uses:`, `Genus of ...`, the bibliography).

Line-end hyphens are treated differently here than in the generator. In an
untranslated section the lines are the scan's, so a trailing hyphen is a broken
word and is closed up; in a translated one the model was handed whole
paragraphs and emitted whole words, so a trailing hyphen is real (`non-` +
`ramified`) and is kept.
"""
from __future__ import annotations

import argparse
import re
import statistics
from pathlib import Path

from flora_ocr.flora import REPO_ROOT
from flora_ocr.wiki.gen_genus import PARA_OPENER_RE, SENTENCE_END_RE

WIKI_DIR = REPO_ROOT / "wiki"
SECTION_RE = re.compile(r"(^## Diagnosis\n)(.*?)(?=^## |\Z)", re.S | re.M)
TRANSLATE_MARKER = "<!-- TODO:translate"


# A line-end hyphen is either a word broken across the line -- `petio-` + `late`
# -- or a real hyphen in a compound that happened to break there -- `long-` +
# `petiolate`. The two are told apart by whether the piece before the hyphen is
# itself a word: `long`, `opposite` and `obovate` are, `petio`, `distri` and
# `em` are not. "Is it a word" is answered from the corpus's own vocabulary,
# with a length floor because short fragments collide with real words (`an-` +
# `thers`), and a list of prefixes that never stand alone.
HYPHEN_PREFIXES = {
    "non", "sub", "semi", "pseudo", "quasi", "multi", "pluri", "uni", "bi",
    "tri", "inter", "intra", "infra", "supra", "ex", "post", "pre", "self",
}
MIN_COMPOUND_HEAD = 4
LAST_WORD_RE = re.compile(r"([A-Za-z\u00c0-\u024f]+)-$")
WORD_RE = re.compile(r"(?<![-\w])([a-z\u00e0-\u024f]{4,})(?![-\w])")
_vocabulary: set[str] | None = None


def vocabulary(directory: Path) -> set[str]:
    """Lowercase words seen standing alone somewhere in the pages."""
    global _vocabulary
    if _vocabulary is None:
        _vocabulary = set()
        for path in sorted(directory.rglob("*.md")):
            _vocabulary.update(WORD_RE.findall(path.read_text(encoding="utf-8")))
    return _vocabulary


def _closes_up(left: str, vocab: set[str]) -> bool:
    """Is this line-end hyphen a broken word rather than a real hyphen?"""
    m = LAST_WORD_RE.search(left)
    if not m:
        return True
    word = m.group(1).lower()
    if word in HYPHEN_PREFIXES:
        return False
    return not (len(word) >= MIN_COMPOUND_HEAD and word in vocab)


def join_lines(lines: list[str], *, close_hyphens: bool,
               vocab: set[str] | None = None) -> str:
    vocab = vocab if vocab is not None else set()
    out = ""
    for line in lines:
        if not out:
            out = line
        elif out.endswith("-") and (close_hyphens or _closes_up(out, vocab)):
            out = out[:-1] + line           # `petio-` + `late`
        elif out.endswith("-"):
            out += line                     # `long-` + `petiolate`
        else:
            out += " " + line
    return out.strip()


STRUCTURAL_RE = re.compile(r"^(?:-{3,}|<<<|#{1,6}\s|\||\[)")


def is_wrapped(lines: list[str]) -> bool:
    """Is this section one scan line per paragraph, rather than real prose?

    Most genus pages came out of the translator reflowed into proper
    paragraphs, and merging *those* would run the description into the
    bibliography. The broken ones are recognisable by shape: short blocks, all
    about a column wide, with sentences running from one into the next.
    """
    if len(lines) < 3:
        return False
    width = statistics.median(len(x) for x in lines)
    if not 45 <= width <= 130:
        return False           # too narrow to be prose: a key or a name list
    if max(len(x) for x in lines) > 250:
        return False           # a long block means the text is already reflowed
    unfinished = sum(1 for x in lines[:-1] if not SENTENCE_END_RE.search(x))
    return unfinished >= 0.25 * (len(lines) - 1)


def rejoin(section: str, vocab: set[str] | None = None) -> str:
    """Rebuild the paragraphs of one Diagnosis section."""
    blocks = [b.strip() for b in section.split("\n\n")]
    blocks = [b for b in blocks if b]
    if any("\n" in b for b in blocks):
        return section                      # not one block per line: leave it
    markers = [b for b in blocks if b.startswith("<!--")]
    body = [b for b in blocks if not b.startswith("<!--")]
    prose = [b for b in body if not STRUCTURAL_RE.match(b)]
    if not is_wrapped(prose):
        return section
    close_hyphens = TRANSLATE_MARKER in section

    width = statistics.median(len(x) for x in prose)
    out: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            out.append(join_lines(current, close_hyphens=close_hyphens,
                                  vocab=vocab))
            current.clear()

    for i, line in enumerate(body):
        if STRUCTURAL_RE.match(line):
            flush()                         # never merge across a rule or heading
            out.append(line)
            continue
        current.append(line)
        nxt = body[i + 1] if i + 1 < len(body) else None
        ends = bool(SENTENCE_END_RE.search(line)) and len(line) < 0.85 * width
        if nxt is not None and PARA_OPENER_RE.match(nxt):
            ends = True
        if ends:
            flush()
    flush()
    return "\n" + "\n\n".join(markers + out) + "\n\n"


def fix(text: str, vocab: set[str] | None = None) -> str:
    return SECTION_RE.sub(lambda m: m.group(1) + rejoin(m.group(2), vocab), text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR / "genera"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    pages = merged = 0
    vocab = vocabulary(Path(args.dir))
    for path in sorted(Path(args.dir).rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        fixed = fix(text, vocab)
        if fixed == text:
            continue
        pages += 1
        merged += text.count("\n\n") - fixed.count("\n\n")
        if not args.dry_run:
            path.write_text(fixed, encoding="utf-8")

    verb = "would rejoin" if args.dry_run else "rejoined"
    print(f"{verb} {merged} wrapped lines on {pages} genus pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
