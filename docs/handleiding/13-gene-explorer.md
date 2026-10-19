# 13. Gene Explorer & versiecontrole

Dit hoofdstuk beschrijft hoe CoGA een genprofiel opbouwt en toont: van een gensymbool (bv. `BRCA1`) naar een pagina met transcripten, constraint-metrieken (maten voor hoe gevoelig een gen is voor mutaties), ziekte- en fenotypeassociaties en externe links. Het beschrijft ook uit welke bronnen die informatie komt, waarom CoGA ze in Postgres bewaart in plaats van ze telkens live op te vragen, hoe een verversing als achtergrondjob draait, en hoe elke import van referentiedata met bron, tijdstip en uitvoerder wordt vastgelegd. De Engelse referentie voor de synchronisatie van genen is `docs/data-import.md`.

Een **cache** is een lokale kopie die je niet telkens opnieuw hoeft op te halen; een **worker** is een achtergrondproces dat langlopend werk doet, los van het webverzoek.

## De Gene Explorer

De Gene Explorer is de pagina `/genes` (`frontend/src/pages/genes/GeneInfoPage.tsx`). De gebruiker typt een symbool, kiest een suggestie en ziet één samengevoegd profiel. De pagina gebruikt twee endpoints, beide achter een login:

| Endpoint | Levert |
| --- | --- |
| `GET /api/genes/search?q=` | Suggesties: symbolen die met de invoer beginnen (minstens twee tekens) |
| `GET /api/genes/profile?symbol=…` | Het volledige genprofiel |

Het profiel toont onder meer: de plaats van het gen op GRCh38 en, waar beschikbaar, op T2T-CHM13 en GRCh37; de transcripten met hun badges (MANE, Canonical, CCDS, …); constraint-metrieken (gnomAD pLI en LOEUF, pHaplo/pTriplo, missense-Z); ziekte- en fenotypeassociaties (OMIM, GenCC, ClinGen, Orphanet, ClinVar, en Monarch met de fenotypes die de familie deelt); of het gen op de ACMG-lijst van nevenbevindingen staat (hieronder); de panels waarin het gen zit; externe links (Ensembl, NCBI, OMIM, ClinGen, gnomAD, …); en per bron een status met het tijdstip van de laatste verversing. Die bronstatus maakt de herkomst rechtstreeks op de pagina zichtbaar.

**ACMG-nevenbevindingen.** De rij *ACMG secondary finding* zegt of het gen op de ACMG SF-lijst staat: de genen die de ACMG aanbeveelt te melden als nevenbevinding (*secondary finding*) bij exoom- en genoomsequencing. CoGA levert die lijst mee (ACMG SF v3.3, hieronder). Staat het gen erop, dan toont de rij de versie, met een link naar de publicatie, en het fenotype, de categorie, de overerving, de te melden varianten en de voetnoten die de lijst voor het gen geeft (zoals bij *TTN*: alleen truncerende varianten). Anders staat er *Not on ACMG SF v3.3*. *Not checked* betekent dat de lijst voor dit gen niet gelezen is: de geninformatie werd ververst voordat CoGA de lijst had, of de lijst kon niet gelezen worden. De rijen *Clinical actionability* en *Variant pathogenicity* zijn weg: geen enkele bron vulde ze, zodat ze bij elk gen "—" toonden.

**ClinGen-dosage.** Onder *ClinGen dosage assertions* staan twee soorten items. *ClinGen dosage curation* komt uit ClinGens eigen dosagebestand: de haploinsufficiëntie en de triplosensitiviteit in woorden, de datum van de curatie en een link naar ClinGens rapport. Het item met een *HI score* is dezelfde haploinsufficiëntiescore zoals dbNSFP ze meegeeft.

Het profiel combineert twee Postgres-tabellen: **`genes`** (de referentiegenen per assembly, met chromosoom, positie, exonen en transcript) en **`gene_info`** (de bewaarde verrijking: naam, samenvatting, aliassen, id's, constraint, ziekteassociaties). Staat een gen zowel op een gewoon chromosoom als op een alternatieve sequentie, dan wint het gewone chromosoom. Is er voor de gekozen assembly nog geen verrijking, dan neemt het profiel de meest recente verrijking van hetzelfde symbool op een andere menselijke assembly.

**Toegang.** Wordt een familie of project meegegeven (voor de fenotype-matching), dan controleert de backend eerst de toegang. Een familie of project buiten de projecten van de gebruiker krijgt hetzelfde antwoord als een id dat niet bestaat (`404 Family not found` of `404 Project not found`). Zo lekt het profiel geen familiecontext, en ook niet welke families of projecten er bestaan.

**Waar in de code:** `backend/app/routers/genes.py` en `build_gene_profile` in `backend/app/services/gene_metadata_service.py`.

## Transcriptbadges

Niet elk transcript is klinisch gelijkwaardig. **MANE Select** is het aanbevolen referentietranscript, **MANE Plus Clinical** een klinisch belangrijk extra transcript, en **Ensembl Canonical** en **CCDS** zijn aanvullende referenties. De badges komen eerst uit de markeringen die de genannotatie per transcript meegeeft (GENCODE labelt MANE en Canonical per transcript, en geeft het CCDS-id). Alleen als die ontbreken, vergelijkt de pagina het transcript-id met de referentietranscripten in het profiel, zonder rekening te houden met het versienummer (`NM_007294.4` = `NM_007294`). Geannoteerde transcripten staan bovenaan, MANE eerst.

Bij het tekenen van genen in een genoomvenster kiest de backend per gen het beste transcript op dezelfde manier: MANE Select, dan Ensembl Canonical, dan de rest.

**Waar in de code:** `GeneInfoPage.tsx`; de keuze per gen in `backend/app/services/reference_metadata_service.py`.

## Waar de gegevens vandaan komen

**Referentiegenen.** Voor GRCh38 komen de genen uit de GENCODE-annotatie; voor T2T-CHM13 uit UCSC's RefSeq-afgeleide annotatie. Lukt dat niet, dan valt CoGA terug op een UCSC-gentabel en legt het vast dat het dat deed (hoofdstuk 4).

**De verrijking** komt uit twee soorten bronnen:

- **Bestanden in bulk**, per job één keer ingelezen en per symbool opgezocht: de **HGNC complete set** (het register van welke genen bestaan, met vorige symbolen, aliassen en id's), het lokale **dbNSFP**-genbestand (de rijkste bron: constraint, OMIM- en Orphanet-associaties, GO-termen, pathways, HPO-termen, expressie, orthologen), **ClinGen** gene validity en dosage, **GenCC**, **ClinVar** gene-condition en de **ACMG SF-lijst**. Elke bulkbron wordt voor elk gen geraadpleegd.
- **De ACMG SF-lijst** komt met CoGA mee in `data/ref-data/acmg-sf/`: ACMG SF v3.3 (*Genetics in Medicine*, 2025), 84 genen. De README daar noemt de publicatie, hoe de lijst overgenomen en nagekeken werd, en hoe je ze bijwerkt (een gecontroleerde wijziging). Een gen wordt op de lijst gevonden via zijn HGNC-id, zodat een gen dat HGNC sinds de publicatie hernoemde, er nog op staat.
- **Eén live bron per gen:** **NCBI Gene**, voor een samenvatting van genen die dbNSFP niet dekt, en alleen tijdens een verversing, nooit bij het openen van een pagina. De vragen aan NCBI gaan over het hele proces één per 0,4 seconde de deur uit, onder de limiet van drie per seconde die NCBI zonder API-sleutel hanteert; geeft de HGNC complete set het NCBI Gene-id, dan volstaat één vraag in plaats van twee.

Welke genen bestaan, bepaalt **HGNC**, niet de annotatie. Een locus met een symbool dat HGNC niet kent, is geen gen en krijgt geen verrijking. Een hernoemd symbool wordt via de vorige symbolen en aliassen naar het huidige gevouwen; een oud symbool waar twee genen aanspraak op maken, wordt weggelaten in plaats van geraden. Kon de HGNC complete set niet geladen worden, dan staat HGNC bij elk gen op `error` en neemt de verversing de symbolen van de annotatie zoals ze zijn, zonder hernoemde symbolen te vouwen.

**Herkomst per bron.** Elke bron krijgt per gen een status: `success` (de bron had een record), `missing` (bevraagd, maar niets voor dit gen), `not_consulted` (nooit bevraagd: géén uitspraak over dekking) of `error` (downloaden of inlezen mislukte). Eén uitzondering: ontbreekt het lokale dbNSFP-bestand, dan staat dbNSFP bij elk gen op `missing`, zonder uitgave en met de melding dat het bestand niet gevonden werd. Voor de ACMG SF-lijst betekent `missing` "niet op de lijst"; daarom wordt een lijst die ontbreekt, niet leesbaar is of niet volledig (geen versie, een gen zonder symbool, HGNC-id, fenotype of categorie, een gen dat twee keer voorkomt, of een ander aantal genen dan ze zelf opgeeft) in haar geheel geweigerd: elk gen krijgt dan `error`, en de genpagina zegt dat de lijst niet gelezen kon worden, nooit dat het gen er niet op staat. Bij elke status horen het tijdstip, de URL en, waar de bron het vermeldt, de **uitgave** die het antwoord gaf, met de SHA-256 van de ingelezen bytes. Een bron die geen uitgave vermeldt (ClinVar), krijgt er bewust geen, in plaats van een verzonnen versie. De URL's van de bulkbronnen en het pad naar dbNSFP zijn instellingen (`.env.example`).

**Genpanels van PanelApp** worden niet voor de verrijking gebruikt maar als panels geïmporteerd; hoofdstuk 15 beschrijft dat.

**Waar in de code:** `backend/app/services/gene_info_bulk_sources.py` (de bulkbronnen) en `gene_info_external.py` (NCBI en het samenvoegen).

## Waarom en hoe CoGA dit bewaart

De externe bronnen worden **niet** bij elk paginabezoek bevraagd. Dat zou traag en kwetsbaar zijn, en niet reproduceerbaar: twee reviewers konden verschillende data zien. De verrijking staat daarom in de Postgres-tabel **`gene_info`**, één rij per assembly en symbool, met de bronstatus en het tijdstip van de laatste verversing. De pagina leest alleen uit die tabel.

**Verversen als achtergrondjob.** Een volledige verversing omvat duizenden genen en draait daarom als job in `gene_info_refresh_jobs`, voor één gen of voor alle menselijke genen. Er kan maar **één actieve job** tegelijk zijn; de databank dwingt dat af, en een tweede aanvraag krijgt `409`. Bij elke job wordt bewaard wie hem aanvroeg (het e-mailadres van de beheerder, of `startup-bootstrap` voor de eerste job bij de installatie). Een worker neemt de job, verwerkt de genen en schrijft de voortgang geregeld weg; een job waarvan de worker stopte, wordt na een tijd opnieuw opgepakt.

**Beheer.** Op de pagina *Gene reference sync* (`GeneReferenceAdminPage.tsx`) kan een beheerder één gen of alle genen laten verversen, de actieve job volgen, de dekking per bron bekijken (met de kolommen *Release*, *No record* en *Not consulted*) en de recente jobs zien. Alle endpoints daarvoor (`/api/admin/gene-reference/...`) zijn alleen voor beheerders.

**Waar in de code:** `backend/app/services/gene_info_jobs_pg.py`; de worker start en stopt in de `lifespan` van `backend/app/main.py`.

## Versiecontrole van referentiedata

Voor reproduceerbaarheid onder de IVDR wordt elke import van referentiedata vastgelegd. Elke import van genen, cytobanden, blacklist, klinische CNV's, segmentale duplicaties of DGV, via een upload, de automatische import, een herbouw van de CNV-kennisbank of het DGV-importscript (`scripts/import_dgv.py`), schrijft een rij in **`reference_dataset_imports`**: de assembly, de soort dataset, het aantal rijen, of bestaande data vervangen werd, de bron en haar URL, de uitgave die de bron over zichzelf vermeldt, wie de import deed en wanneer. Alleen GENCODE vermeldt een uitgave: de import neemt de versie en de datum uit de kop van de GTF. Bij de andere bronnen (de UCSC-tabellen en de T2T-GTF van UCSC, de CNV-kennisbank, uploads, de ingebouwde referentiebestanden en het DGV-script) staat `not stated`, zonder datum. De pagina *Organisms and assemblies* (`/reference-data`) toont die geschiedenis (*Recent reference activity*) en de status per assembly en dataset.

**Genpanels** krijgen een nieuwe versie telkens hun genen of regio's veranderen, ook door een nieuwe import uit PanelApp (een lokaal panel ook bij een nieuwe beschrijving). Elke versie wordt bewaard met de volledige genlijst; een panel verwijderen wist ook zijn versies (hoofdstuk 15).

Samen beantwoorden `reference_dataset_imports` (welke referentiedata actief was, sinds wanneer), de panelversies (welke genset een panel had) en de bronstatus in `gene_info` (uit welke bronnen en uitgaven de verrijking kwam) de vraag van een auditor: welke referentiedata lag onder dit resultaat? Het ondertekende rapport bevriest daarnaast de bron van de genloci en de Monarch-release (hoofdstuk 11).

**Waar in de code:** `backend/app/services/reference_metadata_service.py` (de imports en hun registratie) en `reference_source_service.py` (de automatische import); de pagina `frontend/src/pages/reference/ReferenceCatalogPage.tsx`.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/genes.py` | Zoeken, genprofiel, genen per regio |
| `backend/app/services/gene_metadata_service.py` | Het genprofiel uit `genes` en `gene_info`, met toegangscontrole |
| `backend/app/services/gene_info_bulk_sources.py` · `gene_info_external.py` | De bulkbronnen, de ACMG SF-lijst inbegrepen; NCBI en het samenvoegen |
| `data/ref-data/acmg-sf/` | De meegeleverde ACMG SF-lijst (v3.3) en haar README |
| `backend/app/services/gene_info_jobs_pg.py` | De verversingsjobs en de worker |
| `backend/app/services/reference_metadata_service.py` | Imports van referentiedata en hun registratie; transcriptkeuze |
| `backend/app/services/reference_source_service.py` | De automatische import van GRCh38 en T2T |
| `backend/db/schema/postgres/02_reference.sql` | `genes`, `gene_info`, `gene_info_refresh_jobs`, `reference_dataset_imports` |
| `frontend/src/pages/genes/GeneInfoPage.tsx` | De Gene Explorer, met de transcriptbadges |
| `frontend/src/pages/admin/GeneReferenceAdminPage.tsx` | Beheer van de synchronisatie |
| `frontend/src/pages/reference/ReferenceCatalogPage.tsx` | Assemblies, referentiedata en hun geschiedenis |
