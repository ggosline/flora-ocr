"""Populate the structured frontmatter fields the wiki schema defines.

`wiki/CLAUDE.md` specifies `countries`, `subdivisions`, `in_region`, `habit`,
`habitat`, `altitude_m` and `countries_incomplete`. The generated species pages
carry the source prose but none of those fields, so no Dataview query can see
them.

Everything here is read out of the page's own text. The schema is emphatic that
only a place the source itself names may be recorded, and that inferring a
province from a locality is fabrication -- it cites the Monts de Cristal error
by name. So:

  countries      recorded only where the text names the country
  subdivisions   recorded only where the text names the subdivision outright,
                 as these treatments usually do ("in Gabon, known from Nyanga
                 and Ogooue-Ivindo"). A locality never yields a province.
  countries_incomplete
                 set when a Distribution section exists but names no country,
                 so the gap is visible rather than silent

`habit`, `habitat` and `altitude_m` come from explicit statements in the
Description and Ecology sections and are left out when absent.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from flora_ocr.flora import REPO_ROOT

WIKI_DIR = REPO_ROOT / "wiki"

# The region, per wiki/CLAUDE.md. Western DRC and Cabinda only, but the source
# rarely says which part, so DRC and Angola count as in-region when named and
# the range note carries the detail.
IN_REGION = {
    "Nigeria", "Cameroon", "Equatorial Guinea", "Sao Tome and Principe",
    "Gabon", "Republic of the Congo", "Democratic Republic of the Congo",
    "Angola",
}

# surface form -> canonical country. Both languages, since a page may not have
# been translated yet, plus the historical names these floras use.
COUNTRIES = {
    "nigeria": "Nigeria", "nigéria": "Nigeria",
    "cameroon": "Cameroon", "cameroun": "Cameroon", "kamerun": "Cameroon",
    "gabon": "Gabon",
    "equatorial guinea": "Equatorial Guinea",
    "guinée équatoriale": "Equatorial Guinea",
    "rio muni": "Equatorial Guinea", "río muni": "Equatorial Guinea",
    "bioko": "Equatorial Guinea", "fernando po": "Equatorial Guinea",
    "annobon": "Equatorial Guinea", "annobón": "Equatorial Guinea",
    "sao tome": "Sao Tome and Principe", "são tomé": "Sao Tome and Principe",
    "principe": "Sao Tome and Principe", "príncipe": "Sao Tome and Principe",
    "republic of the congo": "Republic of the Congo",
    "congo-brazzaville": "Republic of the Congo",
    "democratic republic of the congo": "Democratic Republic of the Congo",
    "congo belge": "Democratic Republic of the Congo",
    "belgian congo": "Democratic Republic of the Congo",
    "zaire": "Democratic Republic of the Congo",
    "zaïre": "Democratic Republic of the Congo",
    "angola": "Angola", "cabinda": "Angola",
    # outside the region, recorded because ranges are kept in full
    "senegal": "Senegal", "sénégal": "Senegal",
    "gambia": "Gambia", "gambie": "Gambia",
    "guinea-bissau": "Guinea-Bissau", "guinea": "Guinea", "guinée": "Guinea",
    "sierra leone": "Sierra Leone",
    "liberia": "Liberia", "libéria": "Liberia",
    "ivory coast": "Cote d'Ivoire", "côte d'ivoire": "Cote d'Ivoire",
    "cote d'ivoire": "Cote d'Ivoire",
    "ghana": "Ghana", "togo": "Togo", "benin": "Benin", "bénin": "Benin",
    "burkina": "Burkina Faso", "mali": "Mali", "niger": "Niger",
    "chad": "Chad", "tchad": "Chad",
    "central african republic": "Central African Republic",
    "centrafrique": "Central African Republic",
    "sudan": "Sudan", "soudan": "Sudan",
    "uganda": "Uganda", "ouganda": "Uganda",
    "kenya": "Kenya", "tanzania": "Tanzania", "tanzanie": "Tanzania",
    "rwanda": "Rwanda", "burundi": "Burundi",
    "zambia": "Zambia", "zambie": "Zambia",
    "zimbabwe": "Zimbabwe", "mozambique": "Mozambique",
    "malawi": "Malawi", "madagascar": "Madagascar",
}

# "Congo" unqualified is ambiguous in these floras and is deliberately not
# mapped: the Republic and the DRC are both called that, and guessing would put
# a wrong country on the page.

SUBDIVISIONS = {
    "Gabon": {
        "Estuaire", "Haut-Ogooue", "Haut-Ogooué", "Moyen-Ogooue",
        "Moyen-Ogooué", "Ngounie", "Ngounié", "Nyanga", "Ogooue-Ivindo",
        "Ogooué-Ivindo", "Ogooue-Lolo", "Ogooué-Lolo", "Ogooue-Maritime",
        "Ogooué-Maritime", "Woleu-Ntem",
    },
}

HABITS = [
    (r"\b(liana|lianas|liane|lianes|climber|scandent)\b", "liana"),
    (r"\b(tree|trees|arbre|arbres)\b", "tree"),
    (r"\b(shrub|shrubs|arbuste|arbustes|arbrisseau)\b", "shrub"),
    (r"\b(herb|herbs|herbe|herbes|herbaceous)\b", "herb"),
    (r"\b(epiphyte|epiphytic|épiphyte)\b", "epiphyte"),
    (r"\b(fern|fougère)\b", "fern"),
]

HABITATS = [
    (r"\b(mangrove|mangroves)\b", "mangrove"),
    (r"\b(gallery forest|forêt galerie|galeries forestières)\b", "gallery forest"),
    (r"\b(swamp|marsh|marécage|marécageux|inondé)\b", "swamp forest"),
    (r"\b(savanna|savannah|savane)\b", "savanna"),
    (r"\b(secondary forest|forêt secondaire|secondarized)\b", "secondary forest"),
    (r"\b(primary forest|forêt primaire)\b", "primary forest"),
    (r"\b(riverine|riparian|ripicole|bord de rivière)\b", "riverine"),
    (r"\b(coastal|littoral|bord de mer)\b", "coastal"),
    (r"\b(rain ?forest|evergreen forest|forêt dense|sempervirente)\b",
     "evergreen forest"),
    (r"\b(inselberg|rocky|rochers|rocheux)\b", "rocky ground"),
]

ALTITUDE_RE = re.compile(
    r"(\d{1,4})\s*(?:[-–—]\s*(\d{1,4}))?\s*m\b[^.;]{0,30}"
    r"(?:altitude|alt\.|a\.s\.l|above sea)", re.I)
ALT_PREFIX_RE = re.compile(
    r"(?:at|à|vers|jusqu'à|up to)\s+(\d{1,4})(?:\s*[-–—]\s*(\d{1,4}))?\s*m\b", re.I)

SECTION_RE = re.compile(r"^## (.+?)\n(.*?)(?=^## |\Z)", re.M | re.S)


def sections(text: str) -> dict[str, str]:
    return {m.group(1).strip(): m.group(2).strip()
            for m in SECTION_RE.finditer(text)}


def find_countries(text: str) -> list[str]:
    low = text.lower()
    found = set()
    for surface, canonical in COUNTRIES.items():
        for m in re.finditer(rf"\b{re.escape(surface)}\b", low):
            # "republic of the congo" is a substring of "democratic republic of
            # the congo", and "guinea" of "equatorial guinea" and
            # "guinea-bissau": a match preceded or followed by a qualifier
            # belongs to the longer name.
            before = low[max(0, m.start() - 14):m.start()]
            after = low[m.end():m.end() + 8]
            if re.search(r"(democratic|equatorial|papua)\s*$", before):
                continue
            if surface == "guinea" and after.startswith("-bissau"):
                continue
            found.add(canonical)
    return sorted(found)


def find_subdivisions(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for country, names in SUBDIVISIONS.items():
        hit = sorted({n for n in names if re.search(rf"\b{re.escape(n)}\b", text)})
        if hit:
            # collapse accented/unaccented duplicates, preferring the accented
            seen: dict[str, str] = {}
            for name in hit:
                key = (name.replace("é", "e").replace("ó", "o").lower())
                if key not in seen or "é" in name:
                    seen[key] = name
            out[country] = sorted(seen.values())
    return out


def first_match(patterns, text: str) -> str | None:
    for pattern, label in patterns:
        if re.search(pattern, text, re.I):
            return label
    return None


def all_matches(patterns, text: str) -> list[str]:
    return [label for pattern, label in patterns
            if re.search(pattern, text, re.I)]


def find_altitude(text: str) -> str | None:
    for regex in (ALTITUDE_RE, ALT_PREFIX_RE):
        m = regex.search(text)
        if m:
            lo, hi = m.group(1), m.group(2)
            return f"{lo}–{hi}" if hi else lo
    return None


def fields_for(text: str) -> dict:
    sec = sections(text)
    distribution = sec.get("Distribution", "")
    ecology = " ".join(filter(None, [sec.get("Ecology", ""),
                                     sec.get("Discussion", "")]))
    description = sec.get("Description", "")

    out: dict = {}
    countries = find_countries(distribution or ecology)
    if countries:
        out["countries"] = countries
        out["in_region"] = any(c in IN_REGION for c in countries)
    elif distribution:
        out["countries_incomplete"] = True

    subs = find_subdivisions(distribution)
    if subs:
        out["subdivisions"] = subs

    habit = first_match(HABITS, description[:400])
    if habit:
        out["habit"] = habit
    habitat = all_matches(HABITATS, ecology)
    if habitat:
        out["habitat"] = habitat
    altitude = find_altitude(ecology)
    if altitude:
        out["altitude_m"] = altitude
    return out


def render_fields(fields: dict) -> list[str]:
    lines = []
    for key, value in fields.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, list):
            lines.append(f"{key}: [{', '.join(value)}]")
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for country, names in value.items():
                lines.append(f"  {country}: [{', '.join(names)}]")
        else:
            lines.append(f'{key}: "{value}"')
    return lines


MANAGED = ("countries", "subdivisions", "in_region", "countries_incomplete",
           "habit", "habitat", "altitude_m")


def apply_to(text: str) -> str | None:
    if not text.startswith("---") or "\n---" not in text[3:]:
        return None                     # no closing frontmatter: leave it alone
    end = text.index("\n---", 3)
    front, body = text[4:end], text[end:]
    fields = fields_for(body)
    if not fields:
        return None

    kept, skip = [], False
    for line in front.split("\n"):
        if skip and line.startswith(("  ", "\t")):
            continue
        skip = False
        if line.split(":")[0].strip() in MANAGED:
            skip = True
            continue
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()

    # insert before tags, which the schema keeps last
    at = next((i for i, l in enumerate(kept) if l.startswith("tags:")), len(kept))
    merged = kept[:at] + render_fields(fields) + kept[at:]
    return "---\n" + "\n".join(merged) + body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(WIKI_DIR / "species"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sample", type=int, default=0)
    args = ap.parse_args(argv)

    stats = {k: 0 for k in MANAGED}
    changed = 0
    for path in sorted(Path(args.dir).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        updated = apply_to(text)
        if updated is None or updated == text:
            continue
        for key in MANAGED:
            if re.search(rf"^{key}:", updated, re.M):
                stats[key] += 1
        changed += 1
        if args.sample and changed <= args.sample:
            print(f"--- {path.name}\n" +
                  "\n".join(render_fields(fields_for(text))))
        if not args.dry_run:
            path.write_text(updated, encoding="utf-8")

    verb = "would update" if args.dry_run else "updated"
    print(f"{verb} {changed} pages")
    for key, n in stats.items():
        print(f"  {key:22s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
