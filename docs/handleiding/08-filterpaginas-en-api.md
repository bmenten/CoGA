# 8. Filterpagina's ↔ API

Dit hoofdstuk beschrijft hoe CoGA, nádat een package correct is geïmporteerd (zie [hoofdstuk 6](06-import-pipeline.md)), de juiste varianten voor een familie *filtert* en *toont*. De hele keten wordt gevolgd: van de filtervelden die de analist in de browser invult, via de opbouw van de API-aanvraag, de backend-filterservice die er een veilige ClickHouse-query van maakt, de verrijking met metadata en review-status uit Postgres, tot de weergave in tabel en kaarten. In **deel A** komen de twee grootste en meest representatieve datatypes aan bod: de *Small Variants* (SNV/indel — puntmutaties en kleine inserties/deleties) en de *structural variants* (SV — grote deleties, duplicaties, enz.). **Deel B** van dit hoofdstuk behandelt dezelfde keten voor de overige modaliteiten (mitochondriaal, Paraphase, TRGT/tandem repeats, NIPT en PGT) en sluit af met de gezamenlijke bestandentabel voor het hele hoofdstuk.

Enkele begrippen die telkens terugkomen. Een **endpoint** is een URL waarop de backend luistert (bv. `GET /families/FAM01/small-variants`). Een **queryparameter** is een `?sleutel=waarde`-paar in die URL; zo geeft de frontend de filterkeuzes door. **ClickHouse** is de kolomgeoriënteerde database waarin de vele variantrijen staan; **Postgres** bevat de metadata (families, samples, panels) én de handmatige review-toestand (classificaties, tags, notities). Een **geparametriseerde query** is een query waarin gebruikerswaarden niet in de SQL-tekst worden geplakt maar apart als parameters worden meegegeven — dat is de kern van de bescherming tegen SQL-injectie (het injecteren van kwaadaardige SQL via een invoerveld).

## Het algemene stramien

Voor élk variantdatatype in CoGA verloopt het zoeken volgens hetzelfde vaste patroon. Wie dit patroon eenmaal begrijpt, herkent het in alle filterpagina's terug:

1. **Filter in de UI** — de analist vult een filterformulier in (frequentie, effect, genen, genotype per sample, tags…).
2. **Queryparameters** — de frontend zet die keuzes om in een querystring en doet een HTTP-aanvraag naar het endpoint.
3. **Endpoint** — een FastAPI-router ontvangt de aanvraag, controleert de toegang en bouwt de *metadata-context* van de familie.
4. **Filterservice** — een service vertaalt de parameters naar een **geparametriseerde** ClickHouse-query, met paginatie (het opdelen in pagina's) en een begrensde telling.
5. **Join met Postgres** — de ruwe variantrijen uit ClickHouse worden verrijkt met genmetadata, review/ACMG-status, tags en interne cohortfrequentie uit Postgres.
6. **Render** — het resultaat wordt in de browser als tabel of kaarten getoond, met paginatie.

**Waar in de code:** de scheiding is consequent doorgevoerd — routers in `backend/app/routers/` (stap 3), filterservices in `backend/app/services/clickhouse_family_variants.py` en `backend/app/services/clickhouse_variant_queries.py` (stap 4-5), de React-pagina's in `frontend/src/pages/families/` (stap 1-2 en 6).

## Small variants: het filterformulier in de UI

De small-variantpagina is `frontend/src/pages/families/FamilySmallVariantsPage.tsx`. Die component (herbruikbaar UI-blok) haalt de familie op (`GET /families/{familyId}`), laadt de beschikbare genpanels, en delegeert alle filterlogica aan de hook `useSmallVariantSearchState` (in `frontend/src/pages/families/smallVariantSearch.ts`). Een *hook* is een herbruikbaar stukje React-logica dat toestand beheert. Het formulier zelf staat in `frontend/src/pages/families/SmallVariantFilterForm.tsx`.

Het formulier is opgedeeld in secties, elk met een groep gerelateerde filters. De onderstaande tabel volgt uit de queryparameters die de frontend opbouwt (`buildSmallVariantQueryParams`) en die de backend accepteert (de dependency `_family_small_variant_filters`):

| Filtergroep | Voorbeelden van velden |
| --- | --- |
| **Locus / gen** | genlijst (meerdere genen), chromosoom + start/end, `intervals`, `transcript`, `exclude_gene`, `exclude_intervals` |
| **Overerving (inheritance)** | overervingsmodel (de-novo/dominant, recessief, X-gebonden, compound-het), genotype-snelkeuzes per lid, *expanded carrier screening* |
| **Kwaliteit (per sample)** | genotype-kwaliteit (GQ/`qual`), leesdiepte (DP), allelfractie (AF), alt-diepte (`ad_alt`) |
| **Frequentie** | `max_gnomad_af`, `max_gnomad_exomes_af`, `max_gnomad_genomes_af`, `max_gnomad_popmax_af`, `max_topmed_af`, `max_gnomad_ac`, `max_gnomad_hom_count`, `max_gnomad_hemi_count` |
| **Effect / impact** | `impact` (HIGH/MODERATE/…), `effect` (annotatietermen), `canonical_only`, `mane_only`, `lof_only` |
| **ClinVar** | in te sluiten status (`clinvar`), uit te sluiten status (`exclude_clinvar`), en "P/LP overruled de frequentiefilter" (`clinvar_overrides_frequency`) |
| **Predictiescores** | `min_cadd`, `min_revel`, `min_spliceai`, `sift`, `polyphen` |
| **Panel** | `panel_id` (standaard het Mendeliome-panel) |
| **Structural second hit** | `require_sv_second_hit` — beperk tot genen die óók door een SV geraakt worden |
| **Review / tags** | `classification` (ACMG-klasse), `review_tag`, `exclude_review_tag`, `has_notes` |

**Waar in de code:** de secties en velden staan in `SmallVariantFilterForm.tsx`; de standaardinstelling bij een verse opening — het fenotype-prioriteringspreset (`phenotype_priority`) plus het Mendeliome-panel als scope — wordt gezet in de hook `useSmallVariantSearchState` (het `useEffect`-blok dat de default één keer per familie toepast, met de `mendeliomePanelId` afgeleid uit de panellijst).

### Hoe de frontend de aanvraag opbouwt

De filterkeuzes worden tot een querystring geserialiseerd (omgezet in tekst) door `buildSmallVariantQueryParams` in `smallVariantSearch.ts`. Die functie start met `page` en `page_size=100` en voegt daarna elke actieve filter toe als queryparameter. Lijstfilters worden *herhaalde* parameters (`params.append('impact', …)` per waarde), enkelvoudige filters gebruiken `params.set(...)`.

Twee details zijn belangrijk voor traceerbaarheid:

- **Standaardwaarden expliciet meesturen.** `prioritize` (fenotype-prioritering) en `clinvar_overrides_frequency` staan standaard aan; ze worden altíjd expliciet als `'true'`/`'false'` meegestuurd, zodat een uitgezette toestand de heen-en-terug door de URL overleeft (`params.set('prioritize', … === 'true' ? 'true' : 'false')`).
- **Sample-filter-codering.** Een genotype/kwaliteitsfilter per sample wordt in één parameter samengeperst:
  ```
  sample_filter = [sample, gt.join('|'), qual, dp, af, ad_alt].join(':')
  ```
  Dus bv. `sample_filter=child:0/1|1/1:20:10:0.2:5`. Deze compacte codering wordt aan de backend-kant weer uit elkaar getrokken.

**Waar in de code:** `buildSmallVariantQueryParams` in `frontend/src/pages/families/smallVariantSearch.ts`; de gedeelde genotype-parsinghulpen `parseSerializedGenotypeSelection` en `parseExplicitSampleFilterMap` in `frontend/src/lib/sampleFilterState.ts`. De HTTP-aanvraag zelf loopt via de gedeelde axios-client `frontend/src/lib/api.ts`, die via een *request-interceptor* automatisch het `Authorization: Bearer …`-token toevoegt en via een *response-interceptor* bij een 401 (niet-geautoriseerd) naar `/login` stuurt.

## Small variants: het backend-endpoint en de filterservice

De aanvraag komt binnen op `GET /{family_id}/small-variants`, afgehandeld door `get_family_small_variants` in `backend/app/routers/families_small_variants.py`. De router doet drie dingen:

1. **Paginatie begrenzen** — `page_size: int = Query(default=100, ge=0, le=MAX_VARIANT_PAGE_SIZE)`. `MAX_VARIANT_PAGE_SIZE` (afgeleid van `_SMALL_TRACK_RESULT_LIMIT`) begrenst de paginagrootte; een te grote pagina kan dus geen dure diepe scan afdwingen.
2. **Filters parsen** — alle filter-queryparameters worden gebundeld door de FastAPI-dependency `_family_small_variant_filters`. Die functie is bewust gedeeld tussen het pagineringsendpoint en het CSV-export-endpoint (`export_family_small_variants_csv`), zodat beide exact dezelfde filtering toepassen; de telling is niet een apart endpoint maar de `count_only`-modus van hetzelfde pagineringsendpoint.
3. **Toegang en context** — `build_family_metadata_context(session, family_identifier=family_id, user=user, project_id=…)` laadt de familie, de samples en de toegankelijke projecten, én dwingt af dat de gebruiker deze familie mág zien (zie [hoofdstuk 2](02-beveiliging-rollen-rechten.md)).

Daarna roept de router de eigenlijke filterservice aan: `get_family_small_variants_page` in `backend/app/services/clickhouse_family_variants.py`.

### Van parameters naar een SmallVariantQueryFilters

In de service worden de losse parameters eerst in één *dataclass* (een simpel gegevensobject met vaste velden) `SmallVariantQueryFilters` gegoten, gedefinieerd in `backend/app/services/family_variant_filters.py`. Diezelfde module bevat ook de parser voor de sample-filtercodering: `parse_small_variant_sample_filter` splitst de `sample:gt:qual:dp:af:ad`-string in een `SmallVariantSampleFilter`, en `parse_genotype_filter` vertaalt tokens als `het`/`hom`/`ref` naar de concrete genotype-strings (`0/1`, `1/1`, …) via `GENOTYPE_ALIASES`. Dit is precies het spiegelbeeld van de frontend-codering, zodat beide kanten dezelfde taal spreken.

`get_family_small_variants_page` kiest vervolgens een uitvoeringspad, ongeveer in deze volgorde:

- **`count_only`** — enkel een aanwezigheidscheck voor het familiedashboard: `_family_has_small_variants` doet een `LIMIT 1` op de geïndexeerde `family_guid`-kolom en geeft in feite 0 of 1 terug.
- **`prioritize`** (standaard aan) — delegeert naar `_prioritized_small_variants_page`, de fenotype-gestuurde rangschikking met caching (zie verder).
- **Review-filters** — `classification`, `review_tag` en `has_notes` worden éérst in Postgres opgelost tot een verzameling variant-id's (`list_matching_small_variant_review_ids`); uit te sluiten tags idem. Die id-sets gaan als `include`/`exclude`-lijst de ClickHouse-query in. Levert het nul id's op, dan is het antwoord meteen een lege pagina.
- **Panel** — `_fetch_panel_constraints` haalt de genen/regio's van het panel uit Postgres.
- **Native pad vs. Python-pad** — `_can_use_small_native_page` bepaalt of de filter volledig in ClickHouse kan draaien (het snelle "native" pad). Overervingsmodi die paren of ouder-kindvergelijking vereisen (compound-het, de-novo, …) worden in Python nagerekend op een begrensde kandidatenset.

### De geparametriseerde ClickHouse-query

Het hart van de veiligheid zit in `_small_variant_where_clauses` (en de bredere samensteller `_small_query_filter_parts`) in `backend/app/services/clickhouse_variant_queries.py`. Deze functie bouwt een lijst WHERE-clausules (filtervoorwaarden) op als tekst, maar **elke gebruikerswaarde gaat als parameter mee**, nooit als ingeplakte string. Uit de code:

```python
where_clauses = ["e.family_guid = %(family_guid)s", "e.sign = 1"]
params = {"family_guid": context.family_uuid}
if context.project_ids:
    where_clauses.append("e.project_guid IN %(project_ids)s")
    params["project_ids"] = tuple(context.project_ids)
```

De `%(naam)s`-plaatshouders worden door de ClickHouse-driver veilig ingevuld. Zo worden ook chromosoom, positievenster, frequentiedrempels, gen-termen, ClinVar-status en de genotype/QC-sample-filters toegevoegd — telkens als parameter. Wat níet van de gebruiker komt maar wél in de SQL-tekst staat, zijn de *tabelnamen*; die worden per assembly (genoomreferentie) opgebouwd via `_small_table_name`, dat de assembly-naam door `_require_clickhouse_identifier` loodst — een strikte identifiercontrole (via `clickhouse_dataset_key`) die verhindert dat er iets anders dan een geldige, bekende tabelnaam in de query belandt.

De eigenlijke rij-ophaal gebeurt in `_fetch_small_variant_rows`. Belangrijke eigenschappen:

- **Deduplicatie** — de `entries`-tabel is een CollapsingMergeTree (een ClickHouse-tabeltype dat oude en nieuwe rijen via een `sign`-kolom "wegstreept"); de query groepeert met `GROUP BY e.key` en `any(...)`-aggregaties zodat er precies één rij per variant overblijft (bij replica-vertraging kunnen anders meerdere `sign=1`-rijen tegelijk bestaan).
- **Vaste, reproduceerbare sortering** — `ORDER BY any(e.xpos), key`, dus op genomische positie met een unieke tiebreaker. De sorteervolgorde is dus *niet* door de gebruiker te sturen; de kolom-sorteerknopjes in de tabel werken client-side.
- **Paginatie** — `_append_limit_offset` voegt `LIMIT %(limit)s OFFSET %(offset)s` toe (opnieuw als parameters); `_clamp_small_variant_page` begrenst de pagina zodat een extreem hoog paginanummer geen gigantische OFFSET-scan kan uitlokken.
- **Annotatie-join** — de meerdere-KB-grote annotatie-JSON zit niet in `entries` maar in de `variants/details`-tabel (kolom `annotationsJson`); die wordt per pagina apart opgehaald door `_fetch_small_variant_detail_map` en aan de rijen gekoppeld.

De **telling** loopt via `_count_small_variant_rows_bounded`, die het aantal begrenst op `_SMALL_COUNT_LIMIT`. Daarom toont de UI soms een "+"-teller: een begrensde teller in plaats van een dure volledige telling (het antwoord draagt hiervoor `total_is_estimated`). Als ClickHouse een query weigert omdat die te zwaar is, vertaalt `_execute_clickhouse` dat via `_CLICKHOUSE_QUERY_TOO_HEAVY_MARKERS` naar een nette **HTTP 422** met een concrete boodschap ("verfijn de zoekopdracht met een regio, gen of striktere drempels"), in plaats van een ondoorzichtige 500-fout.

### De fenotype-rangschikking en de ranking-cache

Het standaardbeeld (fenotype-prioritering + Mendeliome) is duur om te berekenen (de genscoring tegen de Monarch-kennisgraaf domineert). Daarom wordt de *gerangschikte volgorde* per familie gecachet (tussentijds opgeslagen). `_prioritized_small_variants_page` berekent een `inputs_hash` via `compute_ranking_hashes` in `backend/app/services/variant_ranking_cache.py`: een SHA-256 (een cryptografische vingerafdruk) over álles wat de rangschikking beïnvloedt — de filters, de HPO-termen (fenotypecodes) van de aangedane individuen, een pedigree-handtekening (`structure_hash`), de Monarch-release, de panelversie, de review-filterstaat en een algoritme-versienummer (`_ALGORITHM_VERSION`).

Het cruciale veiligheidskenmerk: **elke wijziging in een input verandert de hash, dus een verouderde rangschikking wordt nooit geserveerd** — een misser leidt tot herberekening. De cache (Postgres-tabel `family_variant_ranking_cache`) bewaart enkel de compacte volgorde; de annotaties en review-status worden bij élke weergave vers opnieuw opgehaald. Een tweede hash zonder het panel (`base_hash`) laat een smaller (sub)panel bedienen uit een bredere, volledige cache (`_serve_subpanel_from_superset`), na hervalidatie tegen ClickHouse. De UI krijgt dit mee via `ranking_cached` en `ranking_computed_at` op het antwoord.

**Waar in de code:** `variant_ranking_cache.py` (hashing en Postgres-cache), `_prioritized_small_variants_page` / `_serve_ranking_from_cache` / `_serve_subpanel_from_superset` in `clickhouse_family_variants.py`, en het ontwerpdocument `docs/variant-ranking-cache.md`.

## Join & verrijking: van ClickHouse-rij naar getoonde variant

De ruwe records uit ClickHouse worden eerst tot API-objecten (`VariantOut`) omgezet door `_small_variant_out`, en daarna verrijkt door `_hydrate_small_variant_outs` (beide in `clickhouse_family_variants.py`). Die verrijkingsstap combineert bronnen:

- **Review/ACMG-status en tags** — `get_small_variant_review_map` haalt per variant-id de opgeslagen review op uit Postgres (module `small_variant_review_pg.py`) en zet die op `variant.review`.
- **Interne cohortfrequentie** — `_fetch_internal_cohort_map` telt in ClickHouse over de toegankelijke projecten hoe vaak elke variant in het eigen cohort voorkomt (hom/het/samples/families), zodat recurrente artefacten zichtbaar worden.
- **Genconstraint-metrieken** — `_fetch_gene_constraint_metric_map` haalt gen-constraintmaten op uit de Postgres-tabel `gene_info`.
- **SV "second hit"** — `_attach_sv_second_hits` markeert een SNV waarvan het gen óók door een structurele variant geraakt wordt (zie verder).

Een terminologische precisie: de module `backend/app/services/clickhouse_variant_rows.py` is de *schrijf*-kant: die bouwt bij import de entry- en annotatie-index-rijen die naar ClickHouse worden weggeschreven (bv. `_small_variant_entry_rows`, `_small_annotation_index_row`). De *lees*-kant-verrijking die de getoonde variant samenstelt, gebeurt in `_hydrate_small_variant_outs`. Beide leunen op gedeelde annotatiehulpjes uit `backend/app/services/clickhouse_variant_records.py` (de dataclasses `SmallVariantRecord` en `SmallVariantCall`, en helpers als `_annotation_gene` en `_annotation_clinvar`).

## Small variants: de weergave

Het antwoord (een `VariantPage` met `variants`, eventueel `variant_groups` voor compound-het-paren, `total`, `small_variant_summary` en de ranking-provenance) wordt in de browser gerenderd:

- **`SmallVariantResults.tsx`** — de omhullende sectie. Toont een teller, een weergaveschakelaar (Auto/Table/Cards; "Auto" toont tot `CARD_VIEW_THRESHOLD` (100) kaarten, daarboven een tabel), de CSV-exportknop (die het endpoint `/families/{familyId}/small-variants/export` met dezelfde querystring aanroept), en statusmeldingen: een waarschuwing bij `ranking_truncated` (te veel kandidaten om volledig te rangschikken) en de subtiele "⚡ Prioritised ranking served from cache"-noot bij `ranking_cached`. Compound-het-paren worden apart als paar-kaarten getoond (`SmallVariantPairCards.tsx`), losse varianten daaronder.
- **`SmallVariantTable.tsx`** — de tabelweergave met kolommen o.a. Chr, Position, End, Gene, Ref, Alt, Impact, Effect, Review, Genotypes, IGV en View. De kolommen positie (Chr/Position), gen, impact en — bij fenotype-prioritering — de prioriteitsscore (Score) zijn client-side sorteerbaar.
- **`SmallVariantCards.tsx`** — de kaartweergave (rijkere per-variant-context).
- **`ResultsPagination.tsx`** — een eenvoudige Prev/Next-balk met "Page x of y"; het paginatotaal (`totalPages`) wordt door de pagina berekend als `Math.ceil((data?.total ?? 0) / 100)` in `FamilySmallVariantsPage.tsx`.

**Waar in de code:** `frontend/src/pages/families/SmallVariantResults.tsx`, `SmallVariantTable.tsx`, `ResultsPagination.tsx`. De review-mutaties (tag togglen, review opslaan) lopen via `FamilySmallVariantsPage.tsx` en spreken de `PUT /{family_id}/small-variants/{variant_id}/review`-endpoints aan (zie [hoofdstuk 10](10-tagging-en-acmg-classificatie.md)).

### Compound-heterozygote paren

Bij een autosomaal-recessieve aandoening is één losse heterozygote variant meestal niet ziekteveroorzakend: een dragervariant op één allel wordt gecompenseerd door het gezonde tweede allel. Twee *verschillende* heterozygote varianten in hetzelfde gen kunnen samen wél oorzakelijk zijn — mits ze **in trans** liggen, dat wil zeggen één op elk allel. Dan is het gen biallelisch geraakt (beide kopieën defect) en zijn de twee varianten samen kandidaat-oorzakelijk. Liggen beide op hetzelfde allel (**in cis**), dan is het tweede allel nog intact en is de combinatie meestal een toevalstreffer. Het onderscheid trans/cis is dus beslissend, en CoGA velt daarom per paar een expliciet oordeel.

Omdat zo'n bevinding alleen betekenis heeft als de twee "hits" *samen* worden beoordeeld, groepeert CoGA kandidaat-varianten als **paar** in plaats van ze als losse rijen te tonen. In de resultatenlijst verschijnen ze bovenaan als pair-cards met het label "Compound het pair", elk met de fase-status (in trans / in cis / onbekend) van de twee partnervarianten. Zo beoordeelt de analist beide varianten in het gen als één geheel.

De backend leidt het paar af via segregatie in de familie. Een paar wordt gevormd wanneer beide varianten heterozygoot zijn in **álle** aangedane familieleden en **niet beide** aanwezig zijn in enig niet-aangedaan lid — die combinatie impliceert dat de twee varianten van verschillende ouders komen en dus in trans liggen. Waar fasering beschikbaar is (een gedeelde phase set) verfijnt die het trans/cis-oordeel rechtstreeks. De paarvorming zit in `_records_form_compound_het_pair` en `_compound_het_pairs`; het aparte endpoint dat de kandidaat-paren oplevert is `get_family_small_variant_compound_het_candidates`.

Een bijzondere vorm is de **cross-type "second hit"**: een heterozygote Small Variant gecombineerd met een overlappende structurele variant (bijvoorbeeld een deletie) in hetzelfde gen. De deletie verwijdert de tweede genkopie en "unmaskt" daarmee de heterozygote SNV op het overgebleven allel — functioneel opnieuw biallelisch. De filter `require_sv_second_hit` beperkt de resultaten tot genen waar zo'n SV-tweede-hit bestaat; de trans/cis-logica hiervan staat beschreven in `docs/snv-sv-compound-het.md`.

**Waar in de code:** frontend `frontend/src/pages/families/SmallVariantPairCards.tsx` (de pair-cards) en `frontend/src/pages/families/smallVariantSearch.ts` (`group_type: 'compound_het'`), met de fase-status-labels in `frontend/src/pages/families/smallVariantResultUtils.ts` (`formatCompoundHetPhaseStatus`). Backend: `_records_form_compound_het_pair` / `_compound_het_pairs` in `backend/app/services/clickhouse_variant_queries.py` (toegepast vanuit `get_family_compound_het_candidates` in `clickhouse_family_variants.py`), het endpoint `get_family_small_variant_compound_het_candidates` in `backend/app/routers/families_small_variants.py`, en de SV-tweede-hit-index in `backend/app/services/sv_gene_index_service.py`.

### Expanded Carrier Screening (ECS)

Expanded Carrier Screening is een *reproductieve* toepassing: geen zoektocht naar de oorzaak bij een patiënt, maar een draagerschapsscreening voor een **koppel** dat een kinderwens heeft. De vraag is niet "welke variant maakt deze persoon ziek", maar "lopen deze twee partners samen risico op een aangedaan kind". Voor autosomaal-recessieve aandoeningen is dat risico er wanneer **beide** partners drager zijn van een (heterozygote) kwalificerende variant in **hetzelfde** gen. CoGA toont daarom enkel de genen waar beide partners zo'n variant dragen; alle overige varianten zijn voor deze vraag irrelevant.

Praktisch verloopt dit via een filter-preset "Expanded carrier screening" op de Small-Variants-filterpagina. De preset is alleen beschikbaar wanneer de frontend een koppel kan afleiden: `resolveCarrierScreeningCoupleMembers` identificeert de twee partners binnen de familie, en bij inschakelen zet het formulier de parameter `expanded_carrier_screening=true`. De backend retourneert vervolgens alleen genen waar beide partners aan de dragercriteria voldoen.

**Waar in de code:** frontend `frontend/src/pages/families/smallVariantSearch.ts` (`resolveCarrierScreeningCoupleMembers` en de queryparameter) en `frontend/src/pages/families/SmallVariantFilterForm.tsx` (de preset-tegel). Backend: de endpoint-parameter `expanded_carrier_screening` in `backend/app/routers/families_small_variants.py`, het filterveld in `backend/app/services/family_variant_filters.py`, en de eigenlijke logica `_filter_expanded_carrier_screening` (met de koppel-partners via `_carrier_partner_names`) in `backend/app/services/clickhouse_variant_queries.py`, toegepast vanuit `clickhouse_family_variants.py`.

Het verschil met compound-het is subtiel maar belangrijk. Beide draaien om "twee hits in één gen", maar met een ander subject en doel: bij **compound-het** zitten de twee hits bij *één* individu (biallelisch → ziekte in die patiënt), terwijl het bij **ECS** gaat om één drager-variant bij *elk* van twee partners in hetzelfde gen (een reproductief risico voor het toekomstige nageslacht, niet voor de partners zelf).

## Structural variants

De structurele-variantpagina volgt exact hetzelfde stramien, met eigen bestanden en enkele wezenlijke verschillen door de aard van SV's (ze hebben een start én een end, een type als DEL/DUP/INV, en géén per-transcript annotatie-indexen zoals SNV's).

**Frontend.** `frontend/src/pages/families/FamilyStructuralVariantsPage.tsx` gebruikt de hook `useStructuralVariantSearchState` (uit `frontend/src/pages/families/structuralVariantSearch.ts`) en het formulier `StructuralVariantFilterForm.tsx`. De filtergroepen omvatten onder meer: type (DEL/DUP/…), `length` en `min_length`, `source`, `region_flags`, populatiefrequentie-drempels (`max_control_af`, `max_population_af`), `min_pli`, fenotype-velden (`phenotype`, `hpo`, `moi`, `gencc_support`), gen/`panel_id`, `inheritance`, en per-sample genotype/kwaliteit. Merk op dat de SV-pagina náást de gefilterde query een tweede, filterloze aanvraag doet (`page=1&page_size=1`) om het "All variants"-totaal te tonen.

**Endpoint.** `GET /{family_id}/structural-variants` in `backend/app/routers/families_structural_variants.py` → service `get_family_structural_variants_page`. Er bestaat daarnaast een sample-gerichte variant `GET /{sample_id}` in `backend/app/routers/structural_variants.py` (voor de SV's van één individu), die intern dezelfde service aanroept met `samples=[sample_context.sample_id]`.

**Service en query.** `get_family_structural_variants_page` (in `clickhouse_family_variants.py`) bouwt een `StructuralVariantQueryFilters` en gebruikt `_structural_variant_where_clauses` (in `clickhouse_variant_queries.py`) voor de basis-WHERE — opnieuw met `family_guid`, `sign = 1`, `project_guid IN …`, de sample-zichtbaarheidscontrole, chromosoom en positievenster, alles geparametriseerd. De rij-ophaal `_fetch_structural_variant_rows` joint links de `variants/details`-tabel voor de annotatie-JSON.

De belangrijkste **verschillen t.o.v. small variants**:

| Aspect | Small variants | Structural variants |
| --- | --- | --- |
| Native vs. Python | veel native paden | `_can_use_structural_native_page`; anders **eerst ophalen, dan in Python filteren** met een harde cap (`_SV_NON_NATIVE_STRUCTURAL_CANDIDATE_CAP = 50000`) — boven de cap is `total` een schatting (`total_is_estimated`) |
| Annotatie-index | aparte annotatie-index-tabellen | geen; matching op annotatie gebeurt in Python via `_structural_record_matches` |
| Cytoband | n.v.t. | `_fetch_structural_cytoband_map` leest de banden uit de Postgres-tabel `chromosomes` (kolom `bands`) |
| Review | `small_variant_review_pg.py` | `structural_variant_review_pg.py` (`get_structural_variant_review_map`) |
| Track-modus | idem | `track_mode` levert versmald JSON terug voor de genoombrowser, zonder review/cytoband |

**SV↔SNV "second hit".** De koppeling tussen beide werelden loopt via `backend/app/services/sv_gene_index_service.py`. Bij de eerste behoefte wordt per familie een index `gen → [SV's]` opgebouwd (`store_sv_gene_index`, in de Postgres-tabellen `family_sv_gene_index` en `family_sv_gene_index_status`) door een ClickHouse-scan. De SNV-pagina gebruikt die index tweeledig: als *badge* (`sv_second_hit`, met een trans/cis-verdict via `summarize_second_hit`) en als *filter* (`require_sv_second_hit`, dat via `get_sv_hit_genes` de SV-gerelateerde genen als extra beperking oplegt). Het ontwerp en de trans/cis-logica staan beschreven in `docs/snv-sv-compound-het.md`.

**Weergave.** `StructuralVariantResults.tsx` toont de tabel, de type/bron-samenvatting (`StructuralVariantSummaryTable.tsx`), CSV-export en dezelfde `ResultsPagination`.

## Veiligheid & traceerbaarheid in dit subsysteem

Elk van de bovenstaande query's is **project- en familie-gescoped**, en die scoping (afbakening) is niet optioneel maar zit in de gedeelde WHERE-bouwers:

- **Toegangscontrole aan de poort.** Elk endpoint hangt aan `Depends(get_current_user)` (of `Depends(get_current_admin_user)` voor uploads en tag-beheer), en roept `build_family_metadata_context` aan, dat verifieert dat de ingelogde gebruiker deze familie mag zien. De frontend-axios-client (`frontend/src/lib/api.ts`) stuurt daartoe altijd het bearer-token mee. Zie [hoofdstuk 2](02-beveiliging-rollen-rechten.md) voor de details.
- **Verplichte scoping in élke query.** `_small_variant_where_clauses` en `_structural_variant_where_clauses` beginnen altijd met `e.family_guid = %(family_guid)s` en voegen `e.project_guid IN %(project_ids)s` toe uit de context. Bovendien filtert `hasAny(e.calls.sampleId, %(visible_sample_ids)s)` op de voor de gebruiker zichtbare samples; is die lijst leeg, dan wordt de clausule letterlijk `"0"` toegevoegd (waardoor de query gegarandeerd nul rijen teruggeeft). Een gebruiker kan dus geen data zien van een familie, project of sample waartoe hij geen recht heeft — ook niet door parameters te manipuleren.
- **Injectiebescherming.** Gebruikerswaarden gaan uitsluitend als `%(naam)s`-parameters de query in; tabelnamen worden via `_require_clickhouse_identifier` gevalideerd. Pagina- en paginagroottes worden geklemd (`Query(..., le=MAX_VARIANT_PAGE_SIZE)`, `_clamp_small_variant_page`), en te zware query's worden als HTTP 422 teruggegeven i.p.v. de server te belasten.
- **Provenance & reproduceerbaarheid.** De ranking-cache is per-input gehasht, zodat een getoonde rangschikking altijd consistent is met exact die inputs (HPO, pedigree, panelversie, Monarch-release, algoritmeversie); annotaties en review-status worden bij elke weergave vers opgehaald zodat de cache nooit een verouderde klinische status kan tonen. De CSV-export gebruikt exact dezelfde filter-dependency als het scherm, zodat de download overeenkomt met wat de analist zag, en cellen worden ontdaan van formule-injectie via `csv_safe_cell` (in `backend/app/core/csv_export.py`).

Hiermee is de keten voor small en structural variants volledig: van filterveld tot geverifieerde, project-gescopede weergave. In deel B van dit hoofdstuk volgt dezelfde analyse voor de mitochondriale, Paraphase-, TRGT-, NIPT- en PGT-modules, gevolgd door de gezamenlijke "Belangrijkste bestanden"-tabel.

## Mitochondriaal (mtDNA) + Sample QC

### Wat de mtDNA-pagina oplost

Mitochondriaal DNA (mtDNA, het kleine circulaire genoom van 16.569 basen in de mitochondriën) erft uitsluitend via de moeder over en komt in elke cel in vele kopieën voor. Daardoor is een variant niet gewoon "aan/uit": hij kan in een deel van de kopieën zitten (**heteroplasmie**) of in bijna alle (**homoplasmie**). De pagina toont per variant de allelfractie (VAF, het percentage kopieën dat de variant draagt) per familielid, en groepeert moeders en kinderen samen tegenover de vader (die zijn mtDNA niet doorgeeft).

**Waar in de code:** frontend `frontend/src/pages/families/FamilyMitoDNAAnalysisPage.tsx`; endpoint `GET /families/{family_id}/mitochondrial-dna` in `backend/app/routers/families_tracks.py` (functie `get_family_mitochondrial_dna`), die doorschakelt naar `get_family_mitochondrial_analysis_response` in `backend/app/services/mitochondrial_analysis.py`.

### Van filter naar weergave

De mtDNA-varianten leven in dezelfde ClickHouse-variantopslag als de gewone Small Variants; de service haalt ze op met een filter op chromosoom `MT` via de gedeelde leeshelper `_fetch_small_variant_rows`. Er is dus geen aparte tabel, maar wel een aparte interpretatielaag:

- **Heteroplasmie/VAF-classificatie** — de functie `_zygosity` in `mitochondrial_analysis.py` bepaalt per call `homoplasmic` (VAF ≥ 95%, constante `HOMOPLASMY_THRESHOLD`), `heteroplasmic` (VAF ≥ 2%, `HETEROPLASMY_THRESHOLD`), `low_level` of `reference`. De VAF komt uit het VCF-veld `AF` of, bij ontbreken, uit de verhouding alt-reads/leesdiepte (`_allele_fraction`).
- **Locus-annotatie** — elke positie wordt via de vaste tabel `MT_LOCI` (in het servicebestand) aan een mitochondrieel gen/regio gekoppeld (`tRNA`, `rRNA`, `protein coding`, `control region`), plus een klinische betekenis (`_clinical_significance`) en een MITOMAP-zoeklink. MITOMAP is de referentiedatabank voor mitochondriële varianten.
- **Maternale-lijn-controle** — `_maternal_transmission` classificeert of een variant maternaal wordt gedeeld (`maternal_shared`), alleen bij de moeder zit (`maternal_only`), alleen bij de vader (`father_only`, verdacht want mtDNA erft niet paternaal over), enzovoort. De UI biedt hiervoor de filterknop "Maternal review".

De frontend voegt daar client-side filters aan toe (zoektekst, gnomAD-frequentiedrempel, synonieme varianten verbergen, scope "Clinical / Heteroplasmic / Homoplasmic / Maternal review"). Het combineert mtDNA met het **nucleaire mito-genpanel** doordat een mtDNA-variant naar een `SmallVariant`-vorm wordt vertaald (`toSmallVariantForAcmg`) en dezelfde ACMG-classificatiemodal en dezelfde review-endpoint gebruikt als de Small Variants — zie hoofdstuk [10 — Tagging & ACMG](10-tagging-en-acmg-classificatie.md). De heteroplasmie- en maternale-transmissiecontext wordt daarbij als `mito`-context meegegeven aan de ACMG-evaluator.

**Waar in de code:** review-koppeling via `_attach_reviews` (mtDNA-service) en `get_small_variant_review_map`; VAF-drempels als constanten bovenaan `mitochondrial_analysis.py`.

### Coverage en QC per sample

Naast varianten toont de pagina per sample de haplogroep, gemiddelde leesdiepte, breedte van dekking en een QC-status. Coverage komt uit de ClickHouse interval-tracks (`_coverage_by_sample`), met terugval op de leesdiepte uit de variant-calls als er geen coverage-track is. `_sample_qc` zet dit om in een status: `fail` bij een contaminatieschatting ≥ 3%, `warning` bij een verhoogde contaminatie (≥ 1%) of bij een gemiddelde diepte < 50x. Bij een `count_only`-aanvraag wordt alleen goedkoop gecheckt of er data is (`_has_mt_coverage` gebruikt een `LIMIT 1`-probe), zodat het familie-dashboard de mtDNA-tegel kan tonen zonder de volledige track te streamen.

### Sample QC: verwantschap, geslacht en sample-swaps

De **Sample-integriteit-QC** is een aparte, kritische pagina die controleert of de juiste sequentiedata aan de juiste persoon hangt. In een stamboomgedreven pijplijn is de stilste fout een verwisseld sample (sample-swap) of een verkeerd geregistreerde verwantschap: de varianten kloppen dan wel, maar worden aan de verkeerde persoon toegeschreven, waardoor de overervingsredenering ongemerkt fout gaat.

Drie referentievrije controles (dat wil zeggen: ze hebben geen externe referentie nodig, ze rekenen puur op de genotypes zelf) draaien op de genotypes:

| Controle | Wat wordt gemeten | Waar |
| --- | --- | --- |
| **Geslachtsconcordantie** | Uit het genotype afgeleid geslacht (heterozygotie op chromosoom X) versus het geregistreerde geslacht | `infer_sex` / `_evaluate_sex` |
| **Verwantschap** | KING-robuuste verwantschap (kinship) + IBS0 tussen sample-paren, vergeleken met wat de stamboom beweert (ouder-kind / broer-zus / onverwant) | `king_relatedness`, `classify_relatedness` |
| **Mendeliaanse foutgraad** | Aandeel sites waar het kind een genotype heeft dat de ouders niet kunnen voortbrengen | `mendelian_stats`, `_mendelian_check` |

Elke controle geeft een status `pass`/`warn`/`fail`/`skip`; de slechtste bepaalt de totaalstatus (`_worst`). Een geslacht dat niet klopt levert bijvoorbeeld expliciet de boodschap "possible sample swap or mislabel".

**Waar in de code:** de zuivere reken-kern (unit-testbaar, geen invoer/uitvoer naar databanken) staat in `backend/app/services/sample_integrity_qc.py`; de laadlaag die genotypes uit ClickHouse haalt en de stamboom oplost in `backend/app/services/sample_integrity_service.py` (`get_family_sample_integrity_qc`); endpoint `GET /families/{family_id}/qc/sample-integrity` in `backend/app/routers/families_reports.py`; frontend `frontend/src/pages/families/FamilySampleQcPage.tsx`.

### Toepassingsprofielen: elk datatype andere controles

Een generieke QC zou fout zijn, omdat CoGA verschillende toepassingen met verschillende inputmodaliteiten draait. `resolve_application` leidt de toepassing af uit het analysetype en de familievorm, en `profile_for` bepaalt welke controles draaien:

- **wgs** (WGS-familie) — geslacht, verwantschap én Mendeliaans.
- **pgt** (shallow-WGS met geïmputeerde genotypes) — geslacht plus de eis dat elk embryo een echt eerstegraads kind van beide ouders is.
- **nipt** — geen genotype-verwantschap (het cfDNA is een mengsel), maar **vaderschap uit de cfDNA-classificatie** (categorieën 7/8), plus foetale geslachtscontrole en een controle op de categorieverdeling. Zie de NIPT-sectie hieronder.
- **couple** (dragerpaar, BEGECS) — geslacht per partner; het paar wordt geacht onverwant te zijn (de verwantschapscheck flagt consanguïniteit).
- **single** (enkel sample) — alleen geslacht.

De NIPT-specifieke controles (`evaluate_paternity`, `evaluate_fetal_sex`, `evaluate_nipt_category_qc`) draaien via `_nipt_checks` in `sample_integrity_service.py`, dat de NIPT-analyse hergebruikt.

### Weergave en traceerbaarheid

De frontend rolt per sample de deelcontroles op tot één ring-status op de stamboomtekening (`buildQcStatusBySample`) en toont een verwantschapsmatrix met kinship (φ) en IBS0, gekleurd per afgeleide relatie. Rood omlijnde cellen zijn paren waarvan de waargenomen relatie de stamboom tegenspreekt.

**Veiligheid & traceerbaarheid:** Sample-QC is een gate vóór interpretatie. De degradatie is bewust "zichtbaar in plaats van stil": als genotypes of de cfDNA-analyse niet laden, faalt de pagina niet met een 500-fout, maar verschijnt een `warning` met een notitie (zie de `except`-takken in `sample_integrity_service.py`, met `scrub_log` om gevoelige waarden uit de logs te houden). QC-bevindingen werken door naar de rapport-gating: een `fail` moet worden opgelost vóór een rapport wordt ondertekend — zie hoofdstuk [11 — Rapport & traceerbaarheid](11-rapport-en-traceerbaarheid.md).

## Paraphase: moeilijke, paraloge genen

### Wat Paraphase oplost

Sommige medisch belangrijke genen liggen in gedupliceerde genomische regio's met bijna-identieke kopieën (paralogen/pseudogenen), zoals *SMN1/SMN2* (spinale musculaire atrofie) of *PMS2*. Standaard variant-calling faalt daar, omdat reads niet eenduidig aan één kopie zijn toe te wijzen. **Paraphase** is een externe tool (die vóór CoGA draait) die deze regio's als geheel fenotypeert: kopieaantallen (copy number, CN), read-tellingen per allel-onderscheidende positie en samengestelde haplotypes.

CoGA slaat die resultaten op in de Postgres-tabel `sample_paraphase_results` (kolommen o.a. `gene_symbol`, `total_cn`, `gene_cn`, `highest_total_cn`, en een JSON-`payload` met alle ruwe velden).

### Van tabel naar klinische duiding

De service leest de rijen per sample en verrijkt ze met een **medische-regiocatalogus** uit het JSON-bestand `data/ref-data/paraphase-medical-regions.json` (`load_paraphase_medical_regions`; 14 regio's). Die catalogus bepaalt welke velden klinisch relevant zijn per locus, welke disorders eraan hangen, en — belangrijk — een **status_rule**: een regel die uit een CN-metriek automatisch `normal`/`carrier`/`pathogenic` afleidt (`_clinical_status`, `_condition_met`). Voor loci zonder automatische regel wordt elk niet-baseline-signaal als `review` gemarkeerd.

De service groepeert per gen, sorteert de klinisch relevante loci en de loci met CN-signaal bovenaan, en levert per sample de metrieken (`_extract_copy_number_metrics`, `_extract_read_metrics`, `_extract_haplotype_groups`, `_extract_extra_fields`). Een `count_only`-pad telt alleen de distincte genen voor de dashboard-tegel.

**Waar in de code:** `backend/app/services/paraphase_pg.py` (`get_family_paraphase_table_response`); endpoint `GET /families/{family_id}/paraphase` in `backend/app/routers/families_tracks.py`; frontend `frontend/src/pages/families/FamilyParaphasePage.tsx`.

### Weergave

De frontend toont per locus een kaart met de klinische interpretatie, gekoppelde disorders (OMIM-links) en per sample een statuslabel (`Pathogenic`/`Carrier`/`Normal`/`Review`/`No-call`) met de klinisch gemarkeerde CN-metrieken. Een "Extra info"-knop opent de volledige, locus-agnostische technische uitlezing (haplotypegroepen, read-metrieken, fase-regio). Filters: zoeken op gen, "Copy-number changes only" en de scope "Only clinical / Show all". Een "Chromosome view"-link opent de geanalyseerde regio in de chromosoomweergave (hoofdstuk [9 — Visualisaties](09-visualisaties.md)).

## TRGT repeat-expansies

### Wat de repeat-pagina toont

Repeat-expansies zijn ziekten waarbij een kort DNA-motief (bijv. `CAG` in *HTT* bij de ziekte van Huntington) te vaak wordt herhaald. **TRGT** (Tandem Repeat Genotyper) is de externe caller die per locus de allel-lengtes bepaalt. CoGA toont per locus de familiecalls met een driekleurige status: grijs = normaal, oranje = grijze zone/intermediair, rood = pathogeen.

### Ingest (met traceerbaarheid)

De TRGT-output is een VCF; de import parseert per regel de repeat-velden (`AL` = allel-lengte in basen, `MC` = motief-kopieaantallen, `AP`/`AM` = puriteit/methylatie, enz.) en resolveert elk locus tegen de **catalogus** in de Postgres-tabel `repeat_loci`. Die catalogus wordt geseed uit twee bronnen: een ingebouwde lijst `BUILTIN_REPEAT_LOCI` (o.a. *HTT*, *FMR1*, *DMPK*, met `warning_min`/`pathogenic_min`-drempels) en het STRchive-referentiebestand `data/ref-data/STRchive-loci.json` (`seed_builtin_repeat_catalog` seeded beide). Elke allel krijgt een status via `classify_repeat_count` (≥ `pathogenic_min` → `pathogenic`, ≥ `warning_min` → `intermediate`, anders `normal`).

De calls worden opgeslagen in de Postgres-tabel `repeat_expansions`. Bij een familie-upload wordt bovendien de **TRGT-caller-versie uit de VCF-header** vastgelegd in het annotatie-manifest van de familie (`merge_vcf_header_provenance`, modaliteit `repeats`) — provenance die in het rapport terugkomt.

**Waar in de code:** upload-router `backend/app/routers/repeat_expansions.py` (`POST /repeat-expansions/upload/{sample_id}`, admin-only via `get_current_admin_user`); ingest en tabelweergave in `backend/app/services/repeat_expansion_pg.py` (`ingest_trgt_text`, `ingest_family_trgt_text`, `get_family_repeat_expansion_table_response`); catalogus in `backend/app/services/repeat_expansion_catalog.py`.

### Van API naar weergave

De familie-tabel-endpoint `GET /families/{family_id}/repeat-expansions` (in `families_tracks.py`) leest de opgeslagen calls, herclassificeert de allelen tegen de actuele catalogusdrempels (via een `LEFT JOIN LATERAL` op `repeat_loci`, zodat een later bijgewerkte drempel meteen doorwerkt) en groepeert per locus. Een aparte per-sample track-endpoint `GET /families/{family_id}/repeat-expansions/sample/{sample_id}` levert de allel-lengtes voor de chromosoomweergave. De frontend (`FamilyRepeatExpansionsPage.tsx`) toont de tabel met per familielid de allel-repeat-tellingen, drempels ("orange ≥ … · red ≥ …"), OMIM-links en filters op gen/ziekte plus "Aberrant only".

Een detail met klinische betekenis: voor een X-gebonden locus bij een mannelijk sample wordt het tweede (fantoom-)allel weggelaten (`_normalize_x_male_alleles`), omdat mannen hemizygoot zijn op X (één X-chromosoom, dus maar één echt allel).

## Monogene NIPT

### De klinische vraag

Monogene NIPT (niet-invasieve prenatale test) screent een zwangerschap op enkelgen-aandoeningen uit **celvrij DNA (cfDNA) in maternaal plasma**, gekruist met een vaderlijk sample. Het plasma-cfDNA is een **mengsel**: overwegend maternaal DNA met een kleine **foetale fractie (FF)**. De foetus wordt nooit direct gesequenced; zijn genotype wordt afgeleid uit hoe ver de allelfractie in het cfDNA afwijkt van de zuivere maternale verwachtingen (0%, 50%, 100%), in verhouding tot de FF.

Het datamodel is een **trio (vader, moeder, foetus) met slechts twee fysieke samples**: de vaderlijke germline-VCF en de maternale-plasma-cfDNA (geüpload als het sample van de moeder). De foetus is een placeholder-node zonder eigen data.

**Waar in de code:** domeinmodel en trio-resolutie in `backend/app/services/nipt.py` (`resolve_nipt_trio`; een familie is NIPT wanneer `families.metadata.analysis_type = "monogenic_nipt"` en het cfDNA-sample `assay = "nipt_cfdna"` draagt — constanten `MONOGENIC_NIPT_ANALYSIS_TYPE` en `NIPT_CFDNA_ASSAY`). Ontwerp: `docs/monogenic-nipt.md`.

### Het categoriemodel en de foetale fractie

Voor een biallelisch site is de verwachte cfDNA-VAF `VAF = m·(1−FF) + f·FF`, met `m` en `f` de maternale en foetale alt-kopiefracties (0, ½, 1). Dat levert acht categorieën (`_CATEGORY_AXES`, `_expected_vaf` en `_CATEGORY_LABELS` in `nipt_analysis.py`):

| Cat | Maternale/foetale toestand | Verwachte VAF |
| --- | --- | --- |
| 1 | de novo in foetus | FF/2 (laag) |
| 2 | moeder het, foetus erft niet | 50% − FF/2 |
| 3 | moeder het, foetus het | 50% |
| 4 | moeder het, foetus hom | 50% + FF/2 |
| 5 | moeder hom, foetus het | 100% − FF/2 |
| 6 | moeder hom, foetus hom | 100% |
| 7 | afwezig bij moeder, van vader → doorgegeven | FF/2 |
| 8 | vader hom-alt, afwezig in cfDNA (vals-negatief, QC-signaal) | ~0 |

De **foetale fractie** wordt geschat als `2 × (Σ alt-reads / Σ diepte)` over categorie-7-sites (vader draagt, moeder hom-ref, aanwezig in cfDNA op een schone `FF/2`), met een Wilson-betrouwbaarheidsinterval en een per-site-mediaan als kruiscontrole. Een eventuele externe FF wordt vastgelegd en bij afwijking gemarkeerd, maar overschrijft de berekende schatting niet standaard (alleen wanneer `prefer_external` is gezet). Categorie 8 is het complementaire vals-negatief-signaal.

Elke variant wordt daarna geclassificeerd met een **beta-binomiale likelihood** tegen de FF-bepaalde centra (`classify_site`, `_log_beta_binom`), met een softmax-vertrouwensscore (`_softmax`). Een zeldzame de-novo (categorie 1) krijgt een lage prior zodat hij sterk bewijs nodig heeft.

**Waar in de code:** de zuivere reken-kern (geen invoer/uitvoer naar databanken) in `backend/app/services/nipt_analysis.py` (`estimate_fetal_fraction`, `classify_site`, `run_nipt_analysis`, plus foetaal geslacht via `infer_fetal_sex`).

### Wiring: endpoint → service → data

`run_family_nipt_analysis` in `backend/app/services/nipt_service.py` resolveert het trio, laadt de gekoppelde vader- en cfDNA-calls uit de ClickHouse-variantopslag (de `entries`-tabel, via de gedeelde `_fetch_small_variant_rows`), bouwt per site een `NiptSiteObservation` en draait de kern. Twee filters lopen ervoor: een **kwaliteitsfilter** (diepte/QUAL) en een **recurrent-artefact-filter** (panel-of-normals), met tellingen zodat de UI de trechter "total → quality-filtered → artifact-filtered → analysed" kan tonen.

De artefactlijst staat in de Postgres-tabel `nipt_artifact_variants`, gescoped per `(assembly_id, assay_key)` — omdat recurrente artefacten capture-/chemie-specifiek zijn (`backend/app/services/nipt_artifact_pg.py`; `load_nipt_artifact_ids` levert de snelle lidmaatschapsset, met auto-seed uit cohort-recurrentie via `fetch_recurrent_small_variant_ids`).

Endpoints in `backend/app/routers/families_nipt.py`:

- `GET /{family_id}/nipt/summary` — FF, categorie-tellingen, filter-tellingen.
- `GET /{family_id}/nipt/variants` — geclassificeerde varianten. Hergebruikt de volledige Small-Variant-filterset (`_build_nipt_query_filters` → `SmallVariantQueryFilters`) plus NIPT-specifieke filters `category`, `min_confidence` en een overervingspreset `inheritance` (`de_novo`/`paternal_dominant`/`maternal_dominant`/`recessive_at_risk`). De recessieve preset (`_recessive_at_risk_variants`) groepeert de kandidaten per gen: een gen is at-risk wanneer beide ouders er een dragervariant hebben.
- `GET /{family_id}/nipt/coverage` — on-target dekking per doelregio (gen/panel/ROI) en overall mediaan, met een QC-vlag voor genen die onvoldoende geïnterrogeerd zijn.

**Waar in de code:** dekkingskern in `backend/app/services/nipt_coverage.py` (lengte-gewogen mediaan, `evaluate_low_coverage`); doelregio-resolutie in `nipt_service.py` (`_resolve_nipt_target_regions`).

### Weergave

De hoofdpagina toont een FF-gauge (met CI en het aantal cat-7-sites), de filtertrechter, de categorie-tellingen en de on-target-dekkings-QC, met daaronder de hergebruikte Small-Variant-resultatenlijst waarin per variant een NIPT-classificatieblok verschijnt. Het **rapport** groepeert de kandidaten per afgeleide overerving en draagt een verplichte disclaimer dat NIPT-classificaties beslissingsondersteuning uit cfDNA zijn die met een invasieve diagnostische test bevestigd moeten worden.

**Waar in de code:** `frontend/src/pages/families/FamilyNiptPage.tsx`, `frontend/src/pages/families/FamilyNiptReportPage.tsx`, en het classificatieblok `frontend/src/pages/families/NiptClassificationBlock.tsx`. Merk op dat de pagina zich afschermt: is de familie niet als `monogenic_nipt` getagd, dan verschijnt een "Not a monogenic NIPT family"-toestand.

## PGT haplotype-segregatie en ROI-markers

### De klinische vraag

Bij preïmplantatie-genetische testing (PGT) produceert een paar (of één ouder + donor) embryo's, en een bekende ziekte segregeert in één of beide families. Per embryo is de vraag: erfde het de ziekte-haplotype(s)? CoGA kleurt elk individu's twee haplotypes naar de grootouderlijke stamvader-homolog waarvan ze afstammen (identity-by-descent, IBD — gedeeld doordat het van een gemeenschappelijke voorouder komt), en identificeert welk stamvader-haplotype het ziekte-allel draagt.

**Waar in de code:** ontwerp in `docs/haplotype-segregation-analysis.md`. Endpoints in `backend/app/routers/families_tracks.py`: `GET /{family_id}/haplotypes`, `/haplotypes/batch` en `/phased-markers`, elk via `family_service` (`get_family_haplotypes_for_user`, `get_family_haplotypes_batch_for_user`, `get_family_phased_markers_for_user`).

### Twee lagen: gekleurde blokken en ruwe markers

De track tekent twee complementaire lagen:

1. **Gekleurde haplotype-blokken** (de gepolijste interpretatielaag) — per lid twee lanen (de twee homologen); een blok is een stuk chromosoom dat van één stamvader-haplotype afstamt en herkleurt bij elke recombinatie.
2. **Ruwe phased-marker-overlay** (de diagnostische laag) — één punt per informatief geïmputeerd site, **zonder binning, smoothing of stemmen** (geen middeling of meerderheidsbesluit over naburige markers). Juist omdat hij ruw is, legt hij bloot wat de blokken verbergen: geïsoleerde fase-switches, jitter aan recombinatiegrenzen en de exacte crossover-positie. Dit is een bewuste ontwerpkeuze (vastgelegd in de projectmemory: "Phased imputed markers stay raw").

De kleurcode: donker-/lichtblauw = paternale stamvader-homolog 0/1, donker-/lichtgroen = maternaal, grijs = untransmitted/onbekend. De absolute donker-vs-licht is willekeurig (uit de ruwe fasering); wat telt is **consistentie** — dezelfde fysieke grootouder-haplotype houdt dezelfde tint door de hele familie.

### IBD-stamvaderkleuring

De opgeslagen haplotype-blokken (gebouwd bij upload) zijn alleen betekenisvol voor de **index-kernfamilie** (vader, moeder, hun directe kinderen/embryo's), want dat is wat de trio-fasering grondt. Relatieven (grootouder, oom/tante) zitten niet in die fasering, en hun opgeslagen blokken zijn biologisch betekenisloos. Erger: het platte rolmodel hergebruikt `mother`/`father` voor elke ouder, dus een paternale grootmoeder staat als `role = "mother"` opgeslagen — een naïeve kleurder zou haar volledig groen verven (dit is expliciet vastgelegd in de projectmemory "Haplotype lineage pedigree").

Daarom **herberekent CoGA elke relatief-kleur uit de ruwe phased-genotypes** en propageert stamvader-identiteit naar buiten door de stamboom (BFS — breadth-first search, laag voor laag langs ouder-kind-randen). Voor elke relatief die vanaf een reeds gekleurd lid bereikt wordt, worden zijn twee homologen via IBD gematcht tegen die van het lid; de gedeelde homolog erft de kleur, de andere wordt grijs. Zo krijgt een paternale grootmoeder exact één homolog gekleurd — precies wat toelaat af te lezen *welk* paternaal haplotype het dominante ziekte-allel draagt. De matching is recombinatie-bewust: een gedeeld haplotype wisselt van lane bij elke meiotische crossover, maar een switch wordt pas gecommit als een run van tegensprekende markers lang én breed genoeg is (`LINEAGE_SWITCH_MIN_MARKERS = 50`, `LINEAGE_SWITCH_MIN_SPAN = 500.000`), zodat echte crossovers de track splitsen maar ruis niet.

Een lid dat CoGA niet met vertrouwen kan plaatsen wordt **volledig grijs** — nooit fout gekleurd. Op geslachtschromosomen en mtDNA (waar de diploïde twee-homolog-aanname breekt) blijven relatieven bewust grijs.

**Waar in de code:** `backend/app/services/haplotype_lineage_service.py` (`annotate_lineage`: bouwt de stamboom, identificeert de embryo-verankerde kern via `identify_core`, propageert kleur per IBD, segmenteert per recombinatie, grijst niet-autosomen/onplaatsbare leden). Zuiver, zonder invoer/uitvoer naar databanken.

### Single-parent (donor) families

CoGA ondersteunt embryo's met slechts **één bekende ouder** (bijv. een alleenstaande vrouw of een donor-gameet), terwijl de aandoening in de familie van de bekende ouder segregeert (vastgelegd in de projectmemory "Single-parent analysis"). De kern wordt op de embryo's verankerd: de bekende ouder is de ouder van de embryo's, de donorzijde is simpelweg afwezig. De twee homologen van de bekende ouder zijn de stamvaders (één per grootouder), gekleurd door ze op te sporen tot de aangedane grootouder; de embryo's krijgen de van-de-bekende-ouder-afgeleide lane gekleurd en de donor-lane grijs.

**Waar in de code:** `identify_core` (met de terugval naar single-parent-data) en de single-parent-tak in `annotate_lineage`; de foutieve aanname "elke aandoening segregeert in een koppel" wordt hier bewust doorbroken.

### Ruwe phased markers (diagnostisch)

De marker-service levert per site de ruwe homolog-lane-waarden. Voor een kind: welke ouderlijke homolog (0/1) op elke zijde is geërfd, gemapt naar de weergegeven tint van het opgeslagen blok van de ouder (zodat marker en blok genoom-breed overeenstemmen). Voor een ouder: de allelen op zijn eigen twee homologen. Er is **geen binning**: geïsoleerde single-marker-switches blijven zichtbaar, want de overlay bestaat juist om faseringsruis te tonen.

De markers worden alleen berekend voor de **eigen kinderen van de index-ouders** (single-parent: de kinderen van de ene bekende ouder). De transmissielogica op een relatief draaien zou biologisch achterstevoren zijn en toevallige ruis geven; relatieven verschijnen wel (voor de tooltip) maar zonder marker-dots. Per kind rapporteert de service ook een QC: het aantal jointly-informative sites (`informative_sites`) en de **Mendel-foutgraad** (`mendel_errors`/`mendel_rate` — impossibele transmissies, een rode vlag voor sample-swap of verkeerde stamboom). Een fetch-truncatiegarde onderdrukt de overlay bij te veel sites ("zoom in").

**Waar in de code:** `backend/app/services/phased_marker_service.py` (`compute_phased_markers`, `get_family_phased_markers_response`), inclusief de single-parent-modus (`_transmitted_single_parent_haplotype`).

### Embryoclassificatie en de ROI-marker-pagina

De uiteindelijke **embryoclassificatie** (`affected_or_at_risk`, `carrier`, `unaffected_non_carrier`, `uninformative`) wordt aan de frontend afgeleid uit de gekleurde blokken over de region-of-interest (ROI). `haplotypeRisk.ts` leidt de ziekte-haplotype-signatuur af per overervingsmodel (`inferDiseaseHaplotypes`: dominant = de enige haplotype gedeeld door aangedane/obligate leden; recessief = een dragerhaplotype per zijde; X-gebonden = geslacht-bewust) en `interpretSampleHaplotypeRisk` classificeert elk embryo. Cruciaal: de pedigree-bewuste lineage-tags van de backend zijn **autoritatief boven de platte `role`** (`getHaplotypeLaneSignature`).

`embryoSegregation.ts` (`classifyEmbryosAtRoi`) voegt daar twee klinische waarschuwingen aan toe: `recombinationNearRoi` (een crossover binnen of vlak bij de ROI maakt de call onzeker) en `uninformative` (geen ziekte-haplotype resolveerbaar).

De **ROI-marker-pagina** (`FamilyRoiMarkersPage.tsx`) toont een leden × markers-rooster van de ruwe phased-genotypes over de ROI: per marker het nucleotide-allel boven een doorlopende lineage-gekleurde band, met de ROI omkaderd, flankerende markers gedimd, Mendel-fouten donkeroranje gemarkeerd en blok-mismatches (ruwe marker versus opgeschoond blok) lichteroranje. Het spiegelt de backend-Mendel-check client-side (`mendelianConsistent`) zodat de analist een verrassende embryo-call tegen de onderliggende data kan hertoetsen.

**Waar in de code:** `frontend/src/lib/haplotypeRisk.ts`, `frontend/src/lib/embryoSegregation.ts`, `frontend/src/pages/families/FamilyRoiMarkersPage.tsx`.

## Veiligheid & traceerbaarheid over alle datatypes

- **Project-scoping en toegang** — alle endpoints in dit hoofdstuk lopen via `get_current_user` (of `get_current_admin_user` bij de TRGT-upload) en bouwen hun data via `build_family_metadata_context(session, family_identifier=…, user=…, project_id=…)`. Die context dwingt af dat de gebruiker de familie in het opgegeven project mag zien; bij de haplotype-/phased-marker-endpoints gebeurt die contextbouw in de `family_service`-laag. Geen enkele service leest variant- of trackdata buiten die gecontroleerde context om. Zie hoofdstuk [02 — Beveiliging, rollen & rechten](02-beveiliging-rollen-rechten.md).
- **Provenance** — de TRGT-ingest legt de caller-versie uit de VCF-header vast in het annotatie-manifest; NIPT-artefacten zijn per assay gescoped en handmatig gecureerd of auto-geseed met bronvermelding (`source = curated | auto`).
- **Gate op het rapport** — Sample-QC-bevindingen (sample-swap, geslacht, Mendeliaans) zijn een expliciete voorwaarde vóór een rapport wordt ondertekend; hun degradatie is zichtbaar (`warning`) in plaats van stil. De doorwerking naar de rapport-gating staat in hoofdstuk [11 — Rapport & traceerbaarheid](11-rapport-en-traceerbaarheid.md).
- **Afgeleid, niet ingevoerd** — de NIPT-categorieën, de FF, de haplotype-kleuring en de embryoclassificatie zijn allemaal *berekend* uit de twee VCF's / de phased-genotypes en de stamboom, niet door een analist ingetypt. Dat maakt elke conclusie herleidbaar tot de invoerdata.

## Belangrijkste bestanden

Onderstaande tabel dekt heel hoofdstuk 8 (deel A én B).

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/families_small_variants.py` | Endpoints voor Small Variants — deel A |
| `backend/app/services/clickhouse_family_variants.py` | Gedeelde ClickHouse-leeslaag voor Small Variants (`_fetch_small_variant_rows`), hergebruikt door mtDNA en NIPT — deel A |
| `backend/app/services/family_variant_filters.py` | `SmallVariantQueryFilters`: de gedeelde filterset — deel A |
| `backend/app/routers/families_structural_variants.py` | Endpoints voor structurele varianten (SV) — deel A |
| `backend/app/routers/families_tracks.py` | Endpoints voor mtDNA, Paraphase, repeats, haplotypes en phased-markers |
| `backend/app/services/mitochondrial_analysis.py` | mtDNA-analyse: heteroplasmie/VAF, loci-annotatie, maternale transmissie, QC |
| `backend/app/services/sample_integrity_qc.py` | Zuivere QC-kern: geslacht, KING-verwantschap, Mendeliaans, NIPT-vaderschap/categorie |
| `backend/app/services/sample_integrity_service.py` | Laad-/wiringlaag: toepassingsprofiel, genotype-bron, degradatie tot warning |
| `backend/app/routers/families_reports.py` | Endpoint `qc/sample-integrity` (en rapport-gating) |
| `backend/app/services/paraphase_pg.py` | Paraphase: `sample_paraphase_results` + medische-regiocatalogus → klinische status |
| `data/ref-data/paraphase-medical-regions.json` | Klinische regiocatalogus voor Paraphase |
| `backend/app/routers/repeat_expansions.py` | Admin-upload van TRGT-repeats |
| `backend/app/services/repeat_expansion_pg.py` | TRGT-ingest + familietabel; `repeat_expansions` + `repeat_loci`-catalogus |
| `backend/app/services/repeat_expansion_catalog.py` | Ingebouwde repeat-loci met drempels (STRchive vult aan) |
| `backend/app/routers/families_nipt.py` | NIPT-endpoints: summary, variants, coverage |
| `backend/app/services/nipt.py` | NIPT-domeinmodel en trio-resolutie |
| `backend/app/services/nipt_service.py` | Wiring NIPT-kern aan ClickHouse-data + preset-/coverage-logica |
| `backend/app/services/nipt_analysis.py` | Zuivere NIPT-kern: FF-schatting, 8-categorie-classificatie, foetaal geslacht |
| `backend/app/services/nipt_coverage.py` | Lengte-gewogen on-target dekkings-QC |
| `backend/app/services/nipt_artifact_pg.py` | Per-assay recurrent-artefactlijst (`nipt_artifact_variants`) |
| `backend/app/services/haplotype_lineage_service.py` | Pedigree-IBD stamvaderkleuring, recombinatie-segmentatie, single-parent-kern |
| `backend/app/services/phased_marker_service.py` | Ruwe per-site phased markers + per-kind QC (informatieve sites, Mendel-fouten) |
| `frontend/src/pages/families/FamilyMitoDNAAnalysisPage.tsx` | mtDNA-weergave, filters en ACMG-koppeling |
| `frontend/src/pages/families/FamilySampleQcPage.tsx` | Sample-QC: stamboomringen, verwantschapsmatrix, NIPT-checks |
| `frontend/src/pages/families/FamilyParaphasePage.tsx` | Paraphase-locuskaarten en filters |
| `frontend/src/pages/families/FamilyRepeatExpansionsPage.tsx` | TRGT-repeattabel met drempels |
| `frontend/src/pages/families/FamilyNiptPage.tsx` | NIPT-dashboard: FF, trechter, coverage, variantlijst |
| `frontend/src/pages/families/FamilyNiptReportPage.tsx` | NIPT-rapport gegroepeerd per overerving |
| `frontend/src/pages/families/NiptClassificationBlock.tsx` | Per-variant NIPT-classificatieblok |
| `frontend/src/pages/families/FamilyRoiMarkersPage.tsx` | ROI-marker-rooster met Mendel-/blok-vlaggen |
| `frontend/src/lib/haplotypeRisk.ts` | Ziekte-haplotype-inferentie + embryoclassificatie per overervingsmodel |
| `frontend/src/lib/embryoSegregation.ts` | Embryo-ROI-classificatie + recombinatie-/uninformatief-waarschuwingen |
| `docs/monogenic-nipt.md` | Ontwerpreferentie monogene NIPT |
| `docs/haplotype-segregation-analysis.md` | Ontwerpreferentie PGT-haplotype-segregatie |
