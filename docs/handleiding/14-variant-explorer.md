# 14. Variant Explorer

Dit hoofdstuk beschrijft de *Global Small Variant Explorer*: een zoekfunctie die small variants (SNV's en indels) samenvat over **alle projecten waartoe een gebruiker toegang heeft**, in plaats van binnen één familie. Aan bod komen het scherm, hoe een zoekopdracht een geaggregeerde ClickHouse-vraag wordt, hoe de dragers geteld worden, en vooral hoe streng is afgedwongen dat resultaten nooit buiten de projecten van de gebruiker lekken. De uitleg voor gebruikers staat in de gebruikersgids in de app (sectie `explorers`).

Enkele begrippen:

- **Drager:** een sample dat het alternatieve allel draagt, heterozygoot of homozygoot.
- **Aggregatie:** per unieke variant tellen hoeveel samples en families haar dragen.
- **Assembly:** de versie van het referentiegenoom (bv. GRCh38); varianten van verschillende assemblies worden nooit samen geteld.
- **Keyset-paginering:** pagina per pagina door een groot resultaat bladeren zonder dure sprongen (`OFFSET`), door te onthouden waar de vorige pagina eindigde.

## Doel en scherm

De filterpagina van een familie (hoofdstuk 8) beantwoordt "welke varianten zitten in *deze* familie?". De Variant Explorer draait de vraag om: "in hoeveel samples en families van mijn toegankelijke cohort komt *deze* variant voor?". Elke rij is één unieke variant, met de tellers *Total samples*, *Het*, *Hom* en *Families*.

Het scherm (`/variant-explorer`) heeft een keuzelijst voor de assembly, een filter op genotype per sample, het gewone annotatiefilterformulier en de resultaattabel. Een klik op een teller opent een venster met de dragers, gegroepeerd per familie, met een link naar elke familie. Het formulier is dat van de familiepagina, maar zonder de velden voor familie en samples. Filters die de Explorer niet toepast (een intervallijst, transcript, uit te sluiten genen en intervallen, uit te sluiten tags, "heeft notities"), biedt het formulier hier niet aan, en hun parameters worden niet verstuurd.

**Waar in de code:** `frontend/src/pages/variant-explorer/` (`GlobalSmallVariantExplorerPage.tsx`, `globalSmallVariantSearch.ts`, `GlobalSmallVariantTable.tsx`, `VariantCarrierModal.tsx`).

## Endpoints

Alle endpoints staan onder `/api/variant-explorer` en vragen een login.

| Endpoint | Doel |
| --- | --- |
| `GET /variant-explorer/assemblies` | De toegankelijke assemblies, met het aantal projecten per assembly |
| `GET /variant-explorer/samples` | De samples die de gebruiker mag zien (voor de genotypefilter) |
| `GET /variant-explorer/small-variants` | De gepagineerde, geaggregeerde variantlijst |
| `GET /variant-explorer/small-variants/export` | Dezelfde resultaten als CSV (met een bovengrens op het aantal rijen) |
| `GET /variant-explorer/small-variants/{variant_key}/carriers` | De dragers van één variant, per familie |
| `GET /variant-explorer/small-variant-tags` | De tagdefinities van de toegankelijke projecten |

De filters worden op één plaats ingelezen, zodat de lijst en de CSV-export gegarandeerd dezelfde filters gebruiken.

**Waar in de code:** `backend/app/routers/variant_explorer.py`; de logica in `backend/app/services/variant_explorer_service.py`.

## Hoe de dragers geteld worden

De bron is de tabel `entries` in ClickHouse, niet een vooraf berekende samenvatting. De Explorer telt rechtstreeks uit `entries`, met `sign = 1`, zodat een nieuwe import of een verwijdering de tellingen niet opblaast. Per variant:

- worden de genotypes per sample uitgevouwen tot één regel per sample;
- tellen alleen echte dragers mee; *Het*, *Hom* en drager volgen de genotypeklassen van `backend/app/services/genotypes.py` (hoofdstuk 8), zodat bv. een haploïde `1` als homozygoot telt;
- wordt elk sample en elke familie maar één keer geteld, ook als het onder meerdere toegankelijke projecten voorkomt.

**De dragers per familie.** Het dragervenster haalt de dragers van één variant op (begrensd, met een melding als er meer zijn), houdt per familie en sample één record over (homozygoot gaat voor), en koppelt die aan de namen van familie, project en lid uit Postgres. Een familie die de gebruiker niet mag zien, ontbreekt in die koppeling, en haar drager wordt dan overgeslagen. De koppeling is dus tegelijk een tweede toegangscontrole.

## Filters

Bovenop de gewone annotatiefilters (gen, panel, impact, ClinVar, gnomAD, CADD, REVEL, SpliceAI, …) kent de Explorer twee soorten filters:

1. **Tags en classificaties.** Die staan in Postgres, per familie. Filtert de gebruiker erop, dan zoekt de Explorer eerst in Postgres de bijbehorende variant-id's binnen de toegankelijke projecten, en geeft die lijst mee aan de ClickHouse-vraag. Een lege lijst betekent meteen een leeg resultaat.
2. **Genotype per sample.** De gebruiker voegt regels toe als *sample: Het, Hom of Het+Hom*. Elke regel wordt een aparte voorwaarde; een variant moet aan alle regels voldoen.

Met de standaardinstelling "een (waarschijnlijk) pathogene ClinVar-variant passeert het frequentiefilter" gebruikt de Explorer dezelfde ClinVar-termen als de familiepagina.

Elke waarde die de gebruiker aanlevert, gaat als benoemde parameter (`%(naam)s`) in de query en wordt door de ClickHouse-client gebonden, nooit in de tekst geplakt (hoofdstuk 7).

## Afscherming tussen projecten

Dit is voor een auditor het belangrijkste deel. Elke vraag is hard beperkt tot de projecten van de gebruiker:

- De scope wordt op één plaats bepaald. **Beheerders** (`admin` of `superuser`) zien alle projecten; **andere gebruikers** alleen hun eigen projecten. Heeft een gebruiker geen projecten, dan is het resultaat gegarandeerd leeg.
- Elke ClickHouse-vraag krijgt die projecten verplicht mee (`project_guid IN …`): de lijstvraag, de begrensde telling, de voorwaarden per sample en de dragervraag. Omdat `project_guid` gelijk is aan de project-UUID in Postgres, vallen rijen van andere projecten weg voordat er iets geteld wordt.
- De frontend toont alleen een melding als er niets is ("… in your accessible projects"); de afscherming zit volledig op de server.

Het bestand `backend/app/services/data_scope.py` gaat, ondanks de naam, niet over deze afscherming maar over chromosoomnamen. Wie de toegangsscope zoekt, kijkt in `variant_explorer_service.py` (`resolve_scope`).

**Traceerbaarheid.** Elke drager is terug te voeren op zijn familie en project, en de review (tags, classificatie, laatste review) komt uit `small_variant_reviews` (hoofdstuk 10).

**Waar in de code:** `resolve_scope` in `backend/app/services/variant_explorer_service.py`; de rolregels in `backend/app/services/access_control.py`.

## Snelheid

De Explorer bevraagt mogelijk vele projecten en miljoenen genotypes. Enkele keuzes houden dat werkbaar:

- **Verdeling per project.** De tabel `entries` is verdeeld per project (hoofdstuk 3). Omdat elke vraag met de projectfilter begint, slaat ClickHouse alle andere projecten meteen over: dat is snelheid én de fysieke basis van de afscherming.
- **Begrensde telling.** Boven een vaste grens meldt de Explorer het aantal als "N+" (`total_is_estimated`) in plaats van alles exact te tellen.
- **Keyset-paginering.** De cursor onthoudt de laatste rij, de sortering en een vingerafdruk van de filters. Hoort een cursor bij een andere sortering of andere filters, dan wordt hij genegeerd, zodat niemand ongemerkt verkeerd bladert.
- **Aparte annotatie-index.** Annotatiefilters draaien tegen de annotatie-index, en de weergavevelden worden alleen opgehaald voor de zichtbare pagina.

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
