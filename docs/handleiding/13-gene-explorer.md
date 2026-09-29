# 13. Gene Explorer & versiecontrole

Dit hoofdstuk beschrijft hoe CoGA een genprofiel opbouwt en toont: van een gensymbool (bv. `BRCA1`) naar een pagina met transcripten, constraint-metrieken (maten voor hoe gevoelig een gen is voor mutaties), ziekte- en fenotypeassociaties en externe links. Het beschrijft ook uit welke bronnen die informatie komt, waarom CoGA ze in Postgres bewaart in plaats van ze telkens live op te vragen, hoe een verversing als achtergrondjob draait, en hoe elke import van referentiedata met bron, tijdstip en uitvoerder wordt vastgelegd. De Engelse referentie voor de synchronisatie van genen is `docs/data-import.md`.

Een **cache** is een lokale kopie die je niet telkens opnieuw hoeft op te halen; een **worker** is een achtergrondproces dat langlopend werk doet, los van het webverzoek.

## De Gene Explorer

De Gene Explorer is de pagina `/genes` (`frontend/src/pages/genes/GeneInfoPage.tsx`). De gebruiker typt een symbool, kiest een suggestie en ziet één samengevoegd profiel. De pagina gebruikt twee endpoints, beide achter een login:

| Endpoint | Levert |
| --- | --- |
| `GET /api/genes/search?q=` | Suggesties: symbolen die met de invoer beginnen (minstens twee tekens) |
| `GET /api/genes/profile?symbol=…` | Het volledige genprofiel |

Het profiel toont onder meer: de plaats van het gen op GRCh38 en, waar beschikbaar, op T2T-CHM13 en GRCh37; de transcripten met hun badges (MANE, Canonical, CCDS, …); constraint-metrieken (gnomAD pLI en LOEUF, pHaplo/pTriplo, missense-Z); ziekte- en fenotypeassociaties (OMIM, GenCC, ClinGen, Orphanet, ClinVar, en Monarch met de fenotypes die de familie deelt); de panels waarin het gen zit; externe links (Ensembl, NCBI, OMIM, ClinGen, gnomAD, …); en per bron een status met het tijdstip van de laatste verversing. Die bronstatus maakt de herkomst rechtstreeks op de pagina zichtbaar.

Het profiel combineert twee Postgres-tabellen: **`genes`** (de referentiegenen per assembly, met chromosoom, positie, exonen en transcript) en **`gene_info`** (de bewaarde verrijking: naam, samenvatting, aliassen, id's, constraint, ziekteassociaties). Staat een gen zowel op een gewoon chromosoom als op een alternatieve sequentie, dan wint het gewone chromosoom. Is er voor de gekozen assembly nog geen verrijking, dan neemt het profiel de meest recente verrijking van hetzelfde symbool op een andere menselijke assembly.

**Toegang.** Wordt een familie of project meegegeven (voor de fenotype-matching), dan controleert de backend eerst de toegang; een gebruiker zonder toegang krijgt `403`. Zo lekt het profiel geen familiecontext.

**Waar in de code:** `backend/app/routers/genes.py` en `build_gene_profile` in `backend/app/services/gene_metadata_service.py`.

## Transcriptbadges

Niet elk transcript is klinisch gelijkwaardig. **MANE Select** is het aanbevolen referentietranscript, **MANE Plus Clinical** een klinisch belangrijk extra transcript, en **Ensembl Canonical** en **CCDS** zijn aanvullende referenties. De badges komen eerst uit de markeringen die de genannotatie per transcript meegeeft (GENCODE labelt MANE en Canonical per transcript, en geeft het CCDS-id). Alleen als die ontbreken, vergelijkt de pagina het transcript-id met de referentietranscripten in het profiel, zonder rekening te houden met het versienummer (`NM_007294.4` = `NM_007294`). Geannoteerde transcripten staan bovenaan, MANE eerst.

Bij het tekenen van genen in een genoomvenster kiest de backend per gen het beste transcript op dezelfde manier: MANE Select, dan Ensembl Canonical, dan de rest.

**Waar in de code:** `GeneInfoPage.tsx`; de keuze per gen in `backend/app/services/reference_metadata_service.py`.

## Waar de gegevens vandaan komen

**Referentiegenen.** Voor GRCh38 komen de genen uit de GENCODE-annotatie; voor T2T-CHM13 uit UCSC's RefSeq-afgeleide annotatie. Lukt dat niet, dan valt CoGA terug op een UCSC-gentabel en legt het vast dat het dat deed (hoofdstuk 4).

**De verrijking** komt uit twee soorten bronnen:

- **Bestanden in bulk**, per job één keer ingelezen en per symbool opgezocht: de **HGNC complete set** (het register van welke genen bestaan, met vorige symbolen, aliassen en id's), het lokale **dbNSFP**-genbestand (de rijkste bron: constraint, OMIM- en Orphanet-associaties, GO-termen, pathways, HPO-termen, expressie, orthologen), **ClinGen** gene validity en dosage, **GenCC** en **ClinVar** gene-condition. Elke bulkbron wordt voor elk gen geraadpleegd.
- **Eén live bron per gen:** **NCBI Gene**, voor een samenvatting van genen die dbNSFP niet dekt, en alleen tijdens een verversing, nooit bij het openen van een pagina.

Welke genen bestaan, bepaalt **HGNC**, niet de annotatie. Een locus met een symbool dat HGNC niet kent, is geen gen en krijgt geen verrijking. Een hernoemd symbool wordt via de vorige symbolen en aliassen naar het huidige gevouwen; een oud symbool waar twee genen aanspraak op maken, wordt weggelaten in plaats van geraden.

**Herkomst per bron.** Elke bron krijgt per gen een status: `success` (de bron had een record), `missing` (bevraagd, maar niets voor dit gen), `not_consulted` (nooit bevraagd: géén uitspraak over dekking) of `error` (downloaden of inlezen mislukte). Bij elke status horen het tijdstip, de URL en, waar de bron het vermeldt, de **uitgave** die het antwoord gaf, met de SHA-256 van de ingelezen bytes. Een bron die geen uitgave vermeldt (ClinVar), krijgt er bewust geen, in plaats van een verzonnen versie. De URL's van de bulkbronnen en het pad naar dbNSFP zijn instellingen (`.env.example`).

**Genpanels van PanelApp** worden niet voor de verrijking gebruikt maar als panels geïmporteerd; hoofdstuk 15 beschrijft dat.

**Waar in de code:** `backend/app/services/gene_info_bulk_sources.py` (de bulkbronnen) en `gene_info_external.py` (NCBI en het samenvoegen).

## Waarom en hoe CoGA dit bewaart

De externe bronnen worden **niet** bij elk paginabezoek bevraagd. Dat zou traag en kwetsbaar zijn, en niet reproduceerbaar: twee reviewers konden verschillende data zien. De verrijking staat daarom in de Postgres-tabel **`gene_info`**, één rij per assembly en symbool, met de bronstatus en het tijdstip van de laatste verversing. De pagina leest alleen uit die tabel.

**Verversen als achtergrondjob.** Een volledige verversing omvat duizenden genen en draait daarom als job in `gene_info_refresh_jobs`, voor één gen of voor alle menselijke genen. Er kan maar **één actieve job** tegelijk zijn; de databank dwingt dat af, en een tweede aanvraag krijgt `409`. Bij elke job wordt bewaard wie hem aanvroeg (het e-mailadres van de beheerder, of `startup-bootstrap` voor de eerste job bij de installatie). Een worker neemt de job, verwerkt de genen en schrijft de voortgang geregeld weg; een job waarvan de worker stopte, wordt na een tijd opnieuw opgepakt.

**Beheer.** Op de pagina *Gene reference sync* (`GeneReferenceAdminPage.tsx`) kan een beheerder één gen of alle genen laten verversen, de actieve job volgen, de dekking per bron bekijken (met de kolommen *Release*, *No record* en *Not consulted*) en de recente jobs zien. Alle endpoints daarvoor (`/api/admin/gene-reference/...`) zijn alleen voor beheerders.

**Waar in de code:** `backend/app/services/gene_info_jobs_pg.py`; de worker start en stopt in de `lifespan` van `backend/app/main.py`.

## Versiecontrole van referentiedata

Voor reproduceerbaarheid onder de IVDR wordt elke import van referentiedata vastgelegd. Elke import van genen, cytobanden, blacklist, klinische CNV's, segmentale duplicaties of DGV, via een upload, de automatische import, een herbouw van de CNV-kennisbank of het DGV-importscript (`scripts/import_dgv.py`), schrijft een rij in **`reference_dataset_imports`**: de assembly, de soort dataset, het aantal rijen, of bestaande data vervangen werd, de bron en haar URL, wie de import deed en wanneer. De pagina *Organisms and assemblies* (`/reference-data`) toont die geschiedenis (*Recent reference activity*) en de status per assembly en dataset.

**Genpanels** krijgen bij elke wijziging een onveranderlijke versie met de volledige genlijst (hoofdstuk 15).

Samen beantwoorden `reference_dataset_imports` (welke referentiedata actief was, sinds wanneer), de panelversies (welke genset een panel had) en de bronstatus in `gene_info` (uit welke bronnen en uitgaven de verrijking kwam) de vraag van een auditor: welke referentiedata lag onder dit resultaat? Het ondertekende rapport bevriest daarnaast de bron van de genloci en de Monarch-release (hoofdstuk 11).

**Waar in de code:** `backend/app/services/reference_metadata_service.py` (de imports en hun registratie) en `reference_source_service.py` (de automatische import); de pagina `frontend/src/pages/reference/ReferenceCatalogPage.tsx`.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/genes.py` | Zoeken, genprofiel, genen per regio |
| `backend/app/services/gene_metadata_service.py` | Het genprofiel uit `genes` en `gene_info`, met toegangscontrole |
| `backend/app/services/gene_info_bulk_sources.py` · `gene_info_external.py` | De bulkbronnen; NCBI en het samenvoegen |
| `backend/app/services/gene_info_jobs_pg.py` | De verversingsjobs en de worker |
| `backend/app/services/reference_metadata_service.py` | Imports van referentiedata en hun registratie; transcriptkeuze |
| `backend/app/services/reference_source_service.py` | De automatische import van GRCh38 en T2T |
| `backend/db/schema/postgres/02_reference.sql` | `genes`, `gene_info`, `gene_info_refresh_jobs`, `reference_dataset_imports` |
| `frontend/src/pages/genes/GeneInfoPage.tsx` | De Gene Explorer, met de transcriptbadges |
| `frontend/src/pages/admin/GeneReferenceAdminPage.tsx` | Beheer van de synchronisatie |
| `frontend/src/pages/reference/ReferenceCatalogPage.tsx` | Assemblies, referentiedata en hun geschiedenis |
