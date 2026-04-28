# Agent Startup Guide

This file is the quick-start contract for any agent opening this wiki. Do not
use it as the full schema reference. The authoritative wiki schema and content
workflow live in [`CLAUDE.md`](./CLAUDE.md).

## Purpose

This repository is a persistent Markdown wiki for the *Flore du Gabon*
monographs plus article-derived addenda. The wiki is the synthesis layer over
immutable OCR sources in `../ocr_output/` (via the `sources/` symlink).

The ingest unit is the **family treatment**, not the volume.

## Startup Checklist

On every new session, do this before answering questions or editing pages:

1. Read `AGENTS.md` for the startup contract.
2. Read `index.md` first to understand what is already ingested.
3. Read `CLAUDE.md` before any non-trivial wiki edit, ingest, query, or lint.
4. Read the specific family/genus/species/volume pages relevant to the task.
5. If needed, confirm against primary source files under `sources/`.

## Source Priority

When locating a family treatment, prefer sources in this order:

1. `sources/<Family>_vol<NN>_paddle/`
2. `sources/<Family>_vol<NN>_liteparse/`
3. `sources/<Family>_vol<NN>_mineru/`
4. A virtual family slice from `sources/vol<NN>_paddle/` using
   `text_taxa.tsv`

For journal articles, use:

1. `sources/articles/<article_id>/liteparse/`
2. `sources/articles/<article_id>/paddle/`
3. `sources/articles/<article_id>/mineru/`

Within a source directory:

- Read `text_en.md` if present, otherwise `text.md` and translate inline.
- Read `figures.md`, `metadata.json`, and `text_taxa.tsv` when present.

## File Roles

- `CLAUDE.md`: authoritative schema, templates, and ingest/query/lint workflow
- `index.md`: flat catalog of current wiki contents; read this first for queries
- `log.md`: append-only record of ingest/query/lint activity
- `overview.md`: high-level rolling synthesis across the flora
- `families/`, `genera/`, `species/`, `volumes/`, `topics/`: wiki content
- `sources/`: symlink to immutable OCR output; never edit source files

## Operating Rules

- Preserve the existing schema and frontmatter fields from `CLAUDE.md`.
- Use English for wiki prose.
- Do not invent data. If the source is silent or contradictory, say so.
- Treat distribution as regional occurrence data by country/area, not as a
  special Gabon/non-Gabon status flag.
- Update existing pages when a taxon already exists; do not fork duplicate
  pages.
- Keep internal links in Obsidian wikilink format.
- Every meaningful ingest updates `index.md`, usually `log.md`, and
  `overview.md` when the high-level picture changes.
- Do not auto-fix lint issues unless explicitly asked.

## Task Routing

### If asked to ingest

Follow the ingest workflow in `CLAUDE.md` exactly. Author pages in this order:
species, genus, family, volume. Then update `index.md`, `overview.md` if
needed, and append a `log.md` entry.

### If asked a question

Read `index.md` first, then the relevant pages, then confirm in `sources/`
when needed. Cite wiki sections as `[[Page#Section]]` and primary source facts
as `(Vol NN, p. X)` where possible.

### If asked to lint

Follow the lint checklist in `CLAUDE.md`. Report findings briefly. Do not
auto-fix without confirmation.

## Maintenance

Keep `AGENTS.md` short and startup-focused. Put durable schema changes in
`CLAUDE.md`. If startup guidance and schema guidance diverge, update both
deliberately so future sessions do not inherit conflicting instructions.
