# 14. Variant Explorer

Dit hoofdstuk beschrijft de *Global Small Variant Explorer*: een zoekfunctie die small variants (SNV's en indels) samenvat over **alle projecten waartoe een gebruiker toegang heeft**, niet binnen één familie. Resultaten lekken nooit buiten de projecten van de gebruiker (zie *Afscherming tussen projecten*). Uitleg voor gebruikers staat in de gebruikersgids in de app (sectie `explorers`).

Enkele begrippen:

- **Drager:** een sample dat het alternatieve allel draagt, heterozygoot of homozygoot.
- **Aggregatie:** per unieke variant tellen hoeveel samples en families haar dragen.
- **Assembly:** de versie van het referentiegenoom (bv. GRCh38); varianten van verschillende assemblies worden nooit samen geteld.
- **Keyset-paginering:** pagina per pagina door een groot resultaat bladeren zonder dure sprongen (`OFFSET`), door te onthouden waar de vorige pagina eindigde.

## Doel en scherm

De filterpagina van een familie (hoofdstuk 8) beantwoordt "welke varianten zitten in *deze* familie?". De Variant Explorer draait de vraag om: "in hoeveel samples en families van mijn toegankelijke cohort komt *deze* variant voor?". Elke rij is één unieke variant, met de tellers *Total samples*, *Het*, *Hom* en *Families*.

Het scherm (`/variant-explorer`) heeft een keuzelijst voor de assembly, een filter op genotype per sample, het gewone annotatiefilterformulier en de resultaattabel. Een klik op een teller opent een venster met de dragers, gegroepeerd per familie, met een link naar elke familie. Het formulier is dat van de familiepagina, zonder de velden voor familie en samples. Filters die de Explorer niet toepast, biedt het hier niet aan, en hun parameters worden niet verstuurd: een intervallijst, transcript, callset, uit te sluiten genen en intervallen, uit te sluiten tags, "heeft notities" en *Pass variants only*. Die laatste blijft een filter van de familiepagina, die alleen de getoonde familieleden leest (beslissing van de eigenaar van 6 november 2026).

**Waar in de code:** `frontend/src/pages/variant-explorer/` (`GlobalSmallVariantExplorerPage.tsx`, `globalSmallVariantSearch.ts`, `GlobalSmallVariantTable.tsx`, `VariantCarrierModal.tsx`).

## Endpoints

Alle endpoints staan onder `/api/variant-explorer` en vragen een login.

| Endpoint | Doel |
| --- | --- |
| `GET /variant-explorer/assemblies` | De toegankelijke assemblies, met het aantal projecten per assembly |
| `GET /variant-explorer/samples` | De samples die de families van de gebruiker tonen, hun actieve leden (voor de genotypefilter) |
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
- tellen geïmputeerde en gefaseerde calls (bron `glimpse2` of `shapeit`) niet mee, tenzij de gebruiker *Include imputed variants (GLIMPSE2 / SHAPEIT)* aanvinkt. Dat geldt voor de tellers, de voorwaarden per sample en het dragervenster;
- tellen alleen de calls van de samples die hun familie toont (hieronder).

**Alleen de samples die hun familie toont.** Een rij van `entries` bevat een call van elk sample van de VCF waaruit ze geïmporteerd is. Wordt een lid later uit de familie gehaald (`remove_members` bij een wijziging van de familiestructuur, zonder de data te wissen), dan blijven zijn calls in die rijen staan, maar de familiepagina toont het niet meer: ze leest alleen de calls van de actieve leden van de familie (`_visible_clickhouse_sample_ids`). De Explorer leest elke rij op dezelfde manier (beslissing van de eigenaar van 6 november 2026). Hij leest eerst in Postgres de actieve leden van elke familie van de toegankelijke projecten (`_shown_members` in `variant_explorer_service.py`). Daaruit maakt hij de paren (familie, sample), elk sample onder zijn naam en zijn UUID (`_shown_calls`). Een call telt alleen als zijn paar erbij hoort: het sample is een actief lid van de familie van die rij. Dat geldt voor de telling, de tellers, de CSV-export, het dragervenster en de genotypefilter per sample. Een variant die alleen zo'n uit de familie gehaald lid draagt, verschijnt niet. Een sample dat in een andere familie wel lid is, telt in de rijen van die familie mee, niet in die van de oude.

**De dragers per familie.** Het dragervenster haalt de dragers van één variant op (begrensd, met een melding als er meer zijn), alleen de leden die hun familie toont. Het houdt per familie en sample één record over (homozygoot gaat voor) en koppelt die aan de namen van familie en project uit Postgres, en aan de naam, rol en fenotype van het lid in die familie. Een familie die de gebruiker niet mag zien, ontbreekt in die koppeling; haar drager wordt dan overgeslagen. De koppeling is dus tegelijk een tweede toegangscontrole.

**Waar getest:** `backend/tests/integration/test_variant_explorer_visible_members_clickhouse.py` (op echte ClickHouse en Postgres: een familie van vier waaruit vader en broer gehaald werden, de vader als lid van een eigen familie; de telling, de tellers, de export, het dragervenster, de genotypefilter en de keuzelijst van samples).

## Filters

Bovenop de filters van de familiepagina (locatie, type, gen, rsID, panel, impact, ClinVar, gnomAD, CADD, REVEL, SpliceAI, …) kent de Explorer twee soorten filters:

1. **Tags en classificaties.** Die staan per familie in Postgres. Filtert de gebruiker erop, dan zoekt de Explorer eerst in Postgres de bijbehorende variant-id's binnen de toegankelijke projecten, en geeft die lijst mee aan de ClickHouse-vraag. Een lege lijst geeft meteen een leeg resultaat. De lijst staat letterlijk in de query en telt daarom hoogstens 200.000 id's: de eerste in de volgorde van de variant-id, zodat dezelfde filter altijd dezelfde lijst geeft. Postgres leest er één meer, om te weten of er meer matchten; dan kan de zoekopdracht treffers missen. De CSV-export meldt dat, de lijst op het scherm niet.
2. **Genotype per sample.** De gebruiker voegt regels toe van de vorm sample met *Het*, *Hom* of *Het + Hom*. Elke regel is een aparte voorwaarde; een variant moet aan alle regels voldoen. De Explorer leest de call van het sample met de samplefilter van de familiepagina (`_small_sample_call_condition` in `clickhouse_variant_queries.py`), dus met dezelfde genotypeklassen: een haploïde `1` is homozygoot, een `1/2` heterozygoot. Hij leest wel per variant, niet per rij: hij houdt een variant die het sample in een van zijn rijen met dat genotype draagt, en telt dan alle dragers van het cohort. De familiepagina houdt alleen de rijen van haar eigen familie. Per rij lezen zou in de Explorer alleen de dragers van de familie van dat sample tellen. Dit is het enige gedocumenteerde verschil tussen beide (beslissing van de eigenaar van 6 november 2026). De call van het sample leest hij alleen in de rijen van een familie die het toont, onder zijn naam of zijn UUID. Een sample dat geen familie toont, zoals een lid dat uit zijn familie gehaald werd, houdt geen variant over, en de keuzelijst biedt het niet aan.

**Eén definitie: de bouwstenen van de familiezoekopdracht.** Elke filter die de Explorer met de familiepagina deelt, bouwt hij met de functie van de familiezoekopdracht in `clickhouse_variant_queries.py` (`_entries_where` in `variant_explorer_service.py` roept ze aan):

- `_small_row_filter_clauses`: het varianttype, de geïmputeerde callsets, de locatie en de rsID;
- `_small_annotation_filter_clauses`: het gen, de annotatiefilters per transcript en de uit te sluiten ClinVar-statussen;
- `_small_panel_filter_condition`: het panel (hieronder).

Hij leest elke rij van `entries` zoals de familiepagina haar leest, met haar eigen annotatie: de variantsleutel en de annotatieset van die rij (hoofdstuk 8). Een rij blijft dus over precies wanneer de familiepagina van haar familie de variant toont, en een rij van de Explorer telt de families en dragers van die familiepagina's samen. Dat verschilt alleen als dezelfde variant in twee families met een andere annotatie geïmporteerd is (twee VEP-runs). Een voorbeeld: familie A zegt *stop gained* (HIGH), familie B *missense* (MODERATE). Met impact HIGH toont de familiepagina van A de variant, die van B niet; de Explorer toont hem met alleen de dragers van A (beslissing van de eigenaar van 6 november 2026).

- **De locatie.** Het chromosoom geldt met of zonder `chr`, en het mitochondrion als `chrM`, `M` of `MT` (`_chromosome_options`). Een variant valt binnen de locatie als hij er begint (zijn positie op of na het begin, op of vóór het einde).
- **Het gen.** In de gensymbolen van de rij, of in de genindex van haar annotatie (gensymbolen en Ensembl-gen-id's), in welke schrijfwijze ook.
- **De rsID.** In de rsID van de rij of in die van haar annotatie, zoals getypt maar zonder spaties rond.
- **De uit te sluiten ClinVar-statussen.** Geen transcript van de annotatie van de rij heeft die status.
- **De annotatiefilters per transcript** moeten samen gelden voor één transcript (één rij van de tabel `annotations`), met de voorwaarde `_small_annotation_filter_condition`: impact en gevolg, HGVS, de ClinVar-status, de frequentiegrenzen, de voorspellers, en *Canonical only*, *MANE only* en *LoF only*. De frequentiegrenzen kennen de uitzondering "een (waarschijnlijk) pathogene ClinVar-variant passeert het frequentiefilter", op dezelfde ClinVar-termen. Met *MANE only* en impact HIGH valt zo een variant weg waarvan het HIGH-transcript niet het MANE-transcript is. Van de voorspellers (CADD, REVEL, SpliceAI, SIFT, PolyPhen) volstaat er één. Een SpliceAI-score vanaf de grens telt ook als treffer voor impact en gevolg.

**Waar getest:** `backend/tests/integration/test_variant_explorer_filter_rule_clickhouse.py` (op echte ClickHouse: twee families, één variant onder twee annotaties, het mitochondrion als `M` en als `MT`; voor elke filter houdt elke familiepagina over wat de regel overhoudt, en telt elke rij van de Explorer de families en dragers van die pagina's); `backend/tests/test_variant_explorer_service.py` (elke clausule van de Explorer is die van de familiezoekopdracht, met dezelfde parameters).

**Het genpanel, zoals de familiepagina.** De Explorer leest een panel zoals de familiepagina het leest (`_fetch_panel_constraints` in `clickhouse_family_variants.py`), voor de assembly van de zoekopdracht:

- de genen van het panel en de namen van zijn opgeslagen regio's;
- de regio's die voor die assembly bewaard zijn (de coördinaten van PanelApp);
- de locus van elk gen in de genreferentie van die assembly.

Hij past het toe met de voorwaarde van de familiepagina (`_small_panel_filter_condition` in `clickhouse_variant_queries.py`): een variant in een van de regio's, of met een van de genen in de gensymbolen van zijn rij of in de genindex, hoe groot het panel ook is. Een genpanel met meer dan 1000 regio's, zoals het Mendeliome, vergelijkt hij (net als de familiepagina) ook op positie (beslissing van de eigenaar van 14 oktober 2026). Een panel zonder genen en zonder regio's voor de assembly houdt niets over. Een panel dat niet bestaat geeft `404`, een id dat geen UUID is `400`, zoals op de familiepagina.

Alleen de regio's zoekt de Explorer anders op, met hetzelfde antwoord. Elke rij aan elke regio toetsen (`arrayExists`, de regel van `_small_region_filter_condition`) zou over alle families van het cohort minuten duren voor een panel van duizend genen. De Explorer (`_region_key_condition` in `variant_explorer_service.py`) maakt daarom een join op vensters van 64 kb:

- elke regio komt onder elk venster van 64 kb dat ze raakt;
- elke variant komt, op zijn positie in de annotatie-index, onder elk venster dat zijn REF raakt;
- een paar dat een venster deelt, blijft over als de twee overlappen volgens de regel van de familiepagina: hetzelfde chromosoom (`chr1` en `1`, `M` en `MT` gelden als hetzelfde), de eerste base van de REF op of vóór het einde van de regio, en de laatste op of na haar begin.

Twee die overlappen, delen het venster van een base die ze gemeen hebben; geen enkele valt dus weg. De regio's leest hij zoals de familiepagina ze leest (`_small_region_bounds`). De familiepagina maakt dezelfde join, over de eigen rijen van de familie (`_small_family_region_condition` in `clickhouse_variant_queries.py`). Elke rij aan elke regio toetsen kost daar 1,3 s bij 500 regio's en 2,1 s bij 1000, voor een familie van zo'n 190.000 rijen.

Ook *MANE only* volgt de familiepagina: een transcript telt als het MANE Select of MANE Plus Clinical is. Beide lezen die vlaggen uit één lijst, `MANE_ONLY_TRANSCRIPT_FLAGS` in `backend/app/services/clickhouse_variant_records.py`. VEP zet die vlaggen alleen op GRCh38: op GRCh37 en T2T houdt *MANE only* op beide pagina's niets over.

Elke waarde die de gebruiker aanlevert, gaat als benoemde parameter (`%(naam)s`) naar de ClickHouse-client, die ze ge-escapet in de query zet. De code van CoGA plakt ze nooit zelf in de tekst (hoofdstuk 7).

## De CSV-export

De export gebruikt dezelfde filters en sortering als de lijst, zonder paginering, en schrijft hoogstens 50.000 rijen. Hij vraagt ClickHouse één rij meer: komt die terug, dan matchten er meer varianten dan het bestand bevat. Zo'n bestand meldt dat, net als de exports van de familiepagina:

- de bestandsnaam zegt `TRUNCATED`: `variant-explorer-…-TRUNCATED-first-50000.csv`;
- de headers zeggen hoeveel rijen er geschreven zijn (`X-CoGA-Export-Rows`), dat er meer matchten (`X-CoGA-Export-Truncated`), de grens (`X-CoGA-Export-Limit`) en waarom (`X-CoGA-Export-Truncated-Reason`: `row-limit`);
- het scherm toont een waarschuwing die vraagt de filters te verfijnen.

Was de lijst van de tag- of classificatiefilter afgekapt (zie *Filters*), dan kan een treffer overal in het bestand ontbreken, hoe weinig rijen het ook telt. De reden is dan `review-filter-limit`, de naam `…-TRUNCATED-partial-search.csv`, en de waarschuwing vraagt op minder tags of classificaties te filteren. Die reden gaat voor op `row-limit`.

De reviews van de rijen (tags, classificatie, laatste review) zoekt de export in Postgres op, met de variant-id's als één array-parameter. Met één parameter per id zou elke export van meer dan 32.767 rijen afbreken: asyncpg weigert een vraag met meer parameters.

**Waar in de code:** `export_global_small_variants` en `_review_filter_variant_ids` in `backend/app/services/variant_explorer_service.py`; `export_response_headers` in `backend/app/core/csv_export.py`; `describeCsvExport` en `truncatedExportMessage` in `frontend/src/lib/csvExport.ts`.

## Afscherming tussen projecten

Dit is voor een auditor het belangrijkste deel. Elke vraag is hard beperkt tot de projecten van de gebruiker:

- De scope wordt op één plaats bepaald. **Een superuser en een beheerder** (`superuser`, `admin`; `has_superuser_rights`) zien alle projecten, **andere gebruikers** alleen hun eigen projecten. Zonder projecten is het resultaat gegarandeerd leeg.
- Elke vraag naar `entries` krijgt die projecten verplicht mee (`project_guid IN …`): de lijstvraag, de begrensde telling, de voorwaarden per sample en de dragervraag. De lijst van leden die de families tonen, leest Postgres alleen voor de families van die projecten. `project_guid` is gelijk aan de project-UUID in Postgres. Rijen van andere projecten vallen dus weg voordat er iets geteld wordt. De annotatiefilters en het panel lezen per rij (variantsleutel en annotatieset) de tabellen `annotations`, `annotation_index` en `gene_index`; de annotatie van de zichtbare pagina komt daarna uit de annotatie-index. Geen van die tabellen bevat project-, familie- of samplegegevens.
- De frontend toont alleen een melding als er niets is ("… in your accessible projects"); de afscherming zit volledig op de server.

Het bestand `backend/app/services/data_scope.py` gaat, ondanks de naam, niet over deze afscherming maar over chromosoomnamen. De toegangsscope staat in `variant_explorer_service.py` (`resolve_scope`).

**Traceerbaarheid.** Elke drager is terug te voeren op zijn familie en project. De review (tags, classificatie, laatste review) komt uit `small_variant_reviews` (hoofdstuk 10). Is een variant in meer families beoordeeld, dan toont de rij de tags van alle reviews en de ernstigste classificatie (`_classification_rank`). Die leest de klasse uit de opgeslagen tekst, in elke vorm: het label van het reviewvenster ("Pathogenic - class 5"), een klassesleutel (`acmg_class_5`, `cnv_class_5`) of het klassewoord, in welke schrijfwijze ook. Tekst die geen klasse noemt, telt het laagst.

Elke review telt met de klasse die de familiepagina ervoor toont (`review_class_label` in `review_pg_utils.py`, dezelfde regel als `normalizeReviewClassification` in de frontend): een kaal klassewoord ("VUS") als zijn label, anders de ernstigste klassetag van de review (`acmg_class_5` … `acmg_class_1`) als zijn label, anders de tekst zoals opgeslagen. Zo toont ook een review die alleen een klassetag heeft, zonder classificatietekst, zijn klasse. De eigen review gaat voor die van het compound-het-paar. Postgres leest de reviews in een vaste volgorde (laatst bewaard eerst, dan op familie-id), en de server legt die volgorde ook zelf op: van twee reviews met dezelfde klasse toont de rij die van de laatst bewaarde, bij gelijke tijd die van het laagste familie-id. De classificatiefilter houdt, zoals die van de familiepagina (`list_matching_small_variant_review_ids`), een variant over op de klasse die de review of de review van het paar toont, met dezelfde regel in SQL (`review_class_label_sql`, hoofdstuk 10); zo vindt hij ook een review met alleen een klassetag. Hij houdt een variant over waarvan één review die klasse toont, zoals de familiepagina van die familie; de rij kan de ernstigere klasse van een andere familie tonen.

**Waar in de code:** `resolve_scope` in `backend/app/services/variant_explorer_service.py`; de rolregels in `backend/app/services/access_control.py`.

## Snelheid

De Explorer bevraagt mogelijk vele projecten en miljoenen genotypes. Enkele keuzes houden dat werkbaar:

- **Verdeling per project.** De tabel `entries` is verdeeld per project (hoofdstuk 3). Elke vraag begint met de projectfilter, dus slaat ClickHouse alle andere projecten meteen over. Dat is snelheid én de fysieke basis van de afscherming.
- **Begrensde telling.** Boven een vaste grens meldt de Explorer het aantal als "N+" (`total_is_estimated`), in plaats van alles exact te tellen.
- **De paren (familie, sample).** De paren van de leden die de families tonen, staan letterlijk in de query: twee per lid (naam en UUID), elk zowat 60 bytes. Tienduizend leden geven zo'n 1,2 MB, binnen de 16 MiB van `CLICKHOUSE_MAX_QUERY_SIZE`. De Explorer toetst eerst het genotype en zoekt het paar alleen op voor een call met een ALT-allel. Op een synthetisch cohort van 5 miljoen rijen in 400 families kost de begrensde telling zo 0,6 tot 0,95 s (ongeveer 0,3 s zonder die paren); de lijstvraag is even snel als zonder (ongeveer 2,1 tot 2,9 s, binnen de spreiding van de meting).
- **Keyset-paginering.** De cursor onthoudt de laatste rij, de sortering en een vingerafdruk van de filters. Een cursor van een andere sortering of andere filters wordt genegeerd, zodat niemand ongemerkt verkeerd bladert.
- **Annotatiefilters per transcript** (zie *Filters*). De tabel `annotations` heeft één rij per transcript, de kleinere annotatie-index één per variant. Een locus (chromosoom, begin, einde) begrenst ook de gelezen transcriptrijen, de genindex en de rijen van de voorwaarden per sample, zoals op de familiepagina. De weergavevelden komen alleen voor de zichtbare pagina.
- **De annotatie van een rij op sleutel en set.** Elke annotatiefilter bouwt een verzameling van (variantsleutel, annotatieset) over het hele cohort, zonder de annotatieversie. In een meting met de versie erbij duurde een zoekopdracht met een frequentie-, impact- of CADD-filter op 4 miljoen rijen van 2 miljoen varianten 0,6 tot 1,0 s, zonder 0,23 tot 0,47 s, met zowat 10 % minder geheugen (hoofdstuk 8).
- **De regio's van een panel als join** (zie *Filters*). Op een synthetisch cohort van 2 miljoen varianten en 7,6 miljoen rijen in 40 families vond die join de varianten van een panel van 1000 regio's in een twintigste van een seconde. De toets per rij deed er over hetzelfde cohort bijna drie minuten over. Met het Mendeliome op positie (zowat 42.000 loci, samengevoegd tot ongeveer één regio per gen) duurde de begrensde telling op een synthetisch cohort van 1,19 miljoen varianten 0,16 s, tegen 0,10 s alleen op gensymbool (`docs/variant-ranking-cache.md`).

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/variant_explorer.py` | De endpoints en het inlezen van de filters |
| `backend/app/services/variant_explorer_service.py` | Scope, tellingen, paginering, dragers en de brug naar de reviews |
| `backend/app/services/clickhouse_variant_queries.py` | De filters, gedeeld met de familiezoekopdracht |
| `backend/app/services/genotypes.py` | De genotypeklassen |
| `backend/app/services/access_control.py` | De rolregels die de scope voeden |
| `backend/app/services/clickhouse_variant_storage.py` | De tabel `entries`, verdeeld per project |
| `frontend/src/pages/variant-explorer/GlobalSmallVariantExplorerPage.tsx` | Het scherm |
| `frontend/src/pages/variant-explorer/globalSmallVariantSearch.ts` | Zoektoestand, cursor, querystring |
| `frontend/src/pages/variant-explorer/VariantCarrierModal.tsx` | Het dragervenster per familie |
