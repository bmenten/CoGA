# 15. Overige modules & adminfunctionaliteit

In dit hoofdstuk sluiten we de handleiding af met de modules die geen eigen hoofdstuk kregen, zodat de volledige codebase gedekt is. Dit hoofdstuk beschrijft hoe curatoren de klinische-CNV-kennisbank doorbladeren, hoe herbruikbare genpanelen worden beheerd, wat elke adminfunctie precies doet en welke rechten daarvoor nodig zijn, hoe de in-app documentatie en de releasepagina werken, en hoe de UI-telemetrie (klik- en navigatiegedrag van gebruikers) in dezelfde duurzame audit-pijplijn terechtkomt als de HTTP-audittrail uit [hoofdstuk 11](11-rapport-en-traceerbaarheid.md). We eindigen met een dekkingschecklist die per top-level map aangeeft in welk hoofdstuk die aan bod komt.

Termen die we hier gebruiken: een *router* is een FastAPI-bestand dat een groep API-adressen (endpoints) definieert; een *service* bevat de eigenlijke logica en databasequery's; een *dependency* is een functie die FastAPI vóór het endpoint uitvoert (hier vooral voor toegangscontrole). Aan de frontend-kant is een *page* een React-component die een volledig scherm rendert.

## Toegangscontrole als rode draad

Bijna elke module in dit hoofdstuk hangt aan één van twee poortwachters (zie ook [hoofdstuk 2](02-beveiliging-rollen-rechten.md) en [hoofdstuk 5](05-login-authenticatie.md)):

| Dependency | Betekenis | Waar toegepast |
| --- | --- | --- |
| `get_current_user` | Elke ingelogde gebruiker met een geldig token | CNV-catalogus, panelen lezen, releases, UI-events insturen |
| `get_current_admin_user` | Alleen gebruikers met de rol admin | De volledige `/admin`-router, panelen aanmaken/wijzigen, PanelApp-import |

Deze dependencies komen uit `backend/app/dependencies.py`. Cruciaal: de acteur (wie iets doet) wordt altijd uit het auth-token gehaald, nooit uit de request-body. Dat is expliciet vastgelegd in de docstring van `ingest_ui_events` in `backend/app/routers/ui_events.py` ("the actor is always taken from the auth token, never the request body").

## Clinical CNV Explorer

De Clinical CNV Explorer laat curatoren de *gecureerde kennisbank van terugkerende CNV-syndromen* doorbladeren. Een CNV (Copy Number Variant) is een structurele variant waarbij een stuk DNA in kopieaantal afwijkt; bekende voorbeelden zijn microdeletiesyndromen. De kennisbank is samengesteld uit bronnen als ClinGen/ISCA, DECIPHER, OMIM/Orphanet en ClinVar.

**Frontend.** De pagina `frontend/src/pages/cnv-explorer/ClinicalCnvExplorerPage.tsx` kiest eerst een assembly (referentiegenoom, bv. GRCh38), haalt dan de catalogus op via `GET /cnvs/{assembly}/catalog` met een optionele zoekterm, en toont per CNV de gegevens uit de kennisbank. Elke rij linkt naar een detailpagina; de route is `/cnv-details/:cnvId` en het scherm is `frontend/src/pages/genome/CnvDetailsPage.tsx`.

**Backend router.** `backend/app/routers/cnvs.py` definieert drie leesendpoints, allemaal achter een router-brede `Depends(get_current_user)` (referentiedata vereist authenticatie):
- `GET /cnvs/entry/{cnv_id}` — één CNV op id (functie `get_clinical_cnv_entry`).
- `GET /cnvs/{assembly}/catalog` — de doorzoekbare catalogus (functie `list_clinical_cnv_catalog`; `limit` begrensd op 1–2000).
- `GET /cnvs/{assembly}/{chrom}` — CNV's die een genomisch bereik overlappen (functie `get_clinical_cnvs`, met `start`/`end`-parameters); wordt gebruikt door de visualisaties (zie [hoofdstuk 9](09-visualisaties.md)).

De letterlijke prefix-routes (`/entry/...`, `/{assembly}/catalog`) staan bewust vóór de generieke `/{assembly}/{chrom}` zodat ze in de match-volgorde voorrang krijgen (een commentaarregel in het bestand legt dit uit). De echte data-ophaal zit in `reference_metadata_service` (`get_clinical_cnv_by_id_data`, `list_clinical_cnvs_catalog_data`, `get_clinical_cnvs_data`).

**Databankstructuur (traceerbaarheid & provenance).** De curatie-velden zijn additief toegevoegd via idempotente migraties (`ADD COLUMN IF NOT EXISTS`), zodat oude installaties veilig meegroeien:
- `018_clinical_cnv_details.sql` voegt `omim_id`, `decipher_id` en `description` toe. Als deze ontbreken, valt de detailpagina terug op afgeleide links (OMIM-zoekopdracht op naam, DECIPHER-regio).
- `020_clinical_cnv_enrichment.sql` voegt `cytoband`, `source_id` (ISCA-regio-id), `omim_title`, `orpha_id` en `orpha_name` toe — verrijkingsvelden die door de kennisbank-build worden gevuld.

**Kennisbank herbouwen (admin).** De service `backend/app/services/clinical_cnv_kb_jobs.py` verzorgt de door de admin getriggerde herbouw van de kennisbank. Kernpunten voor veiligheid en traceerbaarheid:
- Het zware build-script (`scripts/clinical_cnv_knowledgebase.py`, dat externe bronnen zoals ClinVar ophaalt) draait als een **geïsoleerd subprocess** (`asyncio.create_subprocess_exec`), zodat de netwerkfetches en optionele afhankelijkheden nooit het API-proces raken. Het resultaat is een TSV die via de standaard reference-loader (`apply_reference_dataset_text`) in de tabel `clinical_cnvs` wordt geladen.
- De voortgang wordt bijgehouden in tabel `clinical_cnv_kb_jobs` (migratie `019_clinical_cnv_kb_jobs.sql`): status `queued/running/completed/failed`, `requested_by`, tijdstempels (`requested_at`/`started_at`/`completed_at`), een `inserted`-teller, en een foutmelding (`error`) plus stderr-staart (`log`).
- Een **partiële unieke index** (`idx_clinical_cnv_kb_jobs_active` op `(status)` met `WHERE status IN ('queued','running')`) dwingt af dat er hooguit één herbouw tegelijk loopt; een tweede poging krijgt netjes een 409.
- Beschikbaarheid is defensief: ontbreekt het script op de server, dan meldt `get_clinical_cnv_kb_status` dat (`available: false`) en weigert `queue_clinical_cnv_kb_rebuild` met een 503.

**Waar in de code:** `backend/app/routers/admin.py` — endpoints `get_clinical_cnv_kb_rebuild_status` (`GET /admin/clinical-cnv-kb/status`) en `rebuild_clinical_cnv_kb` (`POST /admin/clinical-cnv-kb/rebuild`), beide admin-only; logica in `backend/app/services/clinical_cnv_kb_jobs.py`.

## Gene-panel-catalogus

Een *genpaneel* is een benoemde, versioneerbare lijst van genen (eventueel met regio's en STR-loci) die je herbruikt om variantfilters te scopen. De catalogus is de bron voor het "panel"-filter op de filterpagina's uit [hoofdstuk 8](08-filterpaginas-en-api.md) en voor de Gene Explorer uit [hoofdstuk 13](13-gene-explorer.md).

**Backend router.** `backend/app/routers/panels.py` scheidt lees- van schrijfrechten heel expliciet:
- Lezen (`get_current_user`): `GET /panels/` (lijst), `GET /panels/{panel_id}`, plus versiegeschiedenis `GET /panels/{panel_id}/versions` en `.../versions/{version}`, en de PanelApp-zoekopdracht `GET /panels/panelapp/search`.
- Schrijven (`get_current_admin_user`): `POST /panels/` (aanmaken), `PUT /panels/{panel_id}`, `DELETE /panels/{panel_id}`, de PanelApp-import (`POST /panels/import/panelapp`) en de Mendeliome-hergeneratie (`POST /panels/mendeliome/regenerate`).

**Versiebeheer (traceerbaarheid).** In `backend/app/services/panel_metadata_service.py` zorgt `_snapshot_panel_version` dat elke wijziging aan een paneel een onveranderlijke versie-snapshot vastlegt; `list_panel_versions` / `get_panel_version` maken die geschiedenis opvraagbaar. Zo is voor elke rapportage terug te vinden welke genset op welk moment gold — belangrijk in de IVDR-context. De snapshots worden bewaard in tabel `gene_panel_versions` (migratie `034_gene_panel_versions.sql`).

**Externe bron: PanelApp.** `backend/app/services/panelapp_service.py` haalt panelen op bij Genomics England PanelApp (`PANELAPP_API_ROOT = "https://panelapp.genomicsengland.co.uk/api/v1"`). De frontend (`GenePanelsPage.tsx`) laat admins zoeken (`search_panelapp`) en importeren (`import_panelapp_panel`) met keuzes voor confidence-niveau, assembly en het al dan niet meenemen van regio's en STR's. De importmetadata (bron, PanelApp-id, versie) wordt bij het paneel bewaard, zodat de herkomst traceerbaar blijft.

**Mendeliome.** Een speciaal, gegenereerd paneel ("Mendeliome", constante `MENDELIOME_SOURCE = "mendeliome"`) wordt afgeleid uit de Monarch-kennisgraaf via `regenerate_mendeliome`. De genselectie (`_select_mendeliome_genes`) neemt genen uit tabel `monarch_gene_disease` met de predicaten `causes` en `gene_associated_with_condition` (`_MENDELIOME_PREDICATES`). Dit paneel wordt automatisch her-geversioneerd wanneer een nieuwe Monarch-release wordt geladen — zie de best-effort aanroep `await regenerate_mendeliome(session, user)` binnen `refresh_monarch_associations` in `admin.py`. Meer over Monarch in [hoofdstuk 12](12-hpo-monarch-prioritisatie.md).

**Frontend-detail.** `frontend/src/pages/panels/GenePanelDetailPage.tsx` toont één paneel met zijn genen/regio's en versiehistoriek; `GenePanelsPage.tsx` toont de catalogus en de PanelApp-importworkflow. Schrijfacties zijn in de UI verborgen voor niet-admins, maar de echte afdwinging zit altijd server-side in de router.

## Adminfunctionaliteit

De hele admin-API zit in `backend/app/routers/admin.py` (prefix `/admin`), waar **elk** endpoint `Depends(get_current_admin_user)` gebruikt. De achterliggende logica staat vooral in `backend/app/services/admin_service.py`. De frontend-schermen staan onder `frontend/src/pages/admin/`. Hieronder per functie wat het doet.

### Gebruikers & projecten

- **UserListPage** (`UserListPage.tsx`): toont per gebruiker e-mail, naam, affiliatie, rol, actief-status en projecttoegang (via `GET /auth/users`). Admins kunnen een account (de-)activeren via `PATCH /auth/users/{user_id}` (kolom `is_active`). Projecttoegang wordt hier alleen getoond, niet bewerkt.
- **Projecttoewijzing van families:** `GET /admin/projects` (`list_project_assignments`) en `PUT /admin/families/{family_id}/projects` (`update_family_projects`) beheren welke projecten een familie zien. De frontend gebruikt dit vanuit `DataManagementPage.tsx`.

### Datamanagement & provenance

- **DataManagementPage** (`DataManagementPage.tsx`): kies een familie (gegroepeerd per project) en beheer leden, samples, projecttoegang, assay-data en de ruwe bronbestanden. Elke destructieve actie loopt via een `confirm=true`-queryparameter naar de backend.
- **DataInventoryDetail** (`pages/admin/DataInventoryDetail.tsx`): het detailpaneel per familie; leunt op `GET /admin/data/families/{family_id}` (`get_family_data_inventory_detail`) voor de telling van Postgres- en ClickHouse-data per type.
- **Selectief verwijderen:** de admin-router biedt fijnmazige delete-endpoints: per data-type voor een sample of familie (`DELETE /admin/data/samples/{sample_id}/{data_type}`, `.../families/{family_id}/{data_type}`) en volledige verwijdering (`DELETE /admin/samples/{sample_id}`, `.../families/{family_id}`). Alle vereisen `confirm=true`.
- **RawFileProvenanceTable** (`pages/admin/RawFileProvenanceTable.tsx`): dit is het traceerbaarheidshart van datamanagement. Per familie (`GET /admin/data/families/{family_id}/files`) toont het alle ruwe bronbestanden met opslagpad, grootte en **SHA-256-checksum**. Twee acties:
  - *Download* — `GET /admin/data/files/{file_id}/download`; geeft een 410 als het bronbestand niet meer op zijn opslagpad staat (`download_raw_import_file` controleert `Path(storage_path).is_file()`).
  - *Verify* — `POST /admin/data/files/{file_id}/verify` (`verify_raw_import_file_by_id`) herberekent de SHA-256 en vergelijkt met de opgeslagen waarde; het resultaat wordt als gekleurde badge getoond. Dit levert het integriteitsbewijs "wat is er precies geïmporteerd" uit [hoofdstuk 6](06-import-pipeline.md).
- **Pedigree-export:** `GET /admin/families/{family_id}/ped` levert een PED-bestand (stamboom) als download (`build_pedigree_text`).

### ClickHouse-onderhoud

- **AdminClickhouseManagementPage** (`AdminClickhouseManagementPage.tsx`): houdt de grootschalige varianttabellen gezond, los van de familie-levenscyclus. Het toont per assembly de tabelstatus (`GET /admin/clickhouse/variants`) en biedt onderhoudsacties, elk achter een bevestigingsdialoog:
  - `POST .../{assembly_name}/ensure` — tabellen/materialized views aanmaken/bijwerken.
  - `POST .../{assembly_name}/optimize` (optioneel `final`) — parts samenvoegen.
  - `POST .../{assembly_name}/rebuild-small-variant-gene-index` — de gene-index herbouwen (dit is precies de herstelactie uit de projectnotitie over corrupte gene-index).
  - `GET .../{assembly_name}/integrity` — integriteitscheck.

Meer achtergrond over deze tabellen staat in [hoofdstuk 3](03-databankstructuren.md).

### Family-statussen

- **AdminFamilyStatusesPage** (`AdminFamilyStatusesPage.tsx`): beheert de catalogus van workflow-statussen (bv. "Solved", "Analysis in progress"). CRUD via `GET/POST/PUT/DELETE /admin/family-statuses[...]`. De logica staat in `backend/app/services/family_status_service.py`:
  - Statussen leven in de lookup-tabel `family_statuses`; een familie verwijst ernaar via `families.status_id`.
  - Een sleutel (`key`) wordt uniek geslugd uit het label (`_unique_status_key` + `_slugify_status`); kleuren worden gevalideerd tegen een hex-patroon (`_normalize_color`) met een veilige default.
  - **Non-destructief verwijderen:** door `ON DELETE SET NULL` op `families.status_id` blijft een familie bestaan wanneer haar status wordt verwijderd — het statusveld wordt simpelweg leeggemaakt (zie de comment in `delete_family_status`).
  - De feitelijke status/toewijzing per familie (`assigned_to`, `reviewed_by`) wordt gezet door elke gebruiker mét toegang tot de familie via `update_family_metadata_for_user`, met 404 bij onbekende familie en 403 bij geen toegang.

### Preset-filters & variant-tags

- **AdminPresetFiltersPage** (`AdminPresetFiltersPage.tsx`): overzicht van de herbruikbare small-variant filter-presets (`GET /admin/small-variant-filter-presets`). Zie voor de filters zelf [hoofdstuk 8](08-filterpaginas-en-api.md).
- **AdminVariantTagsPage** (`AdminVariantTagsPage.tsx`): beheert de tag-definities voor varianten (`GET/POST/PUT/DELETE /admin/variant-tags`). De admin-variant bekijkt tags project-breed. De tagging-workflow zelf staat in [hoofdstuk 10](10-tagging-en-acmg-classificatie.md).

### Audit logs & UI-events

- **AdminAuditLogsPage** (`AdminAuditLogsPage.tsx`): één scherm met twee weergaven (`requests` en `interactions`):
  - *Requests* — de HTTP-audittrail via `GET /admin/audit-logs` (`list_audit_log_events`), met filters op methode, statuscode, gebruikers-e-mail en pad. Toont ook de samengevatte DB-mutatie per request (frontend-helper `summarizeDbUpdate`). Dit is de trail uit [hoofdstuk 11](11-rapport-en-traceerbaarheid.md).
  - *Interactions* — de UI-telemetrie via `GET /admin/ui-events` (`list_ui_events`, zie verderop).
- **Integriteitsketens (tamper-evidence).** `admin.py` bevat de operator/auditor-tools voor de append-only hash-ketens: `GET /admin/integrity/verify` (`verify_integrity_chain`) herwandelt een familie-keten en lokaliseert de eerste afwijkende rij; `POST /admin/integrity/anchor` (`create_integrity_anchor_endpoint`) verzegelt alle keten-koppen met een Ed25519-handtekening; `GET /admin/integrity/anchor/verify` en `.../verify-chain` controleren respectievelijk tegen het laatste anker en de ankerketen zelf. Deze zijn integraal onderdeel van de traceerbaarheid uit [hoofdstuk 11](11-rapport-en-traceerbaarheid.md).

### Overige admin-onderdelen (kort)

`admin.py` bundelt ook: NIPT-artefactbeheer (`/admin/nipt/artifacts...`, incl. `auto-seed`), gene-reference-refresh (`/admin/gene-reference/...`, zie [hoofdstuk 13](13-gene-explorer.md)), HPO-ontologiebeheer (`/admin/hpo/...`) en Monarch-beheer (`/admin/monarch/...`, zie [hoofdstuk 12](12-hpo-monarch-prioritisatie.md)). De bijbehorende schermen zijn o.a. `GeneReferenceAdminPage.tsx`, `HpoTerminologyAdminPage.tsx` en `MonarchDataAdminPage.tsx`; `AdminDashboardPage.tsx` is het instappunt.

## In-app documentatie

Omdat de repository privé is, worden externe links naar `.md`-bestanden vermeden en wordt de referentiedocumentatie **in de app zelf** gebundeld en gerenderd.

- **Bron:** `frontend/src/content/docs/*.md` — negen Markdown-referenties (`data-import.md`, `sample-qc.md`, `monarch-integration.md`, `acmg-classification.md`, `haplotype-segregation.md`, `monogenic-nipt.md`, `variant-ranking-cache.md`, `sv-second-hit.md`, `clinical-traceability.md`).
- **Registratie:** `frontend/src/pages/docs/referenceDocs.ts` importeert elk bestand met Vite's `?raw`-suffix (zodat de tekst als string wordt ingebed) en koppelt er een `slug`, `title` en `summary` aan. Een nieuwe referentie voeg je toe door een `.md` te droppen en hier te registreren (dit staat ook in de comment bovenaan het bestand).
- **Rendering:** `frontend/src/pages/docs/ReferenceDocPage.tsx` zoekt het doc op via `referenceDocBySlug.get(slug)` en rendert de Markdown met `react-markdown` + `remark-gfm`. Interne links (`/...`) gaan via React Router; externe links openen in een nieuw tabblad (`target="_blank" rel="noreferrer"`).
- **Gebruikersgids:** `frontend/src/pages/docs/UserGuidePage.tsx` is de handgeschreven gebruikersgids die per sectie naar de betreffende schermen linkt en doorverwijst naar de diepere referentiedocs.

**Waar in de code:** `frontend/src/pages/docs/` (schermen) en `frontend/src/content/docs/` (inhoud).

## Releases / New features

De New-features-pagina toont de versiegeschiedenis, gesynchroniseerd vanaf GitHub-releases.

- **Frontend:** `frontend/src/pages/product/NewFeaturesPage.tsx` haalt `GET /product/releases` op en toont per release de versie, titel, datum, een korte samenvatting en een link naar GitHub. Bij een mislukte sync (`sync_error`) valt het scherm terug op directe GitHub-links.
- **Backend router:** `backend/app/routers/product.py` — één endpoint `list_product_releases`, achter `get_current_user`.
- **Veilig ophalen (privé repo → token):** `backend/app/services/github_releases_service.py` bevraagt de GitHub API. Als er een token geconfigureerd is (`settings.github_api_token`), wordt een `Authorization: Bearer <token>` meegestuurd; is de repo niet toegankelijk (status 401/403/404), dan geeft de service een nette `sync_error` in plaats van te crashen ("Configure GITHUB_API_TOKEN for a private repository or make the repository public"). De release-bodies worden geschoond en samengevat (`summarize_release_body` strip't Markdown/HTML en beperkt tot vier regels), en het resultaat wordt in een proces-lokale cache met TTL gehouden (`_ReleaseCatalogCache`) om GitHub niet te overvragen.

## UI-telemetrie & audit-pijplijn

Naast de HTTP-audittrail (elke API-call) legt CoGA ook *betekenisvolle UI-interacties* vast die de backend anders nooit zien zou — kliks op knoppen/links/tabs en in-app navigaties. Dit voedt dezelfde duurzame audit-/telemetrie-infrastructuur uit [hoofdstuk 11](11-rapport-en-traceerbaarheid.md).

**Client-zijde.** `frontend/src/lib/telemetry.ts` (`startUiTelemetry`) hangt in de capture-fase globale `click`- en `submit`-listeners op interactieve elementen (`button, a[href], [role="button"], [role="tab"], [role="menuitem"], ...`). Componenten kunnen via `data-audit-id` / `data-audit-label` een stabiel label meegeven. Events worden lokaal gebufferd (max 200), elke 5 s in batches naar `POST /ui-events` gestuurd, en bij het verbergen/verlaten van de pagina synchroon geflusht met `fetch(..., { keepalive: true })` (zodat de bearer-token nog meekan — `sendBeacon` kan die Authorization-header niet zetten). Telemetrie is strikt best-effort: netwerkfouten laten events vallen en verstoren de app nooit.

**Server-zijde ingestie & sanitisatie (privacy).** `backend/app/routers/ui_events.py` (`ingest_ui_events`) is het beveiligingsfilter:
- Alleen een whitelist van event-types (`click`, `navigation`, `submit`, `view`, `query` — `_ALLOWED_EVENT_TYPES`) wordt geaccepteerd; de rest wordt stil weggegooid, zodat een gemanipuleerde client geen willekeurige types kan opslaan.
- **Paden worden gemaskeerd** (`_mask_path`): UUID's en numerieke id-segmenten worden `:id`, en query-strings worden gereduceerd tot hun (gesorteerde) sleutels. Zo belanden klinische identifiers nooit in de opslag.
- **Detail-blob wordt gesaneerd** (`_sanitize_detail`): sleutels die op `password`, `secret`, `token`, `authorization`, `api_key`, `access_key` beginnen worden `***`; geneste structuren worden vervangen door alleen hun type (bv. `[dict]`), zodat een token diep in een payload niet per ongeluk verbatim wordt opgeslagen.
- De acteur (user-id/e-mail/rol) komt uit het token; IP en user-agent uit de request.

**Duurzame opslag.** `backend/app/services/ui_event_pg.py` schrijft events naar de Postgres-tabel `ui_events` (migratie `023_ui_events.sql`). In de `async`-modus lopen ze via een begrensde `asyncio.Queue` en een achtergrond-worker die in batches wegschrijft (`start_ui_event_worker`, `write_ui_event`). De gedeelde duurzaamheidslaag `backend/app/services/event_pipeline.py` — die ook de HTTP-audittrail bedient — garandeert dat een accountability-event nooit stil verloren gaat:
- Bij een volle queue in productie wordt backpressure toegepast en zo nodig synchroon weggeschreven (`enqueue_event`); een echt onopslaanbaar event wordt met zijn volledige (reeds gesaniteerde) payload gelogd én geteld, nooit stil gedropt (`_record_unpersisted`).
- Batch-writes worden met begrensde backoff herprobeerd (`write_event_batch_with_retry`).
- Alleen in niet-productie (`AUDIT_LOG_DROP_ALLOWED`) mag een vol queue met een WARN droppen — de settings-validator weigert dit in productie.

**Waar in de code:** client `frontend/src/lib/telemetry.ts`; ingestie `backend/app/routers/ui_events.py`; opslag `backend/app/services/ui_event_pg.py`; duurzaamheid `backend/app/services/event_pipeline.py`; admin-weergave `frontend/src/pages/admin/AdminAuditLogsPage.tsx`.

## Family-status & -structuur

Naast de statuscatalogus (hierboven) beheert CoGA de feitelijke gezinsstructuur — leden, samples en onderlinge relaties.

- **`family_status_service.py`** — de statuscatalogus en per-familie workflow-metadata (status, `assigned_to`, `reviewed_by`), zie boven.
- **`family_structure_service.py`** — `update_family_structure_for_admin` valideert en herschrijft de volledige gezinsstructuur: leden, ouder-kind- en partner-relaties, met een relatie-graafvalidatie (`_validate_relationship_graph`) die inconsistente stambomen weigert, plus het opnieuw genereren van PED-tekst. Bij verwijderen van een lid wordt bijbehorende genomische data opgeruimd (`_clear_family_genomic_data`).
- **`family_member_management_service.py`** — fijnmaziger beheer per lid: `get_family_member_impact_for_user` toont vooraf de impact van een verandering (hoeveel data eraan hangt), `update_family_member_for_admin` / `update_family_members_batch_for_admin` wijzigen leden, en `delete_family_member_for_admin` verwijdert een lid. Een hernoeming/verwijdering wordt geblokkeerd wanneer er nog genomische data aan hangt (`_rename_block_reason`) — een veiligheidsrem tegen dataverlies.

De onderliggende migraties (zoals gevraagd, `011`/`014`/`024`) bouwen dit incrementeel op:
- `011_pgt_family_roles.sql` — breidt de toegestane ledenrollen uit (voegt o.a. `embryo` en `relative` toe aan de rol-constraint), nodig voor PGT/embryo-families.
- `014_family_structure_relationships.sql` — voegt `clinical_status`/`carrier_status` (+ carrier-detailkolommen) toe aan `family_members` en introduceert de tabel `family_relationships` (relatietypes `parent_child` / `couple`).
- `024_family_metadata.sql` — de catalogustabel `family_statuses` (met geseede default-statussen) plus de kolommen `families.status_id`, `assigned_to_id` en `reviewed_by_id`, elk met `ON DELETE SET NULL`.

Deze services worden aangesproken vanuit de admin- en familie-detailschermen; de stamboomlogica sluit aan op de haplotype-/pedigree-context uit [hoofdstuk 9](09-visualisaties.md).

## Dekkingschecklist

Bevestiging dat elke top-level bron-map in de handleiding aan bod komt:

| Top-level map | Behandeld in |
| --- | --- |
| `backend/app/core` | [Hfdst. 1 (architectuur)](01-architectuur.md), [3 (databank)](03-databankstructuren.md), [4 (deployment)](04-deployment-en-seeding.md), [5 (auth)](05-login-authenticatie.md) |
| `backend/app/routers` | [Hfdst. 7 (routers & services)](07-backend-routers-en-services.md); filters [8](08-filterpaginas-en-api.md); dit hoofdstuk (`cnvs`, `panels`, `admin`, `product`, `ui_events`) |
| `backend/app/services` | [Hfdst. 7](07-backend-routers-en-services.md); domein-specifiek in [6](06-import-pipeline.md), [10](10-tagging-en-acmg-classificatie.md), [11](11-rapport-en-traceerbaarheid.md), [12](12-hpo-monarch-prioritisatie.md); dit hoofdstuk (CNV-KB, panelen, admin, releases, event-pipeline, family-status/-structuur) |
| `backend/app/middleware` | [Hfdst. 2 (beveiliging)](02-beveiliging-rollen-rechten.md) & [11 (audit-middleware)](11-rapport-en-traceerbaarheid.md) |
| `frontend/src/pages` | Verspreid over [5](05-login-authenticatie.md), [8](08-filterpaginas-en-api.md), [10](10-tagging-en-acmg-classificatie.md)–[14](14-variant-explorer.md); dit hoofdstuk dekt `admin/`, `cnv-explorer/`, `panels/`, `docs/`, `product/` (en de CNV-detailpagina in `genome/`) |
| `frontend/src/components` | [Hfdst. 8](08-filterpaginas-en-api.md) & [9](09-visualisaties.md) (filter- en visualisatiecomponenten); de admin-hulpcomponenten die dit hoofdstuk behandelt (`DataInventoryDetail`, `RawFileProvenanceTable`) staan in `pages/admin/`, niet in `components/` |
| `frontend/src/lib` | [Hfdst. 5 (`api`, `auth`)](05-login-authenticatie.md); dit hoofdstuk (`telemetry.ts`) |
| `frontend/src/visualizations` | [Hfdst. 9 (visualisaties)](09-visualisaties.md) |
| `frontend/src/content` | Dit hoofdstuk (in-app referentiedocs onder `content/docs`) |

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/cnvs.py` | Lees-API voor de klinische-CNV-catalogus (auth-only) |
| `backend/app/services/clinical_cnv_kb_jobs.py` | Admin-getriggerde, geïsoleerde herbouw van de CNV-kennisbank + job-tracking |
| `backend/db/schema/postgres/018–020_*.sql` | Curatie- en verrijkingskolommen + `clinical_cnv_kb_jobs`-tabel |
| `backend/app/routers/panels.py` | Genpaneel-CRUD (lezen: user; schrijven: admin) + PanelApp-import |
| `backend/app/services/panel_metadata_service.py` | Panelenlogica, versie-snapshots, Mendeliome-generatie |
| `backend/app/services/panelapp_service.py` | PanelApp-client (zoeken/ophalen bij Genomics England) |
| `backend/app/routers/admin.py` | Volledige admin-API (data, ClickHouse, statussen, tags, audit, integriteit), admin-only |
| `backend/app/services/admin_service.py` | Data-inventaris, delete-logica, ruw-bestand-verificatie |
| `backend/app/routers/product.py` + `services/github_releases_service.py` | Release-feed, veilig via token voor privé repo |
| `backend/app/routers/ui_events.py` | Ingestie + sanitisatie (padmaskering, secret-redactie) van UI-telemetrie |
| `backend/app/services/ui_event_pg.py` + `event_pipeline.py` | Duurzame opslag en verliesvrije audit-/telemetrie-pijplijn |
| `backend/app/services/family_status_service.py` | Statuscatalogus en per-familie workflow-metadata |
| `backend/app/services/family_structure_service.py` / `family_member_management_service.py` | Gezinsstructuur- en ledenbeheer met validatie en impact-checks |
| `frontend/src/pages/cnv-explorer/ClinicalCnvExplorerPage.tsx` + `frontend/src/pages/genome/CnvDetailsPage.tsx` | CNV-browser en CNV-detailpagina |
| `frontend/src/pages/panels/*` | Panelencatalogus en -detail |
| `frontend/src/pages/admin/*` | Adminschermen (users, data, ClickHouse, statussen, presets, tags, audit, provenance) |
| `frontend/src/pages/docs/*` + `frontend/src/content/docs/*` | In-app gebruikersgids en referentiedocumentatie |
| `frontend/src/pages/product/NewFeaturesPage.tsx` | Release-/features-overzicht |
| `frontend/src/lib/telemetry.ts` | Client-zijde UI-telemetrie (kliks, navigaties, keepalive-flush) |
