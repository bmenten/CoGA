# 15. Overige modules & adminfunctionaliteit

Dit hoofdstuk behandelt de modules zonder eigen hoofdstuk: de Clinical CNV Explorer met de kennisbank van klinische CNV's, de genpanels, de beheerfuncties en hun rechten, de in-app documentatie, de releasepagina en de UI-telemetrie. De uitleg voor beheerders staat in de gebruikersgids in de app (`frontend/src/content/docs/user-guide/administration.md`); het beheer van familieleden en stamboom in `docs/family-member-management.md`; alle tabellen in `docs/database.md`.

## Toegang als rode draad

Bijna elke module hangt aan een van twee poorten (hoofdstuk 2):

| Dependency | Betekenis | Toegepast op |
| --- | --- | --- |
| `get_current_user` | Elke ingelogde gebruiker met een geldig token | De CNV-catalogus, panels lezen, releases, UI-events insturen |
| `get_current_admin_user` | Alleen beheerders: de rol `admin` of `superuser` (`ADMIN_ROLES`) | De volledige beheerrouter `/api/admin`, panels aanmaken en wijzigen, de PanelApp-import |

De actor van een handeling komt altijd uit het token, nooit uit de inhoud van het verzoek.

## Clinical CNV Explorer

De Clinical CNV Explorer (`/cnv-explorer`) laat curatoren de **kennisbank van terugkerende CNV-syndromen** doorbladeren (bv. microdeletiesyndromen), samengesteld uit o.a. ClinGen/ISCA, DECIPHER, OMIM/Orphanet en ClinVar. De pagina toont per CNV de gegevens uit de kennisbank en de steun in ClinVar: het aantal (waarschijnlijk) pathogene ClinVar-CNV's (**ClinVar P/LP**) per kant, verlies en winst. Elke rij linkt naar een detailpagina (`/cnv-details/{id}`), die ook de ondersteunende ClinVar-records linkt.

De router `/api/cnvs` heeft drie leesendpoints, alle achter een login: één CNV op id, de doorzoekbare catalogus per assembly, en de CNV's die een genomisch bereik overlappen (voor de tracks, hoofdstuk 9).

**De kennisbank herbouwen.** Een beheerder kan de kennisbank opnieuw laten opbouwen vanaf de pagina *Organisms and assemblies* (`/reference-data`). Het zware bouwscript (`scripts/clinical_cnv_knowledgebase.py`, dat o.a. ClinVar ophaalt) draait als een **apart proces**, zodat de downloads en de extra afhankelijkheden het API-proces niet raken. Het resultaat wordt via de gewone referentielader in `clinical_cnvs` geladen en als import geregistreerd (hoofdstuk 13). De voortgang staat in `clinical_cnv_kb_jobs`, met wie het vroeg, de tijdstippen, het aantal rijen en een eventuele fout. Ontbreekt het script op de server, dan zegt de status dat en weigert de backend een herbouw (`503`).

**ClinVar-steun.** Per regio telt de build de (waarschijnlijk) pathogene ClinVar-CNV's (niet de "conflicting") die de regio voldoende wederzijds overlappen, apart voor verlies en winst. De kant volgt eerst uit het type in ClinVar, dan uit het kopieaantal in de naam van een array-record, en pas dan uit woorden in de naam. De tellingen worden alleen geschreven als ClinVar echt geraadpleegd werd; een kennisbank zonder ClinVar staat als leeg in de tabel en leest in de app als "not recorded", nooit als 0.

**Een herbouw kan vastlopen.** De databank laat hooguit één job met status `queued` en één met status `running` tegelijk toe. Een aanvraag tijdens een lopende herbouw wordt daardoor aanvaard; die nieuwe job kan niet naar `running`, blijft op `queued` staan en blokkeert zo elke volgende herbouw (`409`). Ook een herbouw die loopt wanneer de server herstart, blijft op `running` staan: er is geen herstel voor een verweesde job. De knop staat uit zolang er een job actief is, wat het risico verkleint maar niet wegneemt. Er is geen scherm of endpoint om zo'n job op te ruimen; dat vraagt een ingreep in de databank.

**Waar in de code:** `backend/app/routers/cnvs.py`; de herbouw in `backend/app/services/clinical_cnv_kb_jobs.py` en de endpoints `/api/admin/clinical-cnv-kb/...`; de pagina's `frontend/src/pages/cnv-explorer/ClinicalCnvExplorerPage.tsx` en `frontend/src/pages/genome/CnvDetailsPage.tsx`.

## Genpanels

Een **genpanel** is een benoemde lijst genen (eventueel met regio's en repeatloci) waarmee je variantfilters afbakent. De catalogus voedt het panelfilter op de filterpagina's (hoofdstuk 8) en het panellidmaatschap in de Gene Explorer (hoofdstuk 13).

- **Lezen** (elke ingelogde gebruiker): de lijst, één panel, zijn versies, en zoeken in PanelApp.
- **Schrijven** (alleen beheerders): aanmaken, wijzigen, verwijderen, een PanelApp-panel importeren en het Mendeliome opnieuw opbouwen.

**Versies.** Elke wijziging aan een panel bewaart een onveranderlijke versie met de volledige genen- en regiolijst, de bron en de externe versie, en wie de wijziging deed (ook als tekst, zodat die herkenbaar blijft als het account verdwijnt). Zo is terug te vinden welke genset een panel op een bepaald moment had. **Let op:** een panel verwijderen wist ook zijn versiegeschiedenis.

**PanelApp.** Beheerders zoeken en importeren panels uit Genomics England PanelApp, met keuzes voor het betrouwbaarheidsniveau, de assembly en het al dan niet meenemen van regio's en repeats. Het PanelApp-id, de versie en de bron-URL worden bij het panel bewaard.

**Mendeliome.** Een gegenereerd panel met de genen die in de Monarch-kennisgraaf een oorzakelijke of geassocieerde relatie met een aandoening hebben. Het wordt opnieuw opgebouwd, en dus opnieuw geversioneerd, wanneer een nieuwe Monarch-release wordt geladen (hoofdstuk 12).

De schrijfknoppen zijn in de UI verborgen voor niet-beheerders, maar de afscherming zit op de server.

**Waar in de code:** `backend/app/routers/panels.py`, `backend/app/services/panel_metadata_service.py` en `panelapp_service.py`; de pagina's in `frontend/src/pages/panels/`.

## Beheerfuncties

De hele beheer-API staat in `backend/app/routers/admin.py` (`/api/admin`); elk endpoint vraagt `get_current_admin_user`. De schermen staan in `frontend/src/pages/admin/`, met `AdminDashboardPage.tsx` als ingang.

### Gebruikers en projecten

- **Gebruikers:** per gebruiker e-mail, naam, affiliatie, rol, actief en projecttoegang. Een beheerder activeert of deactiveert een account (`PATCH /api/auth/users/{user_id}`); projecttoegang wordt daar alleen getoond.
- **Projecten van een familie:** welke projecten een familie zien (`GET /api/admin/projects`, `PUT /api/admin/families/{family_id}/projects`).

### Datamanagement en herkomst

- **Datamanagement** (`DataManagementPage.tsx`): per familie de leden, samples, projecttoegang, assaydata en ruwe bronbestanden, met per soort data het aantal rijen in Postgres en ClickHouse. Elke verwijdering (per datasoort van een sample of familie, of een volledig sample of volledige familie) vraagt `confirm=true`.
- **Bronbestanden** (`RawFileProvenanceTable.tsx`): alle ruwe bronbestanden van een familie met opslagpad, grootte en SHA-256. Een bestand is te downloaden (`410` als het niet meer op zijn pad staat) en te **verifiëren**: de backend berekent de SHA-256 opnieuw en vergelijkt met de opgeslagen waarde (hoofdstuk 6).
- **Stamboomexport:** een PED-bestand per familie.
- **Familiestructuur:** leden, ouder-kindrelaties en partners bewerken, met een controle die inconsistente stambomen weigert. Vooraf toont de backend wat een wijziging raakt, en het hernoemen of verwijderen van een lid wordt geweigerd zolang er genomische data aan hangt. Zie `docs/family-member-management.md`.

### ClickHouse-onderhoud

Per assembly: de status van de tabellen, en onderhoudsacties achter een bevestiging: tabellen aanmaken of bijwerken, onderdelen samenvoegen (*optimize*), de gen-index van de small variants herbouwen, en een integriteitscontrole (hoofdstuk 11).

### Familiestatussen

De catalogus van werkstatussen (bv. "Solved", "Analysis in progress"). Een status verwijderen verwijdert geen familie: haar statusveld wordt leeg. De status en de toewijzing van een familie zelf zet elke gebruiker met toegang tot die familie.

### Grenzen voor sequencing-QC

Op de pagina *Sequencing QC thresholds* stelt een beheerder per QC-metriek een **waarschuwings-** en een **foutgrens** in, per profiel:

- **Een profiel per assay.** Eén grens kan geen twee assays dienen; wat normaal is voor een long-read-genoom, is een fout voor een panel. Een familie gebruikt het profiel uit haar metadata, en anders het standaardprofiel (er is er precies één).
- **De metrieken staan in de code, de grenzen in de databank.** Welke metrieken bestaan en of een lage of een hoge waarde de foutkant is, volgt uit wat de QC-parsers schrijven; een beheerder kan de richting niet omdraaien.
- **Geen standaardgrenzen.** Een grens is een klinische beslissing; er worden er geen meegeleverd. Zonder grens wordt een metriek gemeld als *niet beoordeeld*, nooit als geslaagd.
- **Eén oordeel voor iedereen.** De backend evalueert de grenzen, zodat de werkruimte, de sample-QC en het rapport hetzelfde oordeel tonen. Ook de mtDNA-grenzen komen van hier.
- **Streng vastgelegd.** Een wijziging vraagt een bevestiging die beide waarden herhaalt en een **reden**. Ze komt in de append-only tabel `qc_threshold_changes`, samen met de waarde die ze vervangt en de reden. Een trigger weigert wijzigen en wissen, en de runtime-rol `coga_app` heeft er geen rechten voor (hoofdstuk 2). De HTTP-auditlog kent de vorige waarde niet: alleen hier is te zien wie een grens verlaagde en van welke waarde.

**Waar in de code:** `backend/app/services/qc_threshold_service.py`; de pagina `AdminQcThresholdsPage.tsx`.

### Presets en tags

Een overzicht van de gedeelde filterpresets, en het beheer van de eigen varianttags (hoofdstuk 10).

### Auditlogs en integriteit

- **Auditlogs** (`AdminAuditLogsPage.tsx`): de HTTP-auditlog (*Requests*), te filteren op methode, status, gebruiker en pad, met per verzoek de afgeleide databankwijziging; en de UI-telemetrie (*Interactions*).
- **Integriteit:** de hash-keten van een familie nalopen, en integriteitsankers maken en controleren (hoofdstuk 11). Daarvoor bestaan alleen endpoints, geen scherm.

### Overige

Het beheer van de NIPT-artefacten (de lijst terugkerende artefacten per assay, met een automatische eerste vulling uit het cohort; hoofdstuk 8), de synchronisatie van de genreferentie (hoofdstuk 13), en het beheer van HPO en Monarch (hoofdstuk 12).

## In-app documentatie

De documentatie voor gebruikers zit in de app zelf, onder `/docs`.

- **Referentiedocumenten:** Markdown-bestanden in `frontend/src/content/docs/`, geregistreerd in `frontend/src/pages/docs/referenceDocs.ts` en getoond op `/docs/reference/<slug>`.
- **De gebruikersgids:** één Markdown-bestand per sectie in `frontend/src/content/docs/user-guide/`, met de lijst van secties in `frontend/src/pages/docs/userGuideSections.ts`.

De gebruikersgids hoort bij de informatie voor veilig gebruik (`docs/regulatory/TF-15-instructions-for-use.md`). Een test legt de tekst van elke sectie vast (`UserGuideContent.test.tsx`), zodat elke tekstwijziging bewust gebeurt.

## Releases

De pagina *New features* (`NewFeaturesPage.tsx`) toont de versiegeschiedenis uit de GitHub-releases (`GET /api/product/releases`, achter een login). De backend vraagt de GitHub-API op, met een token als dat is ingesteld. Lukt dat niet, dan geeft hij een nette foutmelding en verwijst de pagina rechtstreeks naar GitHub. Het resultaat wordt een tijd bewaard, zodat GitHub niet bij elke opening wordt bevraagd. Deze catalogus is informatie voor de gebruiker; de softwareversie die een rapport bindt, komt uit de build (hoofdstuk 11).

**Waar in de code:** `backend/app/routers/product.py` en `backend/app/services/github_releases_service.py`.

## UI-telemetrie

Naast de HTTP-auditlog legt CoGA **betekenisvolle interacties** in de interface vast die de backend anders niet ziet: klikken op knoppen, links en tabs, en navigatie binnen de app.

- **In de browser** vangt `frontend/src/lib/telemetry.ts` die gebeurtenissen op en stuurt ze in groepjes naar `POST /api/ui-events`, ook bij het verlaten van de pagina. Telemetrie is *best effort*: een netwerkfout laat een gebeurtenis vallen, maar verstoort de app nooit.
- **Op de server** is `ingest_ui_events` de filter: alleen een vaste lijst soorten wordt aanvaard (`click`, `navigation`, `submit`, `view`, `query`); in paden worden id's gemaskeerd en querystrings tot hun sleutels teruggebracht, zodat klinische identificaties niet worden opgeslagen; gevoelige velden (wachtwoord, token, …) worden gemaskeerd en geneste structuren tot hun type herleid. De actor komt uit het token.
- **Opslag:** de gebeurtenissen gaan naar de tabel `ui_events`, via dezelfde verliesvrije wachtrij als de HTTP-auditlog (hoofdstuk 7).

**Waar in de code:** `frontend/src/lib/telemetry.ts`, `backend/app/routers/ui_events.py` en `backend/app/services/ui_event_pg.py`.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/admin.py` | De volledige beheer-API, alleen voor beheerders |
| `backend/app/routers/cnvs.py` · `backend/app/services/clinical_cnv_kb_jobs.py` | De CNV-catalogus en de herbouw van de kennisbank |
| `backend/app/routers/panels.py` · `backend/app/services/panel_metadata_service.py` | Genpanels, versies, Mendeliome |
| `backend/app/services/panelapp_service.py` | PanelApp |
| `backend/app/services/admin_service.py` | Data-inventaris, verwijderen, bronbestanden verifiëren |
| `backend/app/services/qc_threshold_service.py` | Grenzen voor sequencing-QC en hun geschiedenis |
| `backend/app/services/family_structure_service.py` · `family_member_management_service.py` | Familiestructuur en ledenbeheer |
| `backend/app/routers/ui_events.py` · `backend/app/services/ui_event_pg.py` | UI-telemetrie |
| `frontend/src/pages/admin/` | De beheerschermen |
| `frontend/src/pages/docs/` · `frontend/src/content/docs/` | De in-app documentatie |
