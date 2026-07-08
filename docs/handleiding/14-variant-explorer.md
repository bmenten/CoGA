# 14. Variant Explorer

Dit hoofdstuk behandelt hoe CoGA een *variant-centrische* zoekfunctie aanbiedt die Small Variants (SNV's/indels) samenvat over **alle projecten waartoe een gebruiker toegang heeft**, in plaats van binnen één familie. Aan bod komen welke schermen de reviewer te zien krijgt, hoe een zoekopdracht wordt vertaald naar geaggregeerde ClickHouse-queries, hoe dragertellingen (heterozygoot/homozygoot/aantal families) worden berekend, en — het allerbelangrijkste voor een auditor — hoe strikt wordt afgedwongen dat resultaten nooit buiten de projectgrenzen van de gebruiker lekken.

Ter verduidelijking van enkele termen die telkens terugkomen:
- **SNV** (single-nucleotide variant) = puntmutatie; **indel** = kleine insertie/deletie.
- **Drager (carrier)** = een sample dat het alternatieve allel draagt (heterozygoot of homozygoot).
- **Aggregatie** = per unieke variant optellen hoeveel samples/families die variant dragen.
- **Assembly** = referentiegenoom-versie (bv. GRCh38); varianten van verschillende assemblies mag je niet door elkaar tellen.
- **Cursor / keyset-paginering** = een techniek om pagina voor pagina door een grote resultaatset te bladeren zonder dure `OFFSET`-sprongen.

## Doel & UI: cross-cohort in plaats van per familie

De klassieke filterpagina (zie [08-filterpaginas-en-api.md](08-filterpaginas-en-api.md)) beantwoordt de vraag *"welke varianten zitten in DEZE familie?"*. De Variant Explorer draait die vraag om: *"in hoeveel samples en families in mijn hele toegankelijke cohort komt DEZE variant voor?"*. Elke rij in het resultaat is één unieke variant (geïdentificeerd door de ClickHouse-`key`, een 64-bits geheel getal), met dragertellingen samengevat over het volledige cohort.

De reviewer ziet één pagina met bovenaan een keuzelijst voor de **assembly**, daaronder een **Sample–Genotype-filter** (per-sample-genotypefilters plus een schakelaar voor geïmputeerde varianten), vervolgens het gedeelde annotatiefilterformulier, en ten slotte een resultaattabel met per rij de tellers *Total samples / Het / Hom / Families*. Klikken op een tellerwaarde opent een drill-downvenster met de dragers gegroepeerd per familie.

Belangrijk detail voor de traceerbaarheid: de Variant Explorer **hergebruikt het bestaande filterformulier** van de familiepagina, maar in "familie-loze" modus.

**Waar in de code (frontend):**
- De pagina zelf: `GlobalSmallVariantExplorerPage` in `frontend/src/pages/variant-explorer/GlobalSmallVariantExplorerPage.tsx`. Deze rendert `SmallVariantFilterForm` met de prop `familyAware={false}`, waardoor de familie-/samplevelden verborgen worden.
- De zoek-state-hook: `useGlobalSmallVariantSearchState` in `frontend/src/pages/variant-explorer/globalSmallVariantSearch.ts` — de "familie-agnostische tegenhanger" die dezelfde query-string-serialisatie (`buildSmallVariantQueryParams`) hergebruikt maar de familie- en sample-handlers bewust als no-ops (lege functies) invult.
- De tabel en het dragervenster: `frontend/src/pages/variant-explorer/GlobalSmallVariantTable.tsx` en `frontend/src/pages/variant-explorer/VariantCarrierModal.tsx`.
- De TypeScript-typen (spiegel van de backend-schema's): `frontend/src/pages/variant-explorer/types.ts` (`GlobalVariantRow`, `GlobalVariantPage`, `VariantCarriers`).

De frontend praat via de gedeelde axios-client (`frontend/src/lib/api.ts`, standaard-basis-URL `/api`) met de endpoints onder `/variant-explorer/...`. De tekstuele locus-parser `parseGeneOrRegionInput` in `frontend/src/lib/variantSearch.ts` zet een invoer als `chr1:1000-2000` om in een gestructureerd gebied (`region`) of behandelt de invoer anders als gennaam; die parser wordt gedeeld met de familie-filtermodules.

## Endpoint & service: van zoekopdracht naar ClickHouse-aggregaat

De router definieert alle endpoints onder het voorvoegsel `/variant-explorer`.

**Waar in de code (router):** `backend/app/routers/variant_explorer.py`.

| Endpoint | Methode | Doel |
|---|---|---|
| `/variant-explorer/assemblies` | GET | Toegankelijke assemblies + aantal projecten per assembly |
| `/variant-explorer/samples` | GET | Sample-ID's die de gebruiker mag zien (voedt de genotypefilter-picker) |
| `/variant-explorer/small-variants` | GET | Gepagineerde, geaggregeerde variantlijst |
| `/variant-explorer/small-variants/export` | GET | Dezelfde resultaatset als CSV-download (tot 50 000 rijen) |
| `/variant-explorer/small-variants/{variant_key}/carriers` | GET | Drill-down: dragers gegroepeerd per familie |
| `/variant-explorer/small-variant-tags` | GET | Tag-definities binnen de toegankelijke projecten |

Elk endpoint hangt via `Depends(get_current_user)` de ingelogde gebruiker aan de aanvraag (zie [05-login-authenticatie.md](05-login-authenticatie.md)). De filterparameters worden gecentraliseerd geparseerd in `_build_global_variant_filters`, zodat de gepagineerde lijst én de CSV-export gegarandeerd dezelfde filters gebruiken.

**Waar in de code (service):** de eigenlijke logica staat in `backend/app/services/variant_explorer_service.py`. De hoofdfunctie is `search_global_small_variants`. Die:
1. normaliseert `sort`/`order` naar een toegelaten waarde (een onbekende `sort` valt terug op `total_samples`);
2. bepaalt de **scope** (assembly + toegankelijke projecten) via `resolve_scope`;
3. vertaalt eventuele tag-/classificatiefilters naar een lijst van `variant_id`-strings (Postgres-brug, zie verder);
4. bouwt de ClickHouse-`WHERE`-clausules met `_entries_where`;
5. voert een geaggregeerde telling en een gepagineerde rij-query uit tegen de `entries`-tabel (`_fetch_variant_rows`).

De ruwe variant-ID's en variant-keys worden opgebouwd met de hulpfuncties in `backend/app/services/clickhouse_variant_ids.py` (o.a. `build_small_variant_id`, `small_variant_key`, `_xpos`), en de rijen die tijdens import in ClickHouse worden geschreven, worden opgebouwd in `backend/app/services/clickhouse_variant_rows.py` (`_small_variant_entry_rows` voor de `entries`-tabel, `_small_annotation_index_row` voor de annotatie-index). Deze bestanden vormen samen het contract tussen wat bij import wordt weggeschreven en wat de Explorer later uitleest.

## Aggregatie: dragertellingen over projecten heen

De bron van waarheid is de tabel `entries`, niet een voorberekende samenvatting. Een vroegere `project_gt_stats`/`gt_stats`-cascade van "materialized views" (automatisch bijgewerkte hulptabellen) is bewust verwijderd omdat die het `sign`-kolomgedrag van de `CollapsingMergeTree`-engine negeerde en zo bij her-imports/deletes de tellingen opblies. Die legacy-tabellen worden uit bestaande databases weggehaald door `_drop_legacy_gt_stats_aggregates` in `clickhouse_variant_storage.py`. De Explorer telt daarom rechtstreeks uit `entries` met `sign = 1`.

De kern-aggregatiequery staat in `_fetch_variant_rows`. Hij "ontvouwt" de per-variant opgeslagen genotype-arrays met `ARRAY JOIN` en telt vervolgens per variant-`key`:

```sql
uniqExact(family_guid)                    AS families
uniqExactIf(sample_id, is_hom)            AS hom_samples
uniqExactIf(sample_id, NOT is_hom)        AS het_samples
uniqExact(sample_id)                      AS total_samples
```

- `ARRAY JOIN \`calls.sampleId\` AS sample_id, \`calls.gt\` AS gt` zet de per-sample-genotypes van één variantrij om in aparte regels (één per sample).
- `is_hom` is `gt IN ('1/1','1|1')`; de referentie-/ontbrekende genotypes (`0/0`, `./.`, …) worden weggefilterd met `gt NOT IN %(gt_ref_missing)s`, zodat alleen echte dragers meetellen.
- Door **distinct** sample-ID's te tellen, wordt een sample dat onder meerdere toegankelijke projecten voorkomt automatisch maar één keer geteld — en in dezelfde query krijg je het distinct aantal families.

**Waar in de code:** de aggregatie-`SELECT` in `_fetch_variant_rows` en de genotype-bucketdefinities `_GT_REF_MISSING` / `_GT_HOM` bovenaan `variant_explorer_service.py`.

### Carrier drill-down per familie

Wanneer de reviewer op een teller klikt, roept `VariantCarrierModal.tsx` het endpoint `/small-variants/{variant_key}/carriers` aan, dat `get_variant_carriers` uitvoert. Die functie:
1. haalt de ruwe dragerregels op uit `entries` (opnieuw `sign = 1`, `ARRAY JOIN`, optioneel gefilterd op `hom`/`het`), begrensd op `_VARIANT_CARRIER_ROW_LIMIT` (2000) rijen met een `truncated`-vlag;
2. dedupliceert tot één record per (familie, sample), waarbij homozygoot voorrang krijgt op heterozygoot;
3. koppelt de ClickHouse-`family_guid`/`sample_id` aan Postgres-metadata via `_fetch_family_meta` en `_fetch_sample_meta` (familienaam, projectnaam, rol, fenotype);
4. groepeert de dragers per familie in `VariantCarrierFamilyGroupOut`.

Cruciaal: als een familie **niet** zichtbaar is onder de projecten van de gebruiker, ontbreekt ze in `_fetch_family_meta` en wordt de drager overgeslagen (`if meta is None: continue`). De koppeling aan metadata is dus tegelijk een tweede veiligheidscontrole.

## Filters: veilig geparametriseerd

De Explorer kent twee klassen filters bovenop de gedeelde annotatiefilters (gen, panel, impact, ClinVar, gnomAD-frequenties, CADD/REVEL/SpliceAI, enz., opgebouwd in `_annotation_index_clauses`).

**1. Tag- en classificatiefilters (Postgres-brug).** Tags en ACMG-classificaties leven in Postgres (`small_variant_reviews`, per familie), niet in ClickHouse. Wanneer de gebruiker daarop filtert, resolvet `_variant_ids_matching_reviews` eerst de bijhorende `variant_id`-strings (bv. `1-1000-A-T`) binnen de toegankelijke projecten, en die lijst wordt als allow-list doorgegeven aan de ClickHouse-query (`variantId IN %(tag_variant_ids)s`). Zo overbrugt CoGA de review-status (Postgres) en de genotype-opslag (ClickHouse) op de gedeelde `variant_id` — dezelfde brug die de familiepagina gebruikt. Een lege match betekent "niets gevonden" en levert direct een leeg resultaat. Voor de weergegeven tags/classificatie per rij wordt `_review_display_map` gebruikt (unie van tags, meest-severe classificatie).

**2. Per-sample-genotypefilters.** In de UI voegt de reviewer regels toe als `sampleId : {Het | Hom | Het+Hom}`. De frontend serialiseert die als queryparameters `sample_gt=<sampleId>:<mode>` (in `globalSmallVariantSearch.ts`). De router parseert ze in `_parse_sample_genotype_filters`, waarbij een onbekende modus veilig terugvalt op `het_hom`. In `_entries_where` wordt elke sample-constraint een **aparte membership-subquery** die met `AND` wordt gecombineerd: de variant moet aan élke per-sample-eis voldoen.

**Veiligheid van de parametrisering.** Elke door de gebruiker aangeleverde waarde komt als een **benoemde parameter** (`%(naam)s`) in de query en wordt door de ClickHouse-client server-side gebonden — niet met stringconcatenatie. Dat blokkeert SQL-injectie.

**Waar in de code:** `_entries_where` en `_annotation_index_clauses` in `variant_explorer_service.py` bouwen uitsluitend `%(...)s`-placeholders; de binding gebeurt in `execute_clickhouse` in `backend/app/core/clickhouse.py`, dat `parameters=` doorgeeft aan de client (`client.query(query, parameters=...)`).

## Veiligheid: cross-project-scoping (geen lekkage mogelijk)

Dit is het hart van het hoofdstuk voor een auditor. Elke aanvraag wordt hard begrensd tot de projecten waartoe de gebruiker toegang heeft.

De scope wordt centraal opgelost in `resolve_scope`, dat steunt op `_accessible_project_rows`. Die functie splitst expliciet op rol:

```python
if _is_admin_user(user):
    result = await session.execute(text(base_query))          # admin -> alle projecten
else:
    project_ids = _user_metadata_project_ids(user)
    if not project_ids:
        return []                                              # geen projecten -> leeg
    result = await session.execute(... WHERE p.id IN :project_ids ...)
```

- **Admins** (rol in `ADMIN_ROLES`, d.w.z. `admin` of `superuser`) zien alle projecten.
- **Overige gebruikers** zien uitsluitend hun `metadata_project_ids`. Hebben ze er geen, dan is het resultaat gegarandeerd leeg: `_accessible_project_rows` geeft een lege lijst terug, `resolve_scope` geeft `None`, en de caller rendert dat als een leeg resultaat.

**Waar in de code:** `resolve_scope` en `_accessible_project_rows` in `variant_explorer_service.py`, met de rolhelpers `_is_admin_user` (`ADMIN_ROLES = {"admin", "superuser"}`) en `_user_metadata_project_ids` uit `backend/app/services/metadata_service.py`.

De uit deze scope afgeleide project-GUID's worden vervolgens in **iedere** ClickHouse-query verplicht meegegeven. De eerste twee clausules in `_entries_where` zijn altijd:

```python
clauses = ["sign = 1", "project_guid IN %(project_guids)s"]
```

Datzelfde `project_guid IN %(project_guids)s`-predicaat staat ook in:
- de aparte per-sample membership-subqueries in `_entries_where`;
- de begrensde tellingsquery in `search_global_small_variants`;
- de carrier-query in `get_variant_carriers`.

Omdat `project_guid` in ClickHouse gelijk is aan de Postgres-project-UUID (en `family_guid` aan de familie-UUID), sluit dit filter alle rijen van niet-toegankelijke projecten uit vóór er ook maar iets geteld of gedragen wordt. De frontend versterkt dit alleen visueel ("No variants match the current filters in your accessible projects") maar is niet de bewaker — de afdwinging zit volledig server-side.

**Let op — naamgeving.** Het bestand `backend/app/services/data_scope.py` gaat *niet* over projectafscherming; het bevat chromosoom-normalisatie (`normalize_chromosome`, `is_primary_chromosome`) die o.a. de variant-ID-opbouw in `clickhouse_variant_ids.py` gebruikt. Een auditor die "data scope" (toegangsscope) zoekt, moet dus in `variant_explorer_service.py` kijken, niet in `data_scope.py`.

**Traceerbaarheid.** Elke rij blijft herleidbaar: de dragerdrill-down linkt elke drager terug naar zijn familie (`family_uuid` + familienaam) en project (`project_name`), en `VariantCarrierModal.tsx` legt via de familienaam een directe link naar `/families/{family_id}`. De review-metadata (tags, classificatie, `updated_at` als "last reviewed") komt uit `small_variant_reviews` en toont wat de variant al beoordeeld heeft (zie [10-tagging-en-acmg-classificatie.md](10-tagging-en-acmg-classificatie.md) en [11-rapport-en-traceerbaarheid.md](11-rapport-en-traceerbaarheid.md)).

## Prestaties: cross-project-aggregaten in ClickHouse

De Explorer bevraagt varianten over mogelijk vele projecten en miljoenen genotype-oproepen. Enkele bewuste ontwerpkeuzes houden dit schaalbaar (zie ook [03-databankstructuren.md](03-databankstructuren.md)):

- **Partitionering op `project_guid`.** De `entries`-tabel gebruikt `ENGINE = CollapsingMergeTree(sign)`, `PARTITION BY project_guid`, `ORDER BY (project_guid, family_guid, xpos, key)`. Omdat elke query start met `project_guid IN (...)`, snoeit ClickHouse meteen alle niet-relevante partities weg — dit is tegelijk een prestatiewinst én de fysieke onderbouwing van de veiligheidsscoping.
  - **Waar in de code:** de DDL in `backend/app/services/clickhouse_variant_storage.py` (blok `` `{dataset}/SNV_INDEL/entries` ``).
- **Begrensde telling.** Een volledige `uniqExact` over miljoenen keys is duur. `search_global_small_variants` telt daarom hoogstens `_EXPLORER_COUNT_CAP + 1` (10 001) distinct keys via een `GROUP BY key ... LIMIT`; voorbij de cap rapporteert `_bounded_total` het aantal als de cap (10 000) met `total_is_estimated=True` (de UI toont "N+").
- **Keyset-paginering.** In plaats van dure `OFFSET`-sprongen codeert de cursor het triplet `(sort_value, xpos, key)` van de laatste rij, plus de sort/order en een hash van de filters (`_filters_fingerprint`). Een cursor die hoort bij een andere sortering of gewijzigde filters wordt gedetecteerd en genegeerd (`_decode_cursor` → `None`), zodat je nooit stilzwijgend verkeerd bladert.
- **Aparte annotatie-index.** Annotatiefilters draaien als subquery tegen `annotation_index` (`key IN (SELECT DISTINCT ai.key ...)`), en de weergavevelden (impact, effect, HGVS, ClinVar) worden per pagina opgehaald met `_fetch_annotation_display` — enkel voor de zichtbare keys, niet voor de hele set.

## Belangrijkste bestanden

| Bestand | Rol |
|---|---|
| `backend/app/routers/variant_explorer.py` | HTTP-endpoints onder `/variant-explorer`; parseert filters (`_build_global_variant_filters`, `_parse_sample_genotype_filters`) en CSV-export |
| `backend/app/services/variant_explorer_service.py` | Aggregatie-engine: scoping (`resolve_scope`), tellingen, keyset-paginering, dragerdrill-down, Postgres-review-brug |
| `backend/app/services/clickhouse_variant_ids.py` | Opbouw van variant-ID's/keys en `xpos` (`build_small_variant_id`, `small_variant_key`, `_xpos`) |
| `backend/app/services/clickhouse_variant_rows.py` | Opbouw van de `entries`- en annotatie-index-rijen die bij import naar ClickHouse worden geschreven (leescontract van de Explorer) |
| `backend/app/services/clickhouse_variant_storage.py` | DDL van de `entries`-tabel (`CollapsingMergeTree`, partitie op `project_guid`); opruimen legacy `gt_stats` |
| `backend/app/services/metadata_service.py` | Rol-/toegangshelpers `_is_admin_user` (`ADMIN_ROLES`), `_user_metadata_project_ids` die de scope voeden |
| `backend/app/services/data_scope.py` | Chromosoom-normalisatie (let op: géén projectafscherming ondanks de naam) |
| `backend/app/core/clickhouse.py` | `execute_clickhouse` met server-side parameterbinding (`%(...)s`) |
| `frontend/src/pages/variant-explorer/GlobalSmallVariantExplorerPage.tsx` | Hoofdpagina: assembly-keuze, genotypefilter, resultaattabel, CSV-download |
| `frontend/src/pages/variant-explorer/globalSmallVariantSearch.ts` | Familie-agnostische zoek-state, cursor-paginering, query-string-opbouw |
| `frontend/src/pages/variant-explorer/GlobalSmallVariantTable.tsx` | Resultaattabel met sorteerbare tellerkolommen |
| `frontend/src/pages/variant-explorer/VariantCarrierModal.tsx` | Drill-downvenster met dragers gegroepeerd per familie, links naar de familiepagina |
| `frontend/src/pages/variant-explorer/types.ts` | TypeScript-typen die de backend-schema's spiegelen |
| `frontend/src/lib/variantSearch.ts` | Locus-/gebied-parser (`parseGeneOrRegionInput`) |
