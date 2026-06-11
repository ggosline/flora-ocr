"""Per-flora configuration loader.

Each flora lives at floras/<name>/flora.toml and declares where its source PDFs
live, where OCR output goes, what the volume-filename pattern looks like, and
which language the source text is in.

Pipeline scripts call load_flora(name) and route all paths through the returned
config so the same scripts work for any flora.
"""
from __future__ import annotations

import re
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]  # Python 3.10 backport
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FLORAS_DIR = REPO_ROOT / "floras"


@dataclass
class Flora:
    name: str
    root: Path                 # floras/<name>/
    pdf_dir: Path              # where source PDFs live
    output_dir: Path           # where OCR output is written
    vol_pattern: re.Pattern    # named groups: 'label' (required)
    pdf_glob: str              # glob to enumerate source PDFs
    language: str              # ISO 639-1, e.g. "fr"
    title: str                 # human-readable name

    def discover_volumes(self) -> list[tuple[str, Path]]:
        """Return [(label, pdf_path)] sorted by label."""
        out: list[tuple[str, Path]] = []
        for p in self.pdf_dir.glob(self.pdf_glob):
            m = self.vol_pattern.match(p.name)
            if m:
                out.append((m.group("label"), p))
        out.sort(key=lambda x: _label_sort_key(x[0]))
        return out

    def output_for(self, *parts: str) -> Path:
        return self.output_dir.joinpath(*parts)


def _label_sort_key(label: str):
    m = re.match(r"(\d+)(bis)?", label)
    if not m:
        return (10**9, 0, label)
    return (int(m.group(1)), 1 if m.group(2) else 0, label)


def _resolve(base: Path, p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def load_flora(name: str) -> Flora:
    flora_dir = FLORAS_DIR / name
    toml_path = flora_dir / "flora.toml"
    if not toml_path.exists():
        raise FileNotFoundError(f"No flora config at {toml_path}")
    with toml_path.open("rb") as f:
        cfg = tomllib.load(f)

    return Flora(
        name=name,
        root=flora_dir,
        pdf_dir=_resolve(REPO_ROOT, cfg["pdf_dir"]),
        output_dir=_resolve(REPO_ROOT, cfg["output_dir"]),
        vol_pattern=re.compile(cfg["vol_pattern"]),
        pdf_glob=cfg.get("pdf_glob", "*.pdf"),
        language=cfg.get("language", "fr"),
        title=cfg.get("title", name),
    )


def add_flora_arg(parser):
    """Add a --flora argument with default 'flore_du_gabon' to an argparse parser."""
    parser.add_argument(
        "--flora",
        default="flore_du_gabon",
        help="Flora config name under floras/ (default: flore_du_gabon)",
    )
