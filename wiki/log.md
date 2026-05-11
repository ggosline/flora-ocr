# Log

Append-only chronological record of wiki activity. One entry per ingest, query,
or lint pass. Entry prefix `## [YYYY-MM-DD]` so the log is grep-friendly:

```
grep "^## \[" log.md | tail -10
```

See [[CLAUDE#log.md format]] for the entry shape.

---

## [2026-04-08] init | wiki scaffolded

Created the wiki tree under `/mnt/e/FloreDuGabon/wiki/`:

- `CLAUDE.md` — schema and workflow
- `index.md`, `log.md`, `overview.md` — seed files
- `families/`, `genera/`, `species/`, `volumes/`, `topics/` — empty dirs
- `sources -> ../ocr_output` — symlink to immutable raw OCR output

Decision: ingest unit is the **family treatment**, not the volume. A volume is
editorial packaging; a family treatment is the atomic published work and
matches the wiki's page granularity 1:1.

Decision: when `text_en.md` is absent for a family-split source, translate
inline during page authoring. The first such case is Ancistrocladaceae vol 60.

## [2026-04-08] ingest | Ancistrocladaceae (Vol 60)

Source: `sources/Ancistrocladaceae_vol60_liteparse` (liteparse, 21 167 chars,
15 PDF pages, 32 figures detected). Authors: Gereau R.E. & Walters G.M.
Translated inline from `text.md` (no `text_en.md` was generated for this
family-split directory).

Created:
- `families/Ancistrocladaceae.md`
- `genera/Ancistrocladus.md`
- `species/Ancistrocladus_congolensis.md`
- `species/Ancistrocladus_ealaensis.md`
- `species/Ancistrocladus_guineensis.md`
- `species/Ancistrocladus_letestui.md`
- `volumes/vol60.md`

Updated: `index.md`, `overview.md`.

Figures: of 32 detected, 29 are cover/decorative (figs 0–24, pages 2–8 of the
PDF) and were skipped. The botanically meaningful figures used are:
- `fig_025_p0011.png` — Planche 1 (line drawing): *A. congolensis* +
  *A. ealaensis*. Linked from both species pages.
- `fig_026_p0012.jpeg` — Figure 1 (composite photo plate by Harris and
  Bidault): *A. congolensis* (A, B) + *A. ealaensis* (C–E). Linked from both
  species pages with the relevant panel labels in the caption. Figs 27–30 on
  the same page appear to be the individual panels of fig 26 separated out by
  the extractor; they are not linked separately to avoid duplication.
- `fig_031_p0014.jpeg` — Planche 2 (line drawing by Trevithick from Hutchinson,
  Dalziel & Keay 1954): *A. guineensis*. Linked from the *A. guineensis* page.
- *A. letestui* has no figure in the source treatment.

Notes:
- The source's bibliography on the family and genus pages is OCR-mangled
  (`Bi : ...`, `B liographie` artefacts from running text through a small-caps
  header). The translated wiki uses a clean "Bibliography (cited)" section
  with the correct reference list.
- The published page range for the family treatment (1–8 in vol 60 per the
  table of contents) does not match the PDF page count of the family-split
  source (which starts at PDF p. 1). The wiki's `pages` frontmatter and the
  vol60 page table use the **published** range; the species `pages` field on
  each species page uses the relative page within the published treatment.
- TODO: when the Dilleniaceae family from vol 60 is treated, link
  *Dioncophyllaceae* on the Ancistrocladaceae notes block (currently a
  placeholder comment) — they are sister families in Caryophyllales.

## [2026-04-08] ingest | Dilleniaceae (Vol 60)

Source: `sources/Dilleniaceae_vol60_liteparse` (liteparse, 27 157 chars,
12 PDF pages, 17 figures detected). Authors: Niangadouma R., Lachenaud O. &
Sosef M.S.M. Translated inline from `text.md`.

Created:
- `families/Dilleniaceae.md`
- `genera/Tetracera.md`
- `species/Tetracera_alnifolia.md`
- `species/Tetracera_breteleri.md` — **spec. nov.** described in this
  treatment, type from Ogooué-Lolo, Gabon
- `species/Tetracera_podotricha.md`
- `species/Tetracera_poggei.md`
- `species/Tetracera_rosiflora.md`

Updated: `volumes/vol60.md`, `index.md`, `overview.md`.

Figures: of 17 detected, 2 (figs 32–33, p.16) are cover/decorative and were
skipped. The botanically meaningful figures used are:
- `fig_034_p0020.jpeg` — Figure 2 (composite photo plate by E. Bidault):
  *T. alnifolia* (A–D) + *T. podotricha* (E–G). Linked from both species
  pages with the relevant panel labels in the caption. Figs 35–40 on the same
  page appear to be the individual panels split out by the extractor; not
  linked separately to avoid duplication.
- `fig_041_p0021.jpeg` — Planche 3 (line drawing by Trevithick from Hutchinson
  et al. 1954): *T. alnifolia* subsp. *alnifolia*. Linked from the
  *T. alnifolia* page.
- `fig_042_p0024.jpeg` — Figure 3 (composite photo plate): *T. poggei* (A,B)
  + *T. rosiflora* (C) + *T. breteleri* (D,E). Linked from all three species
  pages with the relevant panel labels.
- `fig_047_p0025.png` — Planche 4 (line drawing by d'Apreval from De Wildeman
  & Durand 1899): *T. poggei*. Linked from the *T. poggei* page.
- `fig_048_p0027.jpeg` — Planche 5 (line drawing by Coppin from Boutique 1967):
  *T. rosiflora*. Linked from the *T. rosiflora* page.

Notes:
- *Tetracera breteleri* is a **type-locality-Gabon novelty** described in
  this treatment. Tagged with `type-locality-gabon` and `novelty` for future
  topic queries.
- The key includes two species (*T. masuiana*, *T. potatoria*) that are not
  currently known from Gabon; these are listed in the genus page's
  "Species to be sought in Gabon" section but do not have their own species
  pages — they enter the wiki only if/when a Gabonese collection is found.
- The *T. alnifolia* / *T. podotricha* complex was previously confused; the
  treatment re-circumscribes them. Cross-references between the two species
  pages flag this so a future query about the complex can find both.

## [2026-04-08] ingest | Ranunculaceae (Vol 60)

Source: `sources/Ranunculaceae_vol60_liteparse` (liteparse, 36 KB, 16 PDF
pages, ~10 figures detected). Author: Erik L.A.N. Simons (Naturalis
Biodiversity Center, Leiden). Translated inline from `text.md`.

Created:
- `families/Ranunculaceae.md`
- `genera/Clematis.md`
- `species/Clematis_grandiflora.md`
- `species/Clematis_hirsuta.md`

Updated: `volumes/vol60.md`, `index.md`, `overview.md`.

Figures used:
- `fig_096_p0094.jpeg` — Figure 7 (composite photo plate by Carel Jongkind,
  Allan Holmes, Matthew Walters and Stefan Porembski). Panels A–C illustrate
  *C. grandiflora*; panels D–F illustrate *C. hirsuta*. Linked from both
  species pages with the relevant panel labels in the caption. Figs 97–101
  on the same page appear to be the individual panels split out by the
  extractor; not linked separately to avoid duplication.
- `fig_102_p0095.jpeg` — Planche 34 (line drawings by Wil Wessel-Brand and
  Dominic Troupin, after van der Maesen 2006 and Troupin 1978). Linked from
  the *C. hirsuta* page only.
- Figs 94–95 (p.90) are decorative/cover art for the family treatment;
  skipped. Figures after 102 are Vol 60 back matter and were not considered.

Notes:
- Ranunculaceae is the **last** family in Vol 60, so the source `text.md`
  continues past the family treatment proper into the volume's back matter
  (acknowledgments, full bibliography, etc.). Only the family pages
  (vol 60 pp. 86–91) were used.
- Both Gabonese *Clematis* species are **lianas** with **opposite, compound
  leaves** and **petaloid sepals lacking petals** — atypical for a family that
  is mostly herbaceous worldwide with alternate leaves and well-developed
  petals. This is flagged on both [[Ranunculaceae#Notes]] and
  [[Clematis#Notes]] so future queries about the family character set in
  Gabon find the warning.
- The "*Clematis brachiata* group" synonymy debate (Lebrun & Stork 2003 vs.
  Wang 2000/2004) is recorded on [[Clematis_hirsuta#Notes]]; the Vol 60
  treatment follows Wang and rejects the broad lumping.
- With Ranunculaceae done, **3 / 4 Vol 60 families are ingested**. Only
  Menispermaceae remains for a complete Vol 60 ingest.

## [2026-04-08] ingest | Menispermaceae (Vol 60)

Source: `sources/Menispermaceae_vol60_liteparse` (liteparse, 110 KB,
62 PDF pages, 45 figures detected). Author: Frans J. Breteler (Wageningen).
Pages 29–89 in the published volume. Translated inline from `text.md`.

Created (1 family + 22 genera + 36 species = **59 new pages**):

Family: `families/Menispermaceae.md`

Genera (22): `genera/Albertisia.md`, `genera/Anisocycla.md`,
`genera/Beirnaertia.md`, `genera/Chasmanthera.md`, `genera/Cissampelos.md`,
`genera/Dialytheca.md`, `genera/Dioscoreophyllum.md`, `genera/Jateorhiza.md`,
`genera/Kolobopetalum.md`, `genera/Leptoterantha.md`, `genera/Limaciopsis.md`,
`genera/Penianthus.md`, `genera/Perichasma.md`, `genera/Rhigiocarya.md`,
`genera/Sarcolophium.md`, `genera/Stephania.md`, `genera/Synclisia.md`,
`genera/Syntriandrium.md`, `genera/Syrrheonema.md`, `genera/Tiliacora.md`,
`genera/Tinospora.md`, `genera/Triclisia.md`.

Species (36): `species/Albertisia_badia.md`,
`species/Albertisia_mouilaensis.md`, `species/Albertisia_porcata.md`,
`species/Albertisia_sp_nov.md`, `species/Anisocycla_jollyana.md`,
`species/Beirnaertia_cabindensis.md`, `species/Chasmanthera_dependens.md`,
`species/Cissampelos_owariensis.md`, `species/Dialytheca_gossweileri.md`,
`species/Dioscoreophyllum_gossweileri.md`,
`species/Dioscoreophyllum_volkensii.md`, `species/Jateorhiza_macrantha.md`,
`species/Kolobopetalum_auriculatum.md`, `species/Kolobopetalum_ovatum.md`,
`species/Kolobopetalum_spec_nov.md`, `species/Kolobopetalum_synsepalum.md`,
`species/Leptoterantha_mayumbensis.md`, `species/Limaciopsis_loangensis.md`,
`species/Penianthus_longifolius.md`, `species/Perichasma_laetificata.md`,
`species/Rhigiocarya_racemifera.md`, `species/Sarcolophium_suberosum.md`,
`species/Stephania_dinklagei.md`, `species/Synclisia_oligogyna.md`,
`species/Synclisia_scabrida.md`, `species/Syntriandrium_preussii.md`,
`species/Syrrheonema_fasciculatum.md`, `species/Tiliacora_gabonensis.md`,
`species/Tiliacora_klaineana.md`, `species/Tiliacora_macrophylla.md`,
`species/Tinospora_penninervifolia.md`, `species/Triclisia_dictyophylla.md`,
`species/Triclisia_gabonensis.md`, `species/Triclisia_hypochrysea.md`,
`species/Triclisia_megacarpa.md`, `species/Triclisia_riparia.md`.

Updated: `volumes/vol60.md`, `index.md`, `overview.md`.

Highlights:
- **5 species described as new** in this treatment (Breteler, sometimes with
  Jongkind): [[Albertisia_badia]] (type Ogooué-Lolo),
  [[Albertisia_mouilaensis]] (type Ngounié), [[Synclisia_oligogyna]] (type
  Woleu-Ntem), [[Triclisia_gabonensis]] (type Haut-Ogooué), and
  [[Triclisia_megacarpa]] (type Ngounié). All 5 type localities are in Gabon.
- **2 undescribed entities** documented but not formally named:
  [[Albertisia_sp_nov]] (sterile, Ogooué-Lolo) and
  [[Kolobopetalum_spec_nov]] (sterile, Moyen-Ogooué — alternatively
  *Rhigiocarya*).
- **Notable taxonomic acts**: synonymy of *Chasmanthera welwitschii* under
  *C. dependens*; synonymy of *Tiliacora odorata* and *T. ovalis* under
  [[Tiliacora_klaineana]]; reinstatement of [[Triclisia_dictyophylla]] in a
  wider sense (against Jongkind 2017) coupled with description of the new
  *T. megacarpa*.
- **Several species with unknown female (or male) flowers**: flagged on
  [[Dialytheca_gossweileri]], [[Syrrheonema_fasciculatum]],
  [[Tiliacora_gabonensis]], [[Triclisia_riparia]] (♂ unknown), and others.
  Breteler notes "about ten species remain poorly known."
- **Habit**: of the 36 species, **34 are climbers** (woody lianas or twining
  herbs) and **only 2 are non-climbing**: [[Anisocycla_jollyana]] (a 30–70 cm
  shrublet) and [[Penianthus_longifolius]] (a shrub up to 4.5 m). **No tree.**
  This is consistent across the family worldwide.
- **High generic diversity, low per-genus diversity**: 22 genera, but
  **16 monospecific in Gabon**, several monospecific worldwide
  ([[Beirnaertia]], [[Chasmanthera]], [[Dialytheca]], [[Leptoterantha]],
  [[Limaciopsis]], [[Sarcolophium]], [[Syntriandrium]]).
- **Largest Gabonese genus in the family**: [[Triclisia]] (5 species,
  including 2 new and 2 endemics).

Figures: of 45 detected, ~6 are cover/decorative and were skipped. The
botanically meaningful figures are Planches 6–33 (line drawings, one per
species) and Figures 4–6 (composite photo plates by Breteler, Jongkind,
Bidault and others). Each used figure is linked from the relevant species
page(s); composite plates are linked from all species they illustrate with
the panel labels in the caption to avoid duplication.

Notes:
- **Vol 60 is now fully ingested** (4 / 4 families). This is the first
  complete volume on the wiki.
- The Menispermaceae treatment is the largest single ingest so far (59
  pages from a single 60-page family treatment). It triples the wiki's
  species count (11 → 47) and brings the order Ranunculales to full
  Gabonese coverage on the wiki.
- TODO: a `topics/endemics_of_gabon.md` page is now warranted —
  Menispermaceae alone contributes ~9 endemics, and combined with
  *Tetracera breteleri* there is enough to justify a dedicated topic page.
- TODO: spin out a `topics/menispermaceae_morphology.md` glossary if/when
  more Menispermaceae appear in later volumes — the family has unusual
  vocabulary (condyle, mericarp, pistillode-as-tuft-of-hairs) that future
  ingests may want to share.

## [2026-04-08] ingest | Ebenaceae (Vol 18)

Source: `sources/Ebenaceae_vol18_mineru` (mineru whole-volume OCR). The alternate
`sources/vol18_deepseek` extraction was inspected but found unusable for text
because most pages were returned as `None`. Volume citation: Halle N. (ed.)
(1970) *Flore du Gabon, Volume 18, Ebenacees*. Treatment by Rene Letouzey and
Frank White.

Created:
- `families/Ebenaceae.md`
- `genera/Diospyros.md`
- 38 species pages under `species/`, from `Diospyros_abyssinica.md` through
  `Diospyros_zenkeri.md`
- `volumes/vol18.md`

Updated:
- `index.md`
- `overview.md`

Notes:
- The published treatment is exceptional in being jointly framed for Cameroon
  and Gabon. Vol 18 recognises 36 species in Cameroon and 30 in Gabon, with
  28 shared.
- By explicit user request, the wiki ingest includes **all 38 species treated**
  in the volume, not only the 30 recorded from Gabon. Species not known from
  Gabon are represented through `distribution_gabon` / `distribution_other`
  rather than a special Gabon/non-Gabon status class.
- The OCR has heading/numbering glitches: `Diospyros longiflora` appears
  without a markdown heading marker, and `Diospyros sanza-minika` is numbered
  inconsistently in OCR output. The wiki uses corrected names and filenames.
- Figures were inventoried indirectly through plate references in the text, but
  mineru image extraction is not yet mapped cleanly enough to assign stable
  image paths on every species page without risking bad links. Text coverage
  was prioritised for this ingest.

## [2026-04-08] refresh | Ebenaceae (Vol 18 OCR rerun)

Source: `sources/Ebenaceae_vol18_mineru` regenerated by rerunning
`ocr_with_mineru.py --vol 18 --force` from the repo root. The refreshed output
is family-split and includes `text.md`, `text_keyfmt.md`, `figures.md`,
`metadata.json`, and extracted figure assets under `figures/`.

Updated:
- all 38 `species/Diospyros_*.md` pages
- `volumes/vol18.md`

Results:
- Replaced the first-pass species summaries with the full OCR treatment text
  from the rerun.
- Linked figure plates on species pages wherever `figures.md` provided a
  stable species-to-figure match; duplicate image candidates inside a figure
  block were resolved by keeping the largest extracted asset for that block.
- Standardised all Vol 18 source pointers on wiki pages to
  `sources/Ebenaceae_vol18_mineru`.

Notes:
- The rerun materially improved figure recovery, but it is still imperfect.
  31 of the 38 species pages now have figure embeds; the remaining species are
  those for which the rerun did not expose a reliable species-named entry in
  `figures.md`.
- OCR heading noise remains in the raw treatment text, including a few
  numbering and spelling glitches such as `crassiffora` in the source heading
  for *Diospyros crassiflora*. The wiki filenames and frontmatter continue to
  use corrected names.

## [2026-04-10] refresh | Ebenaceae (Vol 18 paddle figures)

Source: `sources/Ebenaceae_vol18_paddle/figures.md` and extracted plate assets under
`sources/Ebenaceae_vol18_paddle/figures/`. Used the paddle run only to repair figure
embeds on `species/Diospyros_*.md`; treatment text remains from the mineru rerun.

Updated:
- all `species/Diospyros_*.md` figure embeds to point at the paddle plate files
- removed two OCR figure artefacts left in the prose body of
  `species/Diospyros_conocarpa.md` and `species/Diospyros_zenkeri.md`

Notes:
- Paddle `figures.md` gives a cleaner species-to-plate mapping than the earlier
  mineru extraction, especially for the composite plates shared by two or three
  species.
- `Diospyros suaveolens` was linked to `fig_025_p0157.png` even though the
  paddle caption was not detected there; the page position matches Plate 25 and
  the existing wiki caption.

## [2026-04-10] infra | article intake and liteparse article mode

Created:
- `article_pdfs/` intake tree for journal PDFs (`inbox`, `queued`,
  `processed`, `rejected`, plus optional manual filing dirs)
- `article_index/articles.tsv` as a lightweight registry
- `ocr_output/articles/` as the immutable OCR source root for article-derived
  treatments

Updated:
- `ocr_liteparse.py` with `--pdf` and `--article-id` so born-digital journal PDFs
  can be parsed into `ocr_output/articles/<article_id>/liteparse/`
- `README.md`, `wiki/AGENTS.md`, and `wiki/CLAUDE.md` to document the article
  workflow and article source paths

Notes:
- Article OCR is intentionally written as a single bundle per article rather
  than auto-splitting by family. Family/taxon extraction happens at ingest.
- Article-derived evidence should extend existing taxon pages by appending a
  new `treatments` entry, not by creating parallel article pages.

## [2026-04-10] ingest | Diospyros kupensis (Kew Bulletin 53(2), 1998)

Source: `sources/articles/diospyros_kupensis/liteparse` from the local PDF
`article_pdfs/queued/diospyros_kupensis.pdf`. Article citation: Gosline G. &
Cheek M. (1998) "A new species of Diospyros (Ebenaceae) from Southwest
Cameroon." *Kew Bulletin* 53(2): 461-465.

Created:
- `species/Diospyros_kupensis.md`

Updated:
- `families/Ebenaceae.md`
- `genera/Diospyros.md`
- `index.md`
- `overview.md`
- `article_index/articles.tsv`

Notes:
- *Diospyros kupensis* was ingested as a regional comparator because the
  protologue compares it directly with [[Diospyros_conocarpa]].
- The article is already in English, so ingest was made directly from
  `text.md` without translation.
- Liteparse recovered the line drawing as JBIG2 assets with only one useful
  caption in `figures.md`; figure embeds were therefore deferred pending image
  cleanup/conversion.

## [2026-04-11] ingest | first 15 additional family treatments from `ocr_output/`

Scope: ingest the first fifteen family-split OCR directories in sorted order
whose family pages did not yet exist on the wiki. This pass was deliberately
**family-level only**: it created family pages and matching thin volume pages,
but did not yet expand those treatments into genus/species pages.

Created family pages:
- `families/Aizoaceae.md`
- `families/Alismataceae.md`
- `families/Aloaceae.md`
- `families/Anacardiaceae.md`
- `families/Annonaceae.md`
- `families/Anthericaceae.md`
- `families/Apiaceae.md`
- `families/Apocynaceae.md`
- `families/Apodanthaceae.md`
- `families/Arecaceae.md`
- `families/Aristolochiaceae.md`
- `families/Balanophoraceae.md`
- `families/Begoniaceae.md`
- `families/Boraginaceae.md`
- `families/Burmanniaceae.md`
- `families/Buxaceae.md`

Created volume pages:
- `volumes/vol16.md`
- `volumes/vol38.md`
- `volumes/vol39.md`
- `volumes/vol40.md`
- `volumes/vol41.md`
- `volumes/vol42.md`
- `volumes/vol47.md`
- `volumes/vol50.md`
- `volumes/vol53.md`
- `volumes/vol57.md`
- `volumes/vol59.md`

Updated:
- `index.md`
- `overview.md`

Source preference applied:
- preferred `paddle` over `liteparse` over `mineru` when multiple family splits existed
- ignored `Boraginaceaebuxaceae_vol57_liteparse` as an OCR artefact rather than a real family
- for Vol. 57 this mattered: [[Boraginaceae]] and [[Buxaceae]] were taken from Paddle output because liteparse text for that volume is corrupted
- for Vol. 53 the source heading is **Palmae**, but the wiki page is filed under the modern family name [[Arecaceae]]
- added [[Annonaceae]] from `sources/Annonaceae_vol16_mineru` because it is a real earlier family directory that precedes `Buxaceae` alphabetically

Outcome:
- family pages on the wiki increased from **5** to **21**
- volumes represented increased from **2** to **13**
- genus/species counts were intentionally unchanged (**26 genera / 86 species**) because this was not a deep taxon-by-taxon ingest pass

## [2026-05-09] ingest | Diospyros korupensis + D. onanae (Nordic Journal of Botany, 2009)

Source: `sources/articles/nordic_journal_of_botany_2009_gosline_diospyros_korupensis_sp_nov_and_diospyros_onanae_sp_nov_ebenaceae_from/liteparse`
from the PDF under `article_pdfs/by_family/Ebenaceae/`. Article citation:
Gosline G. (2009) "Diospyros korupensis sp. nov. and Diospyros onanae sp. nov.
(Ebenaceae) from Cameroon." *Nordic Journal of Botany*, pp. 355-358.

Created:
- `species/Diospyros_korupensis.md`
- `species/Diospyros_onanae.md`

Updated:
- `families/Ebenaceae.md`
- `genera/Diospyros.md`
- `species/Diospyros_soyauxii.md`
- `index.md`
- `overview.md`

Notes:
- Both species are **post-Vol 18 Cameroonian novelties**, so they were added as
  regional comparator taxa rather than Gabonese occurrences.
- The article is particularly valuable because it tightens the comparison space
  around existing wiki species: [[Diospyros_korupensis]] is discussed against
  [[Diospyros_longiflora]], while [[Diospyros_onanae]] is framed explicitly as
  a small-leaved ally of [[Diospyros_soyauxii]] and also keyed against
  [[Diospyros_gracilescens]] / [[Diospyros_tricolor]].
- Figure links use the article-source paths directly under `sources/articles/.../liteparse/figures/`.

## [2026-05-09] ingest | African Diospyros ferrea revision (Plant Ecology and Evolution, 2025)

Source: `sources/articles/plecevo_article_140561_en_1/liteparse` from the PDF
under `article_pdfs/by_family/Ebenaceae/`. Article citation: Mestre Serra E.,
Puglisi C., Linan A.G., Meeprom N., Rakouth H.N., Schmidt H.H. & Lowry II P.P.
(2025) "A taxonomic revision of the continental African material previously
included in Diospyros ferrea (Ebenaceae)." *Plant Ecology and Evolution*.

Created:
- `species/Diospyros_angolensis.md`
- `species/Diospyros_guineensis.md`
- `species/Diospyros_moutsambotei.md`
- `species/Diospyros_smeathmannii.md`
- `species/Diospyros_suaheliensis.md`

Updated:
- `species/Diospyros_ferrea.md`
- `families/Ebenaceae.md`
- `genera/Diospyros.md`
- `index.md`
- `overview.md`

Notes:
- This article is not a simple add-on: it **retroactively revises** the old
  African concept of [[Diospyros_ferrea]] inherited from Vol 18 and shows that
  none of the continental African material belongs to the true Indian-Sri
  Lankan species.
- The article recognizes five African species in that complex. The most
  important for the Gabon wiki are [[Diospyros_moutsambotei]] (**spec. nov.**
  with type locality in Ivindo National Park) and [[Diospyros_smeathmannii]],
  which is now explicitly documented from Estuaire.

## [2026-05-11] ingest | Huaceae + Taccaceae (Vol 38)

Sources:
- `sources/Huaceae_vol38_liteparse` (liteparse+pymupdf, 12 472 chars, 10 PDF pages, 4 figures detected). Author: Yves Azizet Issembe. Translated inline from `text.md`.
- `sources/Taccaceae_vol38_liteparse` (liteparse+pymupdf, 14 754 chars, 9 PDF pages, 3 figures detected). Author: Marc S.M. Sosef. Translated inline from `text.md`.

Created:
- `families/Huaceae.md`
- `families/Taccaceae.md`
- `genera/Afrostyrax.md`
- `genera/Hua.md`
- `genera/Tacca.md`
- `species/Afrostyrax_kamerunensis.md`
- `species/Afrostyrax_lepidophyllus.md`
- `species/Afrostyrax_macranthus.md`
- `species/Hua_gabonii.md`
- `species/Tacca_leontopetaloides.md`

Updated:
- `volumes/vol38.md`
- `index.md`
- `overview.md`

Figures used:
- `sources/Huaceae_vol38_liteparse/figures/fig_018_p0029.png` — Planche 5, linked from [[Afrostyrax_lepidophyllus]]
- `sources/Huaceae_vol38_liteparse/figures/fig_019_p0031.png` — Planche 6, linked from [[Afrostyrax_macranthus]]
- `sources/Huaceae_vol38_liteparse/figures/fig_020_p0033.png` — Planche 7, linked from [[Hua_gabonii]]
- `sources/Taccaceae_vol38_liteparse/figures/fig_033_p0062.jpeg` — Planche 13, linked from [[Tacca_leontopetaloides]]

Figures skipped:
- `Huaceae` fig. 17 (page 25) and `Taccaceae` figs. 32 and 34 (pages 59 and 63) because the extracted figure inventory gives no usable caption and they appear to be family-opening or incomplete plate artefacts rather than uniquely informative species figures.

Notes:
- [[Huaceae]] adds the wiki's first **Oxalidales** family and contributes a small but distinctive African woody lineage defined by alliacous bark and seeds.
- [[Taccaceae]] expands the Dioscoreales coverage beyond the delicate [[Burmanniaceae]] by adding a large stemless geophyte of open and coastal habitats.
- The Huaceae species accounts do not give province-level Gabon distributions, so those frontmatter fields were left empty rather than guessed.

## [2026-05-11] ingest | Sapotaceae (Vol 1) — initial chunk

Source: `sources/Sapotaceae_vol01_paddle` (Paddle OCR; 170 PDF pages,
223,124 characters, 31 figures detected). Author/editorial treatment:
A. Aubréville, Vol 1 of *Flore du Gabon* (1961). No `text_en.md` was present,
so the ingest was translated inline from the French `text.md`.

Created:
- `families/Sapotaceae.md`
- `genera/Manilkara.md`
- `species/Manilkara_le_testui.md`
- `species/Manilkara_fouilloyana.md`
- `species/Manilkara_lacera.md`
- `species/Manilkara_microphylla.md`
- `species/Manilkara_welwitschii.md`
- `volumes/vol01.md`

Updated:
- `index.md`
- `overview.md`

Figures used:
- `fig_004_p0033.png` — plate covering *Manilkara le-testui* and
  *M. fouilloyana*; linked from both species pages.
- `fig_005_p0039.png` — plate for *Manilkara microphylla*; linked from that
  species page.
- No source figures were linked for *M. lacera* or *M. welwitschii*.

Notes:
- This is the first ingest from the early Aubréville era and the first from
  Vol 1. Unlike later short family treatments, Sapotaceae is a full
  monograph and is being ingested in staged genus-level blocks.
- The family treatment reports **23 genera** and stresses the importance of
  Sapotaceae in primary humid forest; the current wiki materializes 48
  accepted/present species pages from that Vol. 1 treatment.
- The OCR intermittently gives the genus as `Manikara`; the wiki normalises
  to the accepted spelling [[Manilkara]].
- Some species pages intentionally leave `distribution_gabon` broad or empty
  where the source account does not support province-level resolution.

## [2026-05-11] ingest | Sapotaceae (Vol 1) — Letestua + Autranella

Source: `sources/Sapotaceae_vol01_paddle` (same Paddle family split used for
the initial Vol 1 Sapotaceae ingest). Translated inline from `text.md`.

Created:
- `genera/Letestua.md`
- `species/Letestua_durissima.md`
- `genera/Autranella.md`
- `species/Autranella_congolensis.md`

Updated:
- `families/Sapotaceae.md`
- `volumes/vol01.md`
- `index.md`
- `overview.md`

Figures used:
- `fig_006_p0043.png` — Plate III, linked from [[Letestua_durissima]]
- `fig_007_p0045.png` — Plate IV, linked from [[Autranella_congolensis]]

Notes:
- Both genera are effectively **monotypic in the Gabon treatment**, so this
  pass added two genera while only adding two species pages.
- [[Letestua]] is treated as a Gabon-Mayombe lineage with extremely hard
  wood; the account explicitly reduces *Letestua floribunda* to synonymy
  under [[Letestua_durissima]].
- [[Autranella]] is morphologically clear at generic level, especially by its
  seed scar and 4 + 4 calyx, but the treatment remains cautious about whether
  more than one Central African species should ultimately be recognized.

## [2026-05-11] ingest | Sapotaceae (Vol 1) — Tieghemella + Baillonella

Source: `sources/Sapotaceae_vol01_paddle` (same Paddle family split used for
the earlier Sapotaceae genus blocks). Translated inline from `text.md`.

Created:
- `genera/Tieghemella.md`
- `species/Tieghemella_africana.md`
- `genera/Baillonella.md`
- `species/Baillonella_toxisperma.md`

Updated:
- `families/Sapotaceae.md`
- `volumes/vol01.md`
- `index.md`
- `overview.md`

Figures used:
- `fig_008_p0051.png` — Plate V, linked from [[Tieghemella_africana]]
- `fig_010_p0057.png` — Plate VI, linked from [[Baillonella_toxisperma]]

Notes:
- Both accounts are major timber trees and include unusually substantial
  ecological and economic discussion for a flora treatment.
- [[Tieghemella_africana]] is the Gabon-Cameroon **douka** tree, emphasized as
  one of the great primary-forest emergents and noted for seeds yielding an
  edible fat.
- [[Baillonella_toxisperma]] is the classic **moabi** account. The source
  spends several pages sorting out early nomenclatural confusion with
  [[Tieghemella_africana]] and defending the priority of the epithet
  `toxisperma`.
- The southern form `Baillonella toxisperma` var. `obovata` is recorded in the
  species notes but was not given a separate wiki species page because the
  treatment itself keeps it below species rank.

## [2026-05-11] ingest | Sapotaceae (Vol 1) — Lecomtedoxa

Source: `sources/Sapotaceae_vol01_paddle` (same Paddle family split used for
the earlier Sapotaceae genus blocks). Translated inline from `text.md`.

Created:
- `genera/Lecomtedoxa.md`
- `species/Lecomtedoxa_klaineana.md`
- `species/Lecomtedoxa_saint-aubini.md`
- `species/Lecomtedoxa_nogo.md`
- `species/Lecomtedoxa_heitzana.md`
- `species/Lecomtedoxa_biraudii.md`

Updated:
- `families/Sapotaceae.md`
- `volumes/vol01.md`
- `index.md`
- `overview.md`

Figures used:
- `fig_011_p0063.png` — Plate VII, linked from [[Lecomtedoxa_klaineana]]
- `fig_012_p0067.png` — Plate VIII, linked from [[Lecomtedoxa_nogo]]
- `fig_013_p0071.png` — Plate IX, linked from [[Lecomtedoxa_biraudii]]

Notes:
- The treatment presents [[Lecomtedoxa]] as a strongly Gabonese, probably
  littoral genus centered on dehiscent one-seeded fruits and very hard wood.
- Several species are poorly known: [[Lecomtedoxa_saint-aubini]] is based only
  on the holotype in bud, and [[Lecomtedoxa_biraudii]] only on the type
  collection.
- [[Lecomtedoxa_nogo]] is ecologically distinctive as a Fernan Vaz lagoon
  species of marshy ground, reportedly forming local stands and yielding seeds
  used for edible fat.

## [2026-05-11] ingest | Sapotaceae (Vol 1) — Neolemonniera + Gluema

Source: `sources/Sapotaceae_vol01_paddle` (same Paddle family split used for
the earlier Sapotaceae genus blocks). Translated inline from `text.md`.

Created:
- `genera/Neolemonniera.md`
- `species/Neolemonniera_ogouensis.md`
- `genera/Gluema.md`
- `species/Gluema_ivorensis.md`

Updated:
- `families/Sapotaceae.md`
- `volumes/vol01.md`
- `index.md`
- `overview.md`

Figures used:
- `fig_011_p0063.png` — Plate VII, linked from [[Neolemonniera_ogouensis]]
- `fig_014_p0077.png` — Plate X, linked from [[Gluema_ivorensis]]

Notes:
- [[Neolemonniera]] is kept distinct largely on vegetative grounds: stipulate
  pseudo-whorled leaves with conspicuous striation.
- [[Gluema]] is separated from [[Lecomtedoxa]] by the position and fusion of
  the staminodes despite sharing a dehiscent one-seeded fruit.
- The treatment says the Gabonese [[Gluema_ivorensis]] should not be split
  from the Ivorian material despite small differences.

## [2026-05-11] ingest | Sapotaceae (Vol 1) — Tridesmostemon

Source: `sources/Sapotaceae_vol01_paddle` (same Paddle family split used for
the earlier Sapotaceae genus blocks). Translated inline from `text.md`.

Created:
- `genera/Tridesmostemon.md`
- `species/Tridesmostemon_omphalocarpoides.md`

Updated:
- `families/Sapotaceae.md`
- `volumes/vol01.md`
- `index.md`
- `overview.md`

Figures used:
- `fig_017_p0087.png` — Plate XIII, linked from
  [[Tridesmostemon_omphalocarpoides]]

Notes:
- The treatment rejects `Nzidora` as distinct from [[Tridesmostemon]], on the
  grounds that the number of anthers per staminal phalanx is too weak a
  generic character.
- The genus is explicitly contrasted with [[Omphalocarpum]] by its axillary
  flowers and its stamens united into hairy phalanges.

## [2026-05-11] ingest | Sapotaceae (Vol 1) — Omphalocarpum

Source: `sources/Sapotaceae_vol01_paddle` (same Paddle family split used for
the earlier Sapotaceae genus blocks). Translated inline from `text.md`.

Created:
- `genera/Omphalocarpum.md`
- `species/Omphalocarpum_procerum.md`
- `species/Omphalocarpum_elatum.md`
- `species/Omphalocarpum_le-testui.md`

Updated:
- `families/Sapotaceae.md`
- `volumes/vol01.md`
- `index.md`
- `overview.md`

Figures used:
- `fig_015_p0081.png` — Plate XI, linked from [[Omphalocarpum_procerum]]
- `fig_016_p0085.png` — Plate XII, linked from [[Omphalocarpum_le-testui]]

Notes:
- The treatment treats [[Omphalocarpum]] as morphologically unmistakable,
  especially by its trunk-borne fruits, but also argues that many names in the
  genus probably collapse because too many were based only on seeds.
- For the Gabon flora, the author keeps only **3 species**:
  [[Omphalocarpum_procerum]], [[Omphalocarpum_elatum]], and
  [[Omphalocarpum_le-testui]].
- A seed-and-fruit-only `O. ogouense` is mentioned, but not retained among the
  well-defined Gabonese species pages.

## [2026-05-11] ingest | Sapotaceae (Vol 1) — Englerophytum

Source: `sources/Sapotaceae_vol01_paddle` (same Paddle family split used for
the earlier Sapotaceae genus blocks). Translated inline from `text.md`.

Created:
- `genera/Englerophytum.md`
- `species/Englerophytum_hallei.md`
- `species/Englerophytum_kouloungense.md`
- `species/Englerophytum_le-testui.md`

Updated:
- `families/Sapotaceae.md`
- `volumes/vol01.md`
- `index.md`
- `overview.md`

Figures used:
- `fig_018_p0093.png` — Plate XIV, linked from [[Englerophytum_hallei]]
- `fig_023_p0111.png` — Plate XIX, linked from
  [[Englerophytum_kouloungense]] and [[Englerophytum_le-testui]]

Notes:
- The treatment reduces former segregates such as `Bequaertiodendron` and
  `Tisserantiodoxa` into [[Englerophytum]].
- Two of the three Gabonese species remain notably incomplete in the source:
  [[Englerophytum_kouloungense]] is based only on sterile material, and
  [[Englerophytum_le-testui]] mainly on fruit.

## [2026-05-11] ingest | Sapotaceae (Vol 1) — Wildemaniodoxa + Zeyherella

Source: `sources/Sapotaceae_vol01_paddle` (same Paddle family split used for
the earlier Sapotaceae genus blocks). Translated inline from `text.md`.

Created:
- `genera/Wildemaniodoxa.md`
- `species/Wildemaniodoxa_laurentii.md`
- `genera/Zeyherella.md`
- `species/Zeyherella_le-testui.md`
- `species/Zeyherella_mayombense.md`

Updated:
- `families/Sapotaceae.md`
- `volumes/vol01.md`
- `index.md`
- `overview.md`

Figures used:
- `fig_019_p0097.png` — Plate XV, linked from [[Wildemaniodoxa_laurentii]]
- `fig_020_p0099.png` — Plate XVI, linked from [[Zeyherella_le-testui]]
- `fig_021_p0101.png` — Plate XVII, linked from [[Zeyherella_mayombense]]

Notes:
- [[Wildemaniodoxa]] is treated as morphologically unique in African
  Sapotaceae because of its **10-lobed corolla**, **10 stamens**, and
  **10-locular ovary**.
- The treatment accepts **2 confirmed Gabonese species** of [[Zeyherella]] and
  discusses a third riparian Congo species as probably occurring in Gabon but
  not yet collected there.
- The source explicitly rejects merging [[Zeyherella]] into
  `Bequaertiodendron`.

## [2026-05-11] ingest | Sapotaceae (Vol 1) — Tulestea

Source: `sources/Sapotaceae_vol01_paddle` (same Paddle family split used for
the earlier Sapotaceae genus blocks). Translated inline from `text.md`.

Created:
- `genera/Tulestea.md`
- `species/Tulestea_tomentosa.md`
- `species/Tulestea_koulamoutouensis.md`
- `species/Tulestea_gabonensis.md`

Updated:
- `families/Sapotaceae.md`
- `volumes/vol01.md`
- `index.md`
- `overview.md`

Figures used:
- `fig_022_p0107.png` — Plate XVIII, linked from all 3 Tulestea species with
  the relevant panels identified in captions.

Notes:
- The treatment recognizes **3 Gabonese species** and suggests a probable
  fourth still poorly known.
- [[Tulestea]] is treated as close to [[Afrosersalisia]], but separated by its
  nearly free sepals and very short staminal filaments.

## [2026-05-11] ingest | Sapotaceae (Vol 1) — Afrosersalisia

Source: `sources/Sapotaceae_vol01_paddle` (same Paddle family split used for
the earlier Sapotaceae genus blocks). Translated inline from `text.md`.

Created:
- `genera/Afrosersalisia.md`
- `species/Afrosersalisia_afzelii.md`

Updated:
- `families/Sapotaceae.md`
- `volumes/vol01.md`
- `index.md`
- `overview.md`

Figures used:
- `fig_023_p0111.png` — Plate XIX, linked from
  [[Afrosersalisia_afzelii]]

Notes:
- The treatment keeps [[Afrosersalisia]] separate from [[Synsepalum]] and
  [[Pachystela]] by its scale-like staminodes and the small persistent cupule
  formed by the calyx at the fruit base.
- [[Afrosersalisia_afzelii]] is treated as a widespread humid-forest tree from
  Sierra Leone to Gabon.

## [2026-05-11] ingest | Sapotaceae (Vol 1) — final remaining genera

Source: `sources/Sapotaceae_vol01_paddle` (same Paddle family split used for
all earlier Sapotaceae genus blocks). Translated inline from `text.md`.

Created:
- `genera/Pachystela.md`
- `species/Pachystela_brevipes.md`
- `species/Pachystela_buluensis.md`
- `genera/Synsepalum.md`
- `species/Synsepalum_le-testui.md`
- `species/Synsepalum_longecuneatum.md`
- `species/Synsepalum_congolense.md`
- `species/Synsepalum_fleuryanum.md`
- `genera/Vincentella.md`
- `species/Vincentella_ogouensis.md`
- `genera/Pseudopachystela.md`
- `species/Pseudopachystela_lastoursvillensis.md`
- `species/Pseudopachystela_oyemensis.md`
- `genera/Gambeya.md`
- `species/Gambeya_boukokoensis.md`
- `species/Gambeya_subnuda.md`
- `species/Gambeya_africana.md`
- `genera/Delpydora.md`
- `species/Delpydora_macrophylla.md`
- `genera/Donella.md`
- `species/Donella_ogowensis.md`
- `species/Donella_pentagonocarpa.md`
- `species/Donella_pruniformis.md`
- `species/Donella_welwitschii.md`
- `genera/Aningueria.md`
- `species/Aningueria_altissima.md`

Updated:
- `families/Sapotaceae.md`
- `volumes/vol01.md`
- `index.md`
- `overview.md`

Notes:
- This completes the accepted Gabonese genera and species from the Vol 1
  Sapotaceae treatment.
- I preserved the source limits instead of smoothing them away:
  [[Pachystela_buluensis]] remains poorly known,
  [[Synsepalum_fleuryanum]] is known only from buds,
  [[Vincentella_ogouensis]] still lacks a known fruit in the treatment, and
  several [[Donella]] and [[Gambeya]] entities are treated as highly variable.
