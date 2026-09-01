"""Compare two PaddleOCR-VL runs of the same volume.

Usage:
    python scripts/compare_paddle_versions.py <baseline_dir> <candidate_dir>

Reports the metrics that matter for this pipeline: how much text came out, how
the figure separation differs (the reason PaddleOCR was chosen), and where the
two texts actually diverge.
"""
from __future__ import annotations

import difflib
import json
import pathlib
import re
import sys
from collections import Counter


def load(d: pathlib.Path) -> dict:
    meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    meta["_text"] = (d / "text.md").read_text(encoding="utf-8")
    meta["_figs_md"] = (d / "figures.md").read_text(encoding="utf-8") if (d / "figures.md").exists() else ""
    meta["_fig_files"] = sorted(p.name for p in (d / "figures").glob("*")) if (d / "figures").is_dir() else []
    meta["_dir"] = d
    return meta


def fig_stats(meta: dict) -> tuple[Counter, int, int]:
    figs = meta.get("figures", [])
    labels = Counter(f.get("label") or "?" for f in figs)
    captioned = sum(1 for f in figs if f.get("caption"))
    pages = len({f.get("page") for f in figs})
    return labels, captioned, pages


def px_bytes(meta: dict) -> int:
    d = meta["_dir"] / "figures"
    return sum(p.stat().st_size for p in d.glob("*")) if d.is_dir() else 0


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    a, b = (load(pathlib.Path(x)) for x in sys.argv[1:3])

    print("=" * 72)
    print(f"BASELINE : {a['_dir']}  ({a.get('pipeline')})")
    print(f"CANDIDATE: {b['_dir']}  ({b.get('pipeline')})")
    print("=" * 72)

    rows = [
        ("pages",            a.get("page_count"),           b.get("page_count")),
        ("chars",            a.get("markdown_char_count"),  b.get("markdown_char_count")),
        ("figures",          a.get("figure_count"),         b.get("figure_count")),
        ("figure bytes",     px_bytes(a),                   px_bytes(b)),
        ("seconds",          a.get("processing_time_seconds"), b.get("processing_time_seconds")),
    ]
    print(f"\n{'metric':<16}{'baseline':>14}{'candidate':>14}{'delta':>14}")
    print("-" * 58)
    for name, x, y in rows:
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x:
            d = f"{(y - x) / x * 100:+.1f}%"
        else:
            d = "-"
        print(f"{name:<16}{x!s:>14}{y!s:>14}{d:>14}")

    for tag, m in (("baseline", a), ("candidate", b)):
        labels, captioned, pages = fig_stats(m)
        n = len(m.get("figures", []))
        print(f"\n{tag} figures: {n} across {pages} pages; "
              f"{captioned} captioned ({captioned / n * 100:.0f}%)" if n else
              f"\n{tag} figures: 0")
        if labels:
            print("  labels: " + ", ".join(f"{k}={v}" for k, v in labels.most_common()))

    # Per-page figure counts — where does the layout model disagree?
    pa = Counter(f.get("page") for f in a.get("figures", []))
    pb = Counter(f.get("page") for f in b.get("figures", []))
    diff_pages = sorted(p for p in set(pa) | set(pb) if pa[p] != pb[p])
    print(f"\npages where figure count differs: {len(diff_pages)}")
    for p in diff_pages[:25]:
        print(f"  p{p:>4}: baseline={pa[p]}  candidate={pb[p]}")
    if len(diff_pages) > 25:
        print(f"  … and {len(diff_pages) - 25} more")

    # Text divergence
    ta, tb = a["_text"], b["_text"]
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    print(f"\ntext similarity: {sm.quick_ratio():.4f} (quick), "
          f"headings {ta.count(chr(10) + '#')} vs {tb.count(chr(10) + '#')}")

    def counts(t: str) -> dict:
        return {
            "headings":   len(re.findall(r"^#{1,6} ", t, re.M)),
            "tables":     t.count("|---"),
            "img refs":   len(re.findall(r"!\[", t)),
            "blank runs": len(re.findall(r"\n{3,}", t)),
        }
    ca, cb = counts(ta), counts(tb)
    print(f"\n{'structure':<14}{'baseline':>12}{'candidate':>12}")
    print("-" * 38)
    for k in ca:
        print(f"{k:<14}{ca[k]:>12}{cb[k]:>12}")

    print("\nfirst 5 differing text blocks:")
    shown = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal" or shown >= 5:
            continue
        old, new = ta[i1:i2].strip(), tb[j1:j2].strip()
        if len(old) < 12 and len(new) < 12:
            continue
        print(f"\n  [{op}] @char {i1}")
        print(f"    - {old[:180]!r}")
        print(f"    + {new[:180]!r}")
        shown += 1


if __name__ == "__main__":
    main()
