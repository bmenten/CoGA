# 14. Variant Explorer

Dit hoofdstuk beschrijft de *Global Small Variant Explorer*: een zoekfunctie die small variants (SNV's en indels) samenvat over **alle projecten waartoe een gebruiker toegang heeft**, niet binnen één familie. Resultaten lekken nooit buiten de projecten van de gebruiker (zie *Afscherming tussen projecten*). Uitleg voor gebruikers staat in de gebruikersgids in de app (sectie `explorers`).

Enkele begrippen:

- **Drager:** een sample dat het alternatieve allel draagt, heterozygoot of homozygoot.
- **Aggregatie:** per unieke variant tellen hoeveel samples en families haar dragen.
- **Assembly:** de versie van het referentiegenoom (bv. GRCh38); varianten van verschillende assemblies worden nooit samen geteld.
- **Keyset-paginering:** pagina per pagina door een groot resultaat bladeren zonder dure sprongen (`OFFSET`), door te onthouden waar de vorige pagina eindigde.

## Doel en scherm

De filterpagina van een familie (hoofdstuk 8) beantwoordt "welke varianten zitten in *deze* familie?". De Variant Explorer draait de vraag om: "in hoeveel samples en families van mijn toegankelijke cohort komt *deze* variant voor?". Elke rij is één unieke variant, met de tellers *Total samples*, *Het*, *Hom* en *Families*.

Het scherm (`/variant-explorer`) heeft een keuzelijst voor de assembly, een filter op genotype per sample, het gewone annotatiefilterformulier en de resultaattabel. Een klik op een teller opent een venster met de dragers, gegroepeerd per familie, met een link naar elke familie. Het formulier is dat van de familiepagina, zonder de velden voor familie en samples. Filters die de Explorer niet toepast, biedt het hier niet aan, en hun parameters worden niet verstuurd: een intervallijst, transcript, uit te sluiten genen en intervallen, uit te sluiten tags, "heeft notities".

**Waar in de code:** `frontend/src/pages/variant-explorer/` (`GlobalSmallVariantExplorerPage.tsx`, `globalSmallVariantSearch.ts`, `GlobalSmallVariantTable.tsx`, `VariantCarrierModal.tsx`).

## Endpoints

Alle endpoints staan onder `/api/variant-explorer` en vragen een login.

| Endpoint | Doel |
| --- | --- |
| `GET /variant-explorer/assemblies` | De toegankelijke assemblies, met het aantal projecten per assembly |
| `GET /variant-explorer/samples` | De samples die de gebruiker mag zien (voor de genotypefilter) |
| `GET /variant-explorer/small-variants` | De gepagineerde, geaggregeerde variantlijst |
| `GET /variant-explorer/small-variants/export` | Dezelfde resultaten als CSV, tot 50.000 rijen; een bestand dat niet elke treffer bevat, zegt dat (zie *De CSV-export*) |
| `GET /variant-explorer/small-variants/{variant_key}/carriers` | De dragers van één variant, per familie |
| `GET /variant-explorer/small-variant-tags` | De tagdefinities van de toegankelijke projecten |

De filters worden op één plaats ingelezen. Zo gebruiken de lijst en de CSV-export gegarandeerd dezelfde filters.

**Waar in de code:** `backend/app/routers/variant_explorer.py`; de logica in `backend/app/services/variant_explorer_service.py`.

## Hoe de dragers geteld worden

De Explorer telt rechtstreeks uit de ClickHouse-tabel `entries`, niet uit een vooraf berekende samenvatting. Hij leest alleen rijen met `sign = 1`, zodat een nieuwe import of een verwijdering de tellingen niet opblaast. Per variant:

- worden de genotypes per sample uitgevouwen tot één regel per sample;
- tellen alleen echte dragers mee. *Het*, *Hom* en drager volgen de genotypeklassen van `backend/app/services/genotypes.py` (hoofdstuk 8), zodat bv. een haploïde `1` als homozygoot telt;
- telt elk sample en elke familie één keer, ook als het onder meerdere toegankelijke projecten voorkomt;
- tellen geïmputeerde en gefaseerde calls (bron `glimpse2` of `shapeit`) niet mee, tenzij de gebruiker *Include imputed variants (GLIMPSE2 / SHAPEIT)* aanvinkt. Dat geldt voor de tellers, de voorwaarden per sample en het dragervenster.

**De dragers per familie.** Het dragervenster haalt de dragers van één variant op (begrensd, met een melding als er meer zijn). Het houdt per familie en sample één record over (homozygoot gaat voor) en koppelt die aan de namen van familie, project en lid uit Postgres. Een familie die de gebruiker niet mag zien, ontbreekt in die koppeling; haar drager wordt dan overgeslagen. De koppeling is dus tegelijk een tweede toegangscontrole.

## Filters

Bovenop de gewone annotatiefilters (gen, panel, impact, ClinVar, gnomAD, CADD, REVEL, SpliceAI, …) kent de Explorer twee soorten filters:

1. **Tags en classificaties.** Die staan per familie in Postgres. Filtert de gebruiker erop, dan zoekt de Explorer eerst in Postgres de bijbehorende variant-id's binnen de toegankelijke projecten, en geeft die lijst mee aan de ClickHouse-vraag. Een lege lijst geeft meteen een leeg resultaat. De lijst staat letterlijk in de query en telt daarom hoogstens 200.000 id's: de eerste in de volgorde van de variant-id, zodat dezelfde filter altijd dezelfde lijst geeft. Postgres leest er één meer, om te weten of er meer matchten; dan kan de zoekopdracht treffers missen. De CSV-export meldt dat, de lijst op het scherm niet.
2. **Genotype per sample.** De gebruiker voegt regels toe van de vorm sample met *Het*, *Hom* of *Het + Hom*. Elke regel is een aparte voorwaarde; een variant moet aan alle regels voldoen.

**De annotatiefilters per transcript, zoals de familiepagina.** De Explorer houdt dezelfde varianten over als de familiepagina, want hij bouwt zijn annotatievoorwaarde met dezelfde functie: `_small_annotation_filter_condition` in `clickhouse_variant_queries.py`, die `_transcript_annotation_condition` aanroept met de filters van de Explorer.

- Deze filters moeten samen gelden voor één transcript (één rij van de tabel `annotations`): impact en gevolg, HGVS, de ClinVar-status, de frequentiegrenzen, de voorspellers, en *Canonical only*, *MANE only* en *LoF only*. De frequentiegrenzen kennen de uitzondering "een (waarschijnlijk) pathogene ClinVar-variant passeert het frequentiefilter", op dezelfde ClinVar-termen. Met *MANE only* en impact HIGH valt zo een variant weg waarvan het HIGH-transcript niet het MANE-transcript is.
- Van de voorspellers (CADD, REVEL, SpliceAI, SIFT, PolyPhen) volstaat er één. Een SpliceAI-score vanaf de grens telt ook als treffer voor impact en gevolg.
- Het gen, de rsID en de uit te sluiten ClinVar-statussen gelden, ook op de familiepagina, voor de variant als geheel. Die blijven op de annotatie-index.
- Het panel past de Explorer toe zoals de familiepagina (hieronder).

Vroeger toetste de Explorer elke filter apart aan de annotatie-index, die de transcripten van een variant samenvoegt. Hij hield zo'n variant dan wel over, en eiste alle gekozen voorspellers tegelijk (CLIN-29).

De Explorer toetst een variant aan elke annotatieset die voor zijn variantsleutel opgeslagen is; de familiepagina aan de set van haar eigen rij. Dat verschilt alleen als dezelfde variant in twee families met een andere annotatie geïmporteerd is. De genen van een panel toetst de Explorer wel per rij, zoals de familiepagina: aan de gensymbolen en de annotatieset van die rij.

**Het genpanel, zoals de familiepagina.** De Explorer leest een panel zoals de familiepagina het leest (`_fetch_panel_constraints` in `clickhouse_family_variants.py`), voor de assembly van de zoekopdracht:

- de genen van het panel en de namen van zijn opgeslagen regio's;
- de regio's die voor die assembly bewaard zijn (de coördinaten van PanelApp);
- de locus van elk gen in de genreferentie van die assembly.

Hij past het toe met de voorwaarde van de familiepagina (`_small_panel_filter_condition` in `clickhouse_variant_queries.py`): een variant in een van de regio's, of met een van de genen in de gensymbolen van zijn rij of in de genindex, hoe groot het panel ook is. Een genpanel met meer dan 1000 regio's, zoals het Mendeliome, vergeleek hij (net als de familiepagina) vroeger alleen op gensymbool; sinds de beslissing van de eigenaar van 14 oktober 2026 ook op positie. Een panel zonder genen en zonder regio's voor de assembly houdt niets over. Een panel dat niet bestaat geeft `404`, een id dat geen UUID is `400`, zoals op de familiepagina. Vroeger las de Explorer alleen de gensymbolen van het panel, op de annotatie-index. Een panel van alleen regio's filterde dan niets, en een variant die het panel alleen via een regio bereikt (bv. omdat de annotatie het gen anders noemt dan de genreferentie), ontbrak (CLIN-48).

Alleen de regio's zoekt de Explorer anders op, met hetzelfde antwoord. Elke rij aan elke regio toetsen (`arrayExists`, de regel van `_small_region_filter_condition`) duurde over alle families van het cohort minuten voor een panel van duizend genen. De Explorer (`_region_key_condition` in `variant_explorer_service.py`) maakt daarom een join op vensters van 64 kb:

- elke regio komt onder elk venster van 64 kb dat ze raakt;
- elke variant komt, op zijn positie in de annotatie-index, onder elk venster dat zijn REF raakt;
- een paar dat een venster deelt, blijft over als de twee overlappen volgens de regel van de familiepagina: hetzelfde chromosoom (`chr1` en `1`, `M` en `MT` gelden als hetzelfde), de eerste base van de REF op of vóór het einde van de regio, en de laatste op of na haar begin.

Twee die overlappen, delen het venster van een base die ze gemeen hebben; geen enkele valt dus weg. De regio's leest hij zoals de familiepagina ze leest (`_small_region_bounds`). De familiepagina maakt sinds ENG-15 dezelfde join, over de eigen rijen van de familie (`_small_family_region_condition` in `clickhouse_variant_queries.py`). Elke rij aan elke regio toetsen kostte daar 1,3 s bij 500 regio's en 2,1 s bij 1000, voor een familie van zo'n 190.000 rijen.

Ook *MANE only* volgt de familiepagina: een transcript telt als het MANE Select of MANE Plus Clinical is. Beide lezen die vlaggen uit één lijst, `MANE_ONLY_TRANSCRIPT_FLAGS` in `backend/app/services/clickhouse_variant_records.py`. VEP zet die vlaggen alleen op GRCh38: op GRCh37 en T2T houdt *MANE only* op beide pagina's niets over.

Elke waarde die de gebruiker aanlevert, gaat als benoemde parameter (`%(naam)s`) naar de ClickHouse-client, die ze ge-escapet in de query zet. De code van CoGA plakt ze nooit zelf in de tekst (hoofdstuk 7).

## De CSV-export

De export gebruikt dezelfde filters en sortering als de lijst, zonder paginering, en schrijft hoogstens 50.000 rijen. Hij vraagt ClickHouse één rij meer: komt die terug, dan matchten er meer varianten dan het bestand bevat. Zo'n bestand meldt dat, net als de exports van de familiepagina:

- de bestandsnaam zegt `TRUNCATED`: `variant-explorer-…-TRUNCATED-first-50000.csv`;
- de headers zeggen hoeveel rijen er geschreven zijn (`X-CoGA-Export-Rows`), dat er meer matchten (`X-CoGA-Export-Truncated`), de grens (`X-CoGA-Export-Limit`) en waarom (`X-CoGA-Export-Truncated-Reason`: `row-limit`);
- het scherm toont een waarschuwing die vraagt de filters te verfijnen.

Was de lijst van de tag- of classificatiefilter afgekapt (zie *Filters*), dan kan een treffer overal in het bestand ontbreken, hoe weinig rijen het ook telt. De reden is dan `review-filter-limit`, de naam `…-TRUNCATED-partial-search.csv`, en de waarschuwing vraagt op minder tags of classificaties te filteren. Die reden gaat voor op `row-limit`.

De reviews van de rijen (tags, classificatie, laatste review) zoekt de export in Postgres op, met de variant-id's als één array-parameter. Met één parameter per id brak elke export van meer dan 32.767 rijen af: asyncpg weigert een vraag met meer parameters.

**Waar in de code:** `export_global_small_variants` en `_review_filter_variant_ids` in `backend/app/services/variant_explorer_service.py`; `export_response_headers` in `backend/app/core/csv_export.py`; `describeCsvExport` en `truncatedExportMessage` in `frontend/src/lib/csvExport.ts`.

## Afscherming tussen projecten

Dit is voor een auditor het belangrijkste deel. Elke vraag is hard beperkt tot de projecten van de gebruiker:

- De scope wordt op één plaats bepaald. **Beheerders** (`admin` of `superuser`) zien alle projecten, **andere gebruikers** alleen hun eigen projecten. Zonder projecten is het resultaat gegarandeerd leeg.
- Elke vraag naar `entries` krijgt die projecten verplicht mee (`project_guid IN …`): de lijstvraag, de begrensde telling, de voorwaarden per sample en de dragervraag. `project_guid` is gelijk aan de project-UUID in Postgres. Rijen van andere projecten vallen dus weg voordat er iets geteld wordt. De annotatiefilters en het panel lezen per variant de tabellen `annotations`, `annotation_index` en `gene_index`; de annotatie van de zichtbare pagina komt daarna uit de annotatie-index. Geen van die tabellen bevat project-, familie- of samplegegevens.
- De frontend toont alleen een melding als er niets is ("… in your accessible projects"); de afscherming zit volledig op de server.

Het bestand `backend/app/services/data_scope.py` gaat, ondanks de naam, niet over deze afscherming maar over chromosoomnamen. De toegangsscope staat in `variant_explorer_service.py` (`resolve_scope`).

**Traceerbaarheid.** Elke drager is terug te voeren op zijn familie en project. De review (tags, classificatie, laatste review) komt uit `small_variant_reviews` (hoofdstuk 10). Is een variant in meer families beoordeeld, dan toont de rij de tags van alle reviews en de ernstigste classificatie (`_classification_rank`). Die leest de klasse uit de opgeslagen tekst, in elke vorm: het label van het reviewvenster ("Pathogenic - class 5"), een klassesleutel (`acmg_class_5`, `cnv_class_5`) of het klassewoord, in welke schrijfwijze ook. Tekst die geen klasse noemt, telt het laagst. Vroeger telde elk opgeslagen label als 0 en won de eerste gelezen review (CLIN-69).

**Waar in de code:** `resolve_scope` in `backend/app/services/variant_explorer_service.py`; de rolregels in `backend/app/services/access_control.py`.

## Snelheid

De Explorer bevraagt mogelijk vele projecten en miljoenen genotypes. Enkele keuzes houden dat werkbaar:

- **Verdeling per project.** De tabel `entries` is verdeeld per project (hoofdstuk 3). Elke vraag begint met de projectfilter, dus slaat ClickHouse alle andere projecten meteen over. Dat is snelheid én de fysieke basis van de afscherming.
- **Begrensde telling.** Boven een vaste grens meldt de Explorer het aantal als "N+" (`total_is_estimated`), in plaats van alles exact te tellen.
- **Keyset-paginering.** De cursor onthoudt de laatste rij, de sortering en een vingerafdruk van de filters. Een cursor van een andere sortering of andere filters wordt genegeerd, zodat niemand ongemerkt verkeerd bladert.
- **Annotatiefilters per transcript** (zie *Filters*). De tabel `annotations` heeft één rij per transcript, de kleinere annotatie-index één per variant. Een locus (chromosoom, begin, einde) begrenst ook de gelezen transcriptrijen. De weergavevelden komen alleen voor de zichtbare pagina.
- **De regio's van een panel als join** (zie *Filters*). Op een synthetisch cohort van 2 miljoen varianten en 7,6 miljoen rijen in 40 families vond die join de varianten van een panel van 1000 regio's in een twintigste van een seconde. De toets per rij, die de familiepagina toen gebruikte, deed er over hetzelfde cohort bijna drie minuten over. Met het Mendeliome op positie (zowat 42.000 loci, samengevoegd tot ongeveer één regio per gen) duurde de begrensde telling op een synthetisch cohort van 1,19 miljoen varianten 0,16 s, tegen 0,10 s alleen op gensymbool (`docs/variant-ranking-cache.md`).

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/variant_explorer.py` | De endpoints en het inlezen van de filters |
| `backend/app/services/variant_explorer_service.py` | Scope, tellingen, paginering, dragers en de brug naar de reviews |
| `backend/app/services/genotypes.py` | De genotypeklassen |
| `backend/app/services/access_control.py` | De rolregels die de scope voeden |
| `backend/app/services/clickhouse_variant_storage.py` | De tabel `entries`, verdeeld per project |
| `frontend/src/pages/variant-explorer/GlobalSmallVariantExplorerPage.tsx` | Het scherm |
| `frontend/src/pages/variant-explorer/globalSmallVariantSearch.ts` | Zoektoestand, cursor, querystring |
| `frontend/src/pages/variant-explorer/VariantCarrierModal.tsx` | Het dragervenster per familie |
