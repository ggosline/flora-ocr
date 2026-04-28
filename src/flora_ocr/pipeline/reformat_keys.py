"""Reformat flat dichotomous identification keys into indented/bracketed form.

Input:  text_en.md (or any markdown with botanical keys)
Output: text_en_keyfmt.md (written alongside input by default)

Keys are detected by headings matching "KEY TO …" or "KEY BASED ON …".
Within each key, couplet pairs N / N' are nested by depth using a stack.

Usage:
  python reformat_keys.py --input ocr_output/vol11_paddle/text_en.md
  python reformat_keys.py --input ocr_output/vol11_paddle/text_en.md --output reformatted.md
"""

import argparse
import dataclasses
import pathlib
import re
import sys

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Matches the START of a couplet line.
# Groups: (num_str, prime_chars)
# Handles: "9. ", "9'. ", "5''. ", "5\". " (OCR variants of double-prime)
COUPLET_START = re.compile(
    r"^(\d{1,2})((?:'|\"|[\u2019\u2032])*)\.\s"
)

# Key section headings — English ("KEY TO …") or French ("Clé des …", "Clé des genres")
KEY_HEADING = re.compile(r"(?i)^#{1,6}\s+(?:KEY\b|CL[EÉé]S?\b)")

# Markdown heading (any level) — used as segment boundary
ANY_HEADING = re.compile(r"^#{1,6}\s")

# Dotted leaders — presence marks a terminal couplet
DOTTED_LEADERS = re.compile(r"\.{2,}")

# A bare "species reference" line: starts with digit(s), optional prime, period,
# space, then a capital letter (e.g. "6. E. obanensis." or "2. S. Gilletii.")
SPECIES_REF_LINE = re.compile(r"^\d+['\"]*\.\s+[A-Z]")

# Matches a bare species name with an ABBREVIATED genus: "E. obanensis.",
# "S. Gilletii.", "Ps. Friedrichstahlianum."
# The abbreviation ends with a period, which reliably distinguishes it from
# descriptive text like "Ovary glabrous" or "Flowers subsessile".
_TAXON_ABBREV = re.compile(r"^[A-Z][a-z]*\.\s+[A-Z]?[a-z]")


def _is_truncated_species_ref(line: str) -> bool:
    """Return True if a line that looks like a couplet is actually a bare
    species reference with OCR-dropped dotted leaders.

    Criteria: matches COUPLET_START, but the text after the number is short
    (≤ 50 chars) and consists of an abbreviated-genus species name (X. epithet).
    Descriptive couplet text never starts with an abbreviation+period like that.
    """
    m = COUPLET_START.match(line)
    if not m:
        return False
    text_after = line[m.end():].strip()
    return bool(_TAXON_ABBREV.match(text_after) and len(text_after) <= 50)


# ---------------------------------------------------------------------------
# Dash-format normalisation  (vol 60 / French style)
# ---------------------------------------------------------------------------

# Couplet in dash format: "N. - text"
_DASH_COUPLET = re.compile(r"^(\d{1,2})\.\s+-\s+")
# Second alternative in dash format: "- text" (no number)
_BARE_DASH    = re.compile(r"^-\s+")

_PRIMES = ["", "'", "''", "'''"]


def _split_one_runon(line: str) -> list[str]:
    """Split a single line at the first ' - ' that has dotted leaders before it.

    Returns [line] unchanged if no split point found, or [part1, '- ' + part2].
    Using 'any dotted leaders before the dash' is more robust than trying to
    count words in the terminal name (which varies: 'A. letestui' vs
    'E. obanensis Hutch. & Dalz.').
    """
    if '..' not in line or ' - ' not in line:
        return [line]
    for m in re.finditer(r'\s+-\s+', line):
        if re.search(r'\.{2,}', line[:m.start()]):
            return [line[:m.start()], '- ' + line[m.end():]]
    return [line]


def _split_runon_couplets(lines: list[str]) -> list[str]:
    """Split lines where OCR merged two or more couplet alternatives onto one line.

    Iterates until stable so that triple-merged lines are fully separated.
    """
    result = []
    for line in lines:
        segments = [line]
        for _ in range(5):          # up to 5 splits per line
            next_segments = []
            changed = False
            for seg in segments:
                parts = _split_one_runon(seg)
                next_segments.extend(parts)
                if len(parts) > 1:
                    changed = True
            segments = next_segments
            if not changed:
                break
        result.extend(segments)
    return result


def _is_dash_format(lines: list[str]) -> bool:
    """Return True if any line matches the 'N. - text' dash-couplet format."""
    return any(_DASH_COUPLET.match(l) for l in lines)


def _normalize_dash_format(lines: list[str]) -> list[str]:
    """Convert 'N. - text' / '- text' dash format to 'N. text' / "N'. text" prime format.

    This lets the standard prime-based parser handle vol 60 style keys.
    Trichotomous couplets ('- alt1 / - alt2 / - alt3') become N / N' / N''.
    """
    result = []
    current_num: int | None = None
    alt_count = 0

    for line in lines:
        m = _DASH_COUPLET.match(line)
        if m:
            current_num = int(m.group(1))
            alt_count = 0
            result.append(f"{current_num}. {line[m.end():]}")
            continue
        m2 = _BARE_DASH.match(line)
        if m2 and current_num is not None:
            alt_count += 1
            prime = _PRIMES[min(alt_count, len(_PRIMES) - 1)]
            result.append(f"{current_num}{prime}. {line[m2.end():]}")
            continue
        result.append(line)
    return result


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class LogicalCouplet:
    num: int
    prime: str          # the raw prime chars, e.g. "" or "'" or "''"
    text: str           # full text of the couplet (possibly multi-line joined)
    is_terminal: bool


# ---------------------------------------------------------------------------
# Segment collection
# ---------------------------------------------------------------------------

def collect_segments(lines: list[str]) -> list[tuple[int, int, bool]]:
    """Split lines into segments at ### headings.

    Returns list of (start_idx, end_idx, is_key_section).
    end_idx is exclusive.
    """
    segments = []
    seg_start = 0
    is_key = False

    for i, line in enumerate(lines):
        if ANY_HEADING.match(line) and i > 0:
            segments.append((seg_start, i, is_key))
            seg_start = i
            is_key = bool(KEY_HEADING.match(line))

    segments.append((seg_start, len(lines), is_key))
    return segments


# ---------------------------------------------------------------------------
# OCR normalisation (MinerU scanned output)
# ---------------------------------------------------------------------------

# Full-width period (U+FF0E) and middle dot used instead of ASCII period
_FULLWIDTH_PERIOD = re.compile(r'[\uff0e\u30fb\u00b7]')

# Couplet line starting with capital I or l (OCR of digit 1).
# Handles single digit: "I. " / "I'. " and two-digit: "Io. " / "I2'. " etc.
# "o" is OCR for "0" in two-digit numbers like "10" → "Io".
_OCR_I_START  = re.compile(r'^([Il])([\'"\u2019\u2032]*)\.\s')          # "I." single
_OCR_I2_START = re.compile(r'^([Il])([0-9o])([\'"\u2019\u2032]*)\.\s')  # "Io." / "I2." two-digit


def _normalize_ocr_couplets(lines: list[str]) -> list[str]:
    """Fix common OCR errors in scanned key lines.

    Fixes applied (within a key section, before dash-format normalisation):
    1. Full-width period (U+FF0E etc.) → ASCII period.
       Adds a space after period if missing: "1.Text" → "1. Text".
    2. Capital-I / lowercase-l as digit-1 at line start:
         "I. text"   → "1. text"
         "Io. text"  → "10. text"   (o = OCR artefact for 0)
         "I2'. text" → "12'. text"
       Only applied when the resulting line would match COUPLET_START.
    """
    result = []
    for line in lines:
        # Fix 1: full-width / special periods → ASCII
        line = _FULLWIDTH_PERIOD.sub('.', line)
        # Ensure space after period for digit-led couplets: "1.Text" → "1. Text"
        line = re.sub(r'^(\d{1,2}[\'"\u2019\u2032]*)\.(?!\s)', r'\1. ', line)
        # Same for I/l-led two-digit patterns: "Io.Text" → "Io. Text" (before replacement)
        line = re.sub(r'^([Il][0-9o][\'"\u2019\u2032]*)\.(?!\s)', r'\1. ', line)
        # And single-digit I patterns: "I.Text" → "I. Text"
        line = re.sub(r'^([Il][\'"\u2019\u2032]*)\.(?!\s)', r'\1. ', line)

        # Fix 2a: two-digit "Io." / "I2." at line start
        m2 = _OCR_I2_START.match(line)
        if m2:
            second = '0' if m2.group(2) == 'o' else m2.group(2)
            candidate = '1' + second + m2.group(3) + '. ' + line[m2.end():]
            if COUPLET_START.match(candidate):
                line = candidate
                result.append(line)
                continue

        # Fix 2b: single-digit "I." at line start
        m1 = _OCR_I_START.match(line)
        if m1:
            candidate = '1' + line[1:]
            if COUPLET_START.match(candidate):
                line = candidate

        result.append(line)
    return result


# ---------------------------------------------------------------------------
# Couplet parsing
# ---------------------------------------------------------------------------

def parse_couplets(lines: list[str]) -> list[LogicalCouplet]:
    """Parse lines within a key section into LogicalCouplet objects.

    Handles both vol-11 prime format ("N. text" / "N'. text") and
    vol-60 dash format ("N. - text" / "- text").  Run-together lines
    (two alternatives OCR'd onto one line) are split first.

    Continuation lines (non-blank, non-heading, non-couplet, non-comment)
    are joined onto the preceding couplet.

    A bare species-reference line immediately following a couplet is also
    absorbed into that couplet's text (marks it as terminal).
    """
    # --- Normalise OCR variants common in MinerU scanned output ---
    lines = _normalize_ocr_couplets(lines)
    # --- Pre-processing for dash-format keys ---
    lines = _split_runon_couplets(lines)
    if _is_dash_format(lines):
        lines = _normalize_dash_format(lines)

    couplets: list[LogicalCouplet] = []
    i = 0

    while i < len(lines):
        line = lines[i].rstrip()
        m = COUPLET_START.match(line)

        if m:
            num = int(m.group(1))
            prime = m.group(2)
            text = line[m.end():]   # text after "N'. "
            i += 1

            # Absorb continuation lines (but stop at a genuine new couplet;
            # a truncated species ref that looks like a couplet is handled below)
            while i < len(lines):
                nxt = lines[i].rstrip()
                if (not nxt
                        or ANY_HEADING.match(nxt)
                        or nxt.startswith("<!--")):
                    break
                # Stop at a real new couplet, but NOT at a truncated species ref
                if COUPLET_START.match(nxt) and not _is_truncated_species_ref(nxt):
                    break
                text = text + " " + nxt.strip()
                i += 1

            # Detect terminal: dotted leaders in text, OR next non-blank line
            # looks like a bare species reference (with or without a couplet number)
            has_leaders = bool(DOTTED_LEADERS.search(text))
            species_on_next = False
            if not has_leaders:
                j = i
                while j < len(lines) and not lines[j].strip():
                    j += 1
                nxt_stripped = lines[j].strip() if j < len(lines) else ""
                if nxt_stripped and (
                    (SPECIES_REF_LINE.match(nxt_stripped) and not COUPLET_START.match(nxt_stripped))
                    or _is_truncated_species_ref(nxt_stripped)
                ):
                    # Absorb the species ref into this couplet's text
                    text = text.rstrip() + " " + nxt_stripped
                    i = j + 1
                    species_on_next = True

            is_terminal = has_leaders or species_on_next

            couplets.append(LogicalCouplet(num=num, prime=prime, text=text,
                                           is_terminal=is_terminal))
        else:
            # Non-couplet line in the key section — attach as a "separator"
            # by appending a pseudo-couplet with num=-1 so the formatter
            # can pass it through unchanged.
            if line or couplets:   # skip leading blanks
                couplets.append(LogicalCouplet(num=-1, prime="", text=line,
                                               is_terminal=True))
            i += 1

    return couplets


# ---------------------------------------------------------------------------
# Indenting with stack algorithm
# ---------------------------------------------------------------------------

def indent_key(couplets: list[LogicalCouplet]) -> list[str]:
    """Apply stack-based nesting to a list of LogicalCouplets.

    Stack semantics:
      positive int N  → we are inside couplet N (non-prime branch open)
      negative int -N → we are inside the prime branch of N (N' was internal)

    For a non-prime couplet M:
      - pop entries where abs(entry) >= M  (restart / resume at this level)
      - indent = len(stack)
      - push +M

    For a prime couplet M':
      - find M in stack (by abs), pop it and everything above
      - indent = len(stack)
      - if not terminal: push -M  (prime branch acts as container)
    """
    stack: list[int] = []
    out: list[str] = []

    for lc in couplets:
        if lc.num < 0:
            # Drop anything that would interrupt the markdown list:
            # page-break markers (---), page comments, and blank lines.
            t = lc.text.strip()
            if not t or t == "---" or t.startswith("<!-- page"):
                continue
            out.append(lc.text)
            continue

        num = lc.num
        is_prime = bool(lc.prime)

        if not is_prime:
            # Pop entries >= num (handles restarts and level resets)
            while stack and abs(stack[-1]) >= num:
                stack.pop()
            depth = len(stack)
            stack.append(num)
        else:
            # Find num in stack (abs match), pop it and everything above
            idx = next(
                (i for i in range(len(stack) - 1, -1, -1)
                 if abs(stack[i]) == num),
                None,
            )
            if idx is not None:
                stack = stack[:idx]
            depth = len(stack)
            if not lc.is_terminal:
                stack.append(-num)

        # Use markdown list syntax for indentation.
        # Raw-space indentation (4+ spaces) triggers code blocks in CommonMark;
        # list items with 2-space nesting render correctly at any depth.
        if depth == 0:
            indent = ""
            item_prefix = "- "
        else:
            indent = "  " * depth        # 2 spaces per level (CommonMark list nesting)
            item_prefix = "- "

        # Escape the period so "2\. text" doesn't trigger a numbered list.
        label = f"{num}{lc.prime}\\."
        first_line = f"{indent}{item_prefix}{label} {lc.text}"

        # Re-wrap: if the joined text is very long, split at a reasonable
        # place. For now just emit as-is (markdown will wrap in rendering).
        out.append(first_line)

    return out


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def reformat(lines: list[str]) -> list[str]:
    """Process all lines, reformatting key sections in-place."""
    segments = collect_segments(lines)
    result: list[str] = []

    for start, end, is_key in segments:
        seg_lines = lines[start:end]

        if not is_key:
            result.extend(seg_lines)
            continue

        # Key section: keep the heading line as-is, reformat the rest
        heading = seg_lines[0]
        body = seg_lines[1:]

        couplets = parse_couplets(body)
        reformatted_body = indent_key(couplets)

        result.append(heading)
        result.extend(ln + "\n" for ln in reformatted_body)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Reformat dichotomous keys in a markdown file to indented form.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python reformat_keys.py "
            "--input ocr_output/vol11_paddle/text_en.md\n"
            "  python reformat_keys.py "
            "--input ocr_output/vol11_paddle/text_en.md "
            "--output ocr_output/vol11_paddle/text_en_keyfmt.md\n"
        ),
    )
    parser.add_argument("--input", required=True, help="Input markdown file")
    parser.add_argument(
        "--output",
        help="Output path (default: <input_stem>_keyfmt.md in same directory)",
    )
    args = parser.parse_args()

    in_path = pathlib.Path(args.input)
    if not in_path.exists():
        print(f"Error: {in_path} not found")
        sys.exit(1)

    if args.output:
        out_path = pathlib.Path(args.output)
    else:
        out_path = in_path.with_name(in_path.stem + "_keyfmt" + in_path.suffix)

    lines = in_path.read_text(encoding="utf-8").splitlines(keepends=True)
    print(f"Read {len(lines)} lines from {in_path}")

    segments = collect_segments(lines)
    key_count = sum(1 for _, _, is_key in segments if is_key)
    print(f"Found {key_count} key sections")

    result = reformat(lines)

    out_path.write_text("".join(result), encoding="utf-8")
    print(f"Written to {out_path}")


if __name__ == "__main__":
    main()
