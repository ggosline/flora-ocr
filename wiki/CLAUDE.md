# Flore du Gabon Wiki — Schema and Workflow

This file is the working contract between the LLM and the wiki. Read it before
ingesting a source, before answering a query, before linting, before any
non-trivial wiki edit. Update it when conventions evolve — but keep edits
deliberate; the schema is what makes the wiki coherent across sessions.

## What this wiki is

A persistent, compounding knowledge base built from the **Flore du Gabon**
monographs (61 volumes, 1961–present) plus later journal articles and other
botanical papers relevant to Gabon, maintained by the LLM in collaboration
with the user. Sources live in `../ocr_output/` (immutable). The wiki is the
synthesis layer: one page per taxonomic entity, cross-linked, accumulating as
new family treatments and article addenda are ingested.

The wiki is **family-treatment-centric**, not volume-centric. A volume is
editorial packaging; a family treatment is the atomic published unit.

## Three layers

1. **Raw sources** — `../ocr_output/<DIR>/` (read via `sources/` symlink).
   Two shapes:
   - `Family_volNN_<engine>/` — already split by family (preferred).
   - `volNN_<engine>/` — whole volume, split virtually at ingest using
     `text_taxa.tsv` line ranges.
   - `articles/<article_id>/<engine>/` — one article OCR bundle, usually not split
     by family ahead of time.
   Engines: `paddle`, `liteparse`, `mineru`, `marker`. Prefer in that order
   when multiple exist for the same vol/family.
2. **Wiki** — this directory. Markdown files only. The LLM owns this layer
   end-to-end: it creates, updates, refactors, and lints. The user reads.
3. **Schema** — this file (`CLAUDE.md`). Co-evolved with the user.

## Directory layout

```
wiki/
  CLAUDE.md              # this file
  index.md               # content catalog (read FIRST when answering queries)
  log.md                 # append-only ingest/query/lint log
  overview.md            # rolling synthesis of Flore du Gabon as a whole
  sources -> ../ocr_output   # symlink — never edit through this
  families/<Family>.md       # one page per botanical family
  genera/<Genus>.md          # one page per genus
  species/<Genus>_<species>.md   # one page per species (underscored, ASCII)
  volumes/vol<NN>.md         # thin per-volume index page
  topics/<slug>.md           # cross-cutting concepts (endemism, forest types,
                             #   morphology glossary, collectors, etc.)
```

## Naming conventions

- **Family pages**: `families/Myrtaceae.md`. Page title in CapCase, no accents
  even if the source uses MYRTACÉES (note alternate name in body).
- **Genus pages**: `genera/Psidium.md`. Capitalized, no authority in filename.
- **Species pages**: `species/Psidium_guajava.md`. `Genus_species`, ASCII,
  underscore separator. Infraspecific taxa get their own page only if the
  treatment gives them a distinct description; otherwise note inline on the
  species page.
- **Volume pages**: `volumes/vol11.md`, `volumes/vol5bis.md`.
- **Topic pages**: lowercase-slug, e.g. `topics/endemics_of_gabon.md`.

When a name in the source is uncertain or has a typo (common in scanned vols),
use the corrected modern form for the filename and note the OCR/source spelling
in the body.

## YAML frontmatter

Every wiki page starts with frontmatter. Fields by page type:

**Family**
```yaml
---
type: family
name: Ancistrocladaceae
authority: Planch. ex Walp.
order: Caryophyllales
genera_in_gabon: 1
species_in_gabon: 4
treatments:
  - vol: 60
    year: 2022
    authors: [Gereau R.E., Walters G.M.]
    pages: "1–8"
    source: sources/Ancistrocladaceae_vol60_liteparse
tags: [family]
---
```

**Genus**
```yaml
---
type: genus
name: Ancistrocladus
authority: Wall.
family: Ancistrocladaceae
species_in_gabon: 4
treatments:
  - vol: 60
    source: sources/Ancistrocladaceae_vol60_liteparse
tags: [genus]
---
```

**Species**
```yaml
---
type: species
name: Ancistrocladus congolensis
authority: J.Léonard
genus: Ancistrocladus
family: Ancistrocladaceae
synonyms: []
distribution_gabon: [Ngounié, Ogooué-Maritime, Ogooué-Ivindo]
distribution_other: [Republic of the Congo, DRC]
habit: liana
habitat: [riverine forest, terra firma forest, hilltop forest]
altitude_m: "1–480"
treatments:
  - vol: 60
    pages: "10–11"
    source: sources/Ancistrocladaceae_vol60_liteparse
tags: [species]
---
```

Distribution should be stored as occurrence data, not as a Gabon/non-Gabon
status class. Keep using:

- `distribution_gabon`: provinces or subnational areas within Gabon
- `distribution_other`: countries or subnational areas outside Gabon

Do not rely on a special `not-in-gabon` tag for routine country absence. If a
species is absent from Gabon, represent that by leaving `distribution_gabon`
empty and describing the known range in prose.

**Volume**
```yaml
---
type: volume
vol: 60
year: 2022
editors: [Sosef M.S.M., Florence J., Bourobou Bourobou H.P., Bissiengou P.]
publisher: Margraf Publishers, Weikersheim
doi: 10.5281/zenodo.14900487
families: [Ancistrocladaceae, Dilleniaceae, Menispermaceae, Ranunculaceae]
tags: [volume]
---
```

Frontmatter is consumed by Obsidian Dataview. Keep field names stable; add new
fields by appending, not renaming.

For article-derived treatments, keep `source` mandatory and append article
metadata inside each `treatments` item instead of inventing a new page type.
Use fields such as:

```yaml
treatments:
  - source: sources/articles/adansonia_1991_example/liteparse
    kind: article
    citation: "Author A. & Author B. (1991). Title. Journal 12: 34-56."
    year: 1991
    pages: "34–56"
```

If a taxon already has a volume treatment, append the article treatment to the
same page's `treatments` list and update the body cumulatively rather than
forking a separate article page.

## Linking

Use Obsidian wiki-link style for internal references: `[[Ancistrocladus]]`,
`[[Ancistrocladus_congolensis|A. congolensis]]`. This makes the graph view
useful and survives file renames (Obsidian rewrites).

For figures, use markdown image syntax with a path through the `sources/`
symlink so embeds resolve in Obsidian without copying files:

```
![Planche 1 — A. congolensis & A. ealaensis](../sources/Ancistrocladaceae_vol60_liteparse/figures/fig_025_p0011.png)
```

Always include a caption (translated/synthesized from `figures.md` if a real
caption exists, otherwise descriptive). Figures with no botanical content (cover
art, mastheads, decorative scrolls) should not be linked at all.

## Page templates

### Family page

```markdown
---
{frontmatter}
---

# {Family}

**Authority**: {authority}
**Order**: {order} (APG IV placement)
**Common name (FR)**: {if present in source}

## Diagnosis

{2–4 sentence morphological summary, translated/synthesized from the source's
family description. Habit, leaves, inflorescence, flowers, fruits, distinguishing
features.}

## Distribution

{Global range, then narrowed to Africa and Gabon. Number of genera and species
in Gabon.}

## Genera in Gabon

| Genus | Species in Gabon | Treatment |
|-------|------------------|-----------|
| [[Ancistrocladus]] | 4 | [[#Vol 60 (2022)]] |

## Treatments

### Vol {NN} ({year})

**Authors**: {authors}
**Pages**: {range}
**Source**: `sources/{dir}`

{One paragraph summarising the treatment's scope, any taxonomic novelties,
notable methodology. Link to the figures section if relevant.}

## Notes

{Phylogenetic placement, recent taxonomic changes, anything noteworthy that
spans the whole family. Update across treatments — this is a *cumulative*
section.}

## See also

- [[volNN]]
- Related families: [[Dioncophyllaceae]] (if cross-referenced in source)
```

### Genus page

```markdown
---
{frontmatter}
---

# {Genus}

**Authority**: {authority}, {protologue citation if available}
**Family**: [[{Family}]]

## Diagnosis

{Genus-level morphological description, 1–3 paragraphs. Translated/synthesized
from the source.}

## Species in Gabon

| Species | Habit | Distribution (Gabon) | Page |
|---------|-------|----------------------|------|
| [[Ancistrocladus_congolensis\|A. congolensis]] | liana | Ngounié, Og-Maritime, Og-Ivindo | 10 |

## Key

{If the source provides a dichotomous key for the genus, include it here as an
indented markdown list, with leaf items linking to the species pages. Translate
French key text into English.}

## Treatments

{Same shape as family page — one block per volume the genus appears in.}

## Notes
```

### Species page

```markdown
---
{frontmatter}
---

# *{Genus species}* {authority}

**Family**: [[{Family}]] · **Genus**: [[{Genus}]]

{Protologue citation: e.g. "Bull. Soc. Roy. Bot. Belgique 82: 33 (1949)."}

## Synonymy

- *{synonym}* {author}, {citation}

(Omit section if none.)

## Description

{Full morphological description translated from source. Preserve all
measurements, keep Latin terms italicized. This is the heart of the page —
fidelity matters more than concision.}

**Habit**: {liana / shrub / tree / etc., with size}
**Leaves**: ...
**Inflorescence**: ...
**Flowers**: ...
**Fruit**: ...
**Seed**: ...

## Distribution

**Range**: {global/regional}.
**Gabon**: {provinces}.

## Habitat and ecology

{Forest type, soils, altitude, associated species if mentioned.}

## Vernacular names

{If recorded — language, name. Often absent.}

## Figures

![Caption](../sources/.../figures/fig_NNN_pPPPP.png)

## Source

{Citation to the specific treatment. Copy the suggested citation format from
the volume's front matter.}

## Notes

{Anything the LLM noticed worth flagging: synonymy disputes, distribution
updates from later treatments, missing data, source ambiguities.}
```

### Volume page (thin)

```markdown
---
{frontmatter}
---

# Flore du Gabon — Volume {NN} ({year})

**Editors**: {editors}
**Publisher**: {publisher}
**DOI**: {doi}

## Suggested citation

> {full citation from front matter}

## Families treated

- [[Ancistrocladaceae]] (pp. 1–8) — Gereau & Walters
- [[Dilleniaceae]] (pp. 9–20) — ...
- [[Menispermaceae]] (pp. 21–84) — ...
- [[Ranunculaceae]] (pp. 85–91) — ...

## Source

`sources/{first ingest dir}/...`

## Ingest log

- {date} — {family} ingested
```

## index.md format

`index.md` is a flat catalog. Sections: Families, Genera, Species, Volumes,
Topics. Each entry one line:

```
- [[Ancistrocladaceae]] — monogeneric, 4 species in Gabon (Vol 60)
```

Updated on every ingest. The LLM reads `index.md` first when answering queries
to find candidate pages, then drills into them.

## log.md format

Append-only. One entry per ingest, query, or lint pass. Date in ISO format.

```
## [2026-04-08] ingest | Ancistrocladaceae (Vol 60)

Source: sources/Ancistrocladaceae_vol60_liteparse (liteparse, 21 KB, 15 pages)
Created: families/Ancistrocladaceae.md, genera/Ancistrocladus.md,
  species/Ancistrocladus_congolensis.md, species/Ancistrocladus_ealaensis.md,
  species/Ancistrocladus_guineensis.md, species/Ancistrocladus_letestui.md,
  volumes/vol60.md
Updated: index.md, overview.md
Notes: text_en.md absent — translated inline from text.md.
```

Use `## [` as the entry prefix so `grep "^## \[" log.md | tail -10` works.

## Ingest workflow (per family treatment)

Run this when the user says "ingest <Family> from vol <N>" or similar.

1. **Locate the source.** Prefer in order:
   `sources/<Family>_vol<NN>_paddle/` →
   `sources/<Family>_vol<NN>_liteparse/` →
   `sources/<Family>_vol<NN>_mineru/` →
   virtual slice of `sources/vol<NN>_paddle/text_en.md` using `text_taxa.tsv`
   line ranges (`grep "^family\s" text_taxa.tsv` → find adjacent family lines).

2. **Read the source.**
   - `text_en.md` if present, else `text.md` (translate inline).
   - `figures.md` (figure inventory).
   - `metadata.json` (engine, page count, etc.).
   - `text_taxa.tsv` if present (taxa index).

3. **Identify scope.** List the family, all genera, all species and
   infraspecifics. Cross-check against `text_taxa.tsv` if available.

4. **Check for existing pages.** A family or genus may already exist from an
   earlier ingest. If so, *update* — add a new treatment block, not a new file.
   Species pages are usually new but may need merging if a synonym was previously
   filed under a different name.

5. **Filter figures.** Skip cover/decorative figures (typically pages 1–7 of
   modern Margraf volumes). Keep planches, line drawings, photos that
   illustrate species. Each figure must end up on at least one species page.

6. **Author pages** in this order: species → genus → family → volume.
   Bottom-up makes the cross-link tables on the upper pages accurate.

7. **Update index.md.** Add new entries under the appropriate sections.

8. **Update overview.md.** Only if the new ingest changes the high-level
   picture (e.g. adds a new order, increases the species count materially,
   reveals a contradiction with earlier ingests).

9. **Append to log.md.**

10. **Self-check.** Read the new pages back. Verify: every species linked from
    the genus exists; every figure path resolves; frontmatter validates;
    no orphan pages.

## Query workflow

When the user asks a question:

1. Read `index.md`. Identify candidate pages.
2. Read those pages.
3. If the answer is non-obvious or spans multiple pages, also grep the
   relevant `sources/.../text_en.md` files for primary-source confirmation.
4. Answer with citations: `[[Page#Section]]` for wiki refs, `(Vol NN, p. X)`
   for source refs.
5. **If the answer is novel synthesis** (a comparison, a list, an analysis
   that didn't exist before) and is reusable, file it back as a new page —
   typically under `topics/`. Note in `log.md`.

## Lint workflow

When the user says "lint the wiki":

- Orphan pages: pages with zero inbound wiki-links. List them.
- Dead links: `[[X]]` where `X.md` does not exist. List them.
- Frontmatter validation: missing/malformed required fields.
- Stale claims: pages that mention "X species in Gabon" where the count is now
  wrong because a new treatment was ingested.
- Taxa in some `text_taxa.tsv` but missing a wiki page.
- Suggest follow-ups: families with treatments not yet ingested, topics worth
  spinning out.

Output a short report. Do NOT auto-fix without confirmation.

## Conventions and preferences

- **Language**: English. Keep the original French name in italics on the
  family page if it differs significantly from the modern form (`MYRTACÉES`
  → note as alternate).
- **Measurements**: keep source units (mm, cm, m). Don't convert.
- **Authorities**: preserve exactly as printed (`(Engl.) Mildbr.`, `J.Léonard`).
  Don't standardize spacing or punctuation.
- **Translation fidelity**: botanical descriptions must round-trip — a reader
  comparing the wiki page to the French source should find every measurement,
  every character, every locality. Synthesis is fine on family/genus diagnosis
  pages and on overview/topic pages.
- **Comments in markdown**: use HTML comments `<!-- ... -->` to flag
  uncertainty or LLM notes that should be reviewed by the user.
- **No emojis** in any wiki page.
- **Don't invent data.** If the source doesn't say it, don't say it. If two
  sources contradict, surface the contradiction in a Notes section.

## Things this schema does NOT yet handle (known gaps)

- Family-split translation: `translate.py` is volume-scoped. For now, when
  ingesting a family-split source whose `text_en.md` is missing, translate
  inline. Revisit when there are >5 such cases.
- Image OCR for plates with embedded labels (planche numbers, scale bars) —
  no OCR is run on figures themselves.
- A search engine (qmd or similar) — `index.md` is sufficient for now.
- Patches: the `patches_volNN.json` mechanism is for the key-builder app, not
  the wiki. If a wiki page contradicts a patch, flag it but don't auto-apply.
