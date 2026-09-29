# 6. Package import — manifest, controles en traceerbaarheid

Dit hoofdstuk beschrijft hoe CoGA een volledig familiepakket met genoomdata in één gecontroleerde handeling inleest: van een map op schijf of in een cloudbucket, via een *manifest* en een reeks controles, tot de opslag in Postgres en ClickHouse. De rode draad is dat een auditor achteraf moet kunnen nagaan *welk bestand, gemaakt met welke tools, in welke tabel* terechtkwam. De Engelse referentie voor beheerders en ontwikkelaars is `docs/data-import.md` (mapindeling, manifestformaat, instellingen); de herkomst van versies staat in `docs/annotation-provenance.md`.

Enkele begrippen:

- **Manifest:** een YAML- of JSON-bestand (`manifest.yaml`) dat opsomt welke datasets een familie bevat en waar elk bestand staat, zoals een paklijst.
- **PED:** het klassieke stamboombestand (zes kolommen) met de familieleden en hun ouders.
- **VCF:** het standaardformaat voor varianten. De `##`-regels bovenaan (de *header*) vermelden de gebruikte tools en hun versies.
- **Dry-run:** een proefdraai die alles controleert maar niets wegschrijft.

## De pijplijn in vogelvlucht

De hele import is alleen voor beheerders: elk endpoint van de router `/api/family-imports` vraagt `get_current_admin_user`.

| Endpoint | Rol |
| --- | --- |
| `GET /family-imports/packages` | Importeerbare familiemappen vinden |
| `POST /family-imports/manifest/discover` | Een manifestvoorstel opbouwen uit wat in de map staat |
| `POST /family-imports/manifest/write` | `manifest.yaml` wegschrijven |
| `POST /family-imports/validate` | Meteen valideren, zonder job |
| `POST /family-imports` | Een import of dry-run in de wachtrij zetten |
| `GET /family-imports` en `GET /family-imports/{job_id}` | De status volgen |

De import zelf draait niet binnen het webverzoek maar in een **achtergrondworker**, zodat een groot pakket de server niet blokkeert. De worker neemt de volgende job, valideert het pakket, registreert de familie en importeert dan dataset per dataset.

**Waar in de code:** `backend/app/routers/family_imports.py`; de orkestratie en de worker in `backend/app/services/family_package_import.py`.

## Waar het pakket vandaan komt

CoGA leest pakketten uit een lokale map, een AWS **S3**-bucket of **Google Cloud Storage** (GCS). Welke bron actief is, bepaalt `STORAGE_BACKEND`. Het verschil tussen `s3://` en `gs://` zit in één module; de rest van de importcode weet niet waar de bytes vandaan komen. Een pakket uit de cloud wordt eerst naar een tijdelijke map gekopieerd, zodat dezelfde importlogica draait; die map wordt daarna opgeruimd.

Welke locaties gescand mogen worden, staat in `FAMILY_IMPORT_ROOTS`: een **allowlist**. Een pad buiten die lijst wordt geweigerd (`HTTP 403`), zodat een beheerder niet zomaar een willekeurig bestand op de server kan laten inlezen. Alleen als de lijst leeg is, staat de controle open; dat is een bewuste ontwikkelmodus.

**Waar in de code:** `backend/app/core/object_storage.py` (de opslaglaag) en `backend/app/services/family_package_source.py` (de allowlist en het kopiëren).

## Wat een pakket bevat

Een geldig pakket is een map met een manifest en/of een PED-bestand. Het manifest kent vijftien soorten datasets:

- **Varianten:** `snv` (small variants, met optionele annotatietabel), `sv_needlr` (structurele varianten), `cnv` (CNV-calls met hun signaalbestanden), `mito` (mitochondriale varianten), `repeats_trgt` (repeat-expansies).
- **Tracks:** `wisecondorx` en `qdnaseq` (CNV-dekking en -segmenten), `coverage`, `apcad` en `pcf` (embryo-analyses), `haplotypes` (GLIMPSE2-fasering).
- **Overige:** `paraphase` (paraloge genen), `alignments` (CRAM/BAM voor de genoombrowser), `qc` (sequencing-QC) en `pipeline_info` (de run-registratie van de pipeline: toolversies en parameters).

De verwachte mapindeling per dataset staat in `docs/data-import.md`. De ontdekkingsstap zoekt die paden af en toont wat hij vond. De long-read-pipeline zet de toolversie in bestandsnamen; een zoekpad mag daarom een `*` bevatten, maar het manifest bevat altijd het concrete pad dat gevonden werd, nooit het patroon.

**De PED is de bron van waarheid voor de familie.** De controle leest haar strikt: zes kolommen, precies één familie. Elke dataset per sample moet naar een sample uit de PED verwijzen. Het manifest mag klinische status, dragerschap en expliciete relaties toevoegen, maar geen samples die niet in de PED staan. Bij een import in een *bestaande* familie mag de PED ontbreken: dan wordt de stamboom uit de databank gebruikt.

**Waar in de code:** `backend/app/services/family_package_discovery.py` (ontdekken), `family_package_common.py` (de datasetlijst) en `family_package_manifest.py` (de PED en het manifest lezen).

## De dry-run: alles controleren vóór er iets wordt weggeschreven

De dry-run is de veiligheidspoort van de import. Hij voert exact dezelfde controles uit als een echte import, maar stopt vóór de eerste schrijfactie. De controles, in volgorde:

1. **Pad** — de map moet binnen `FAMILY_IMPORT_ROOTS` liggen.
2. **Bestaan** — de map bestaat en het manifest bestaat.
3. **Manifestversie** — `schema_version` moet `1` zijn (ontbreekt ze, dan volgt een waarschuwing).
4. **PED** — zes kolommen, één familie, unieke sample-id's, de juiste familie-id.
5. **Per dataset** — de bestanden bestaan, en een gecomprimeerde VCF/BCF heeft een index (`.tbi`, `.csi` of `.idx`); een ongecomprimeerde `.vcf` mag zonder. Een onbekende dataset is een **fout**; een ontbrekende kerndataset (`snv` of `sv_needlr`) geeft een **waarschuwing**.
6. **Fenotypes (HPO)** — een fenotypetabel moet naar samples uit de PED verwijzen; slechte rijen worden waarschuwingen.

Elke bevinding heeft een vaste `code`, zodat de UI en een auditor een fout eenduidig kunnen benoemen. Is er één fout, dan stopt de import met "Package validation failed; no data were imported." en wordt er niets weggeschreven.

**Waar in de code:** `backend/app/services/family_package_validation.py`.

## Integriteit van de gegevens

- **Herkomst uit de VCF-header.** Bij elke VCF-import leest CoGA de `##`-regels en leidt daaruit af welke caller (bv. DeepVariant, Sniffles, TRGT, GLIMPSE), welke annotatietool (VEP, snpEff, bcftools) en welke databankversies (gnomAD, ClinVar, dbNSFP, SpliceAI, …) de data maakten. Dat is *best effort* en faalt nooit: een onbekende header levert minder op, maar laat de import niet mislukken. **Waar in de code:** `backend/app/services/vcf_header_provenance.py`.
- **Een SHA-256 per bronbestand.** Van elk bronbestand wordt een SHA-256-vingerafdruk berekend, in blokken, zodat ook een CRAM van vele gigabytes niet in het geheugen hoeft. Een beheerder kan die hash later opnieuw laten controleren; voor zeer grote bestanden (boven een vaste grootte- of tijdsgrens) slaat de controle over en meldt ze `too_large`. **Waar in de code:** `backend/app/services/raw_import_files_pg.py`.
- **Begrensd lezen en uitpakken.** Pakketbestanden en uploads worden begrensd gelezen en uitgepakt, tegen "decompressiebommen" (hoofdstuk 2). Downloads van referentiedata hebben een eigen begrenzing. **Waar in de code:** `backend/app/services/upload_safety.py` en `bounded_download.py`.
- **Geen ontsnapping uit de map.** Elk pad uit het manifest moet binnen de pakketmap blijven (`../../etc/passwd` geeft `HTTP 400`), en bij het kopiëren uit de cloud moet elk doelpad binnen de tijdelijke map blijven. **Waar in de code:** `family_package_common.py` en `backend/app/core/object_storage.py`.

## Wat komt in welke tabel

Eerst registreert de import de familie en de herkomst, daarna volgen de datasets één voor één. Grote aantallen variantrijen gaan naar **ClickHouse**; metadata, herkomst en brongegevens per sample naar **Postgres**.

| Bron in het pakket | Doel | Databank |
| --- | --- | --- |
| `snv`, `mito`, `haplotypes` (VCF) | Small variants (bij `haplotypes` ook haplotypeblokken als interval-track) | ClickHouse |
| `sv_needlr`, `cnv` (VCF) | Structurele varianten; bij `cnv` ook dekking, kopieaantal en allelfractie als interval-tracks | ClickHouse |
| `wisecondorx`, `qdnaseq`, `apcad`, `pcf`, `coverage` | Interval-tracks | ClickHouse |
| Bron van elke interval-track | `sample_interval_track_sources` | Postgres |
| `repeats_trgt` (VCF) | `repeat_expansions` | Postgres |
| `paraphase` (JSON) | `sample_paraphase_results` | Postgres |
| `qc`, `alignments` | De metadata van het sample (`samples.metadata`) | Postgres |
| `pipeline_info` | `family_annotation_manifest` en de metadata van de familie | Postgres |
| Fenotypes (HPO) | `individual_hpo` | Postgres |
| Familiestructuur (PED, manifest) | `families`, `samples`, `family_members`, `family_projects`, `sample_projects` | Postgres |
| Elk ruw bronbestand | `raw_import_files` | Postgres |
| Tool- en databankversies | `family_annotation_manifest` | Postgres |
| De importjob zelf | `family_import_jobs` | Postgres |

Elke datasetsoort heeft een eigen importfunctie in een register. Een soort zonder importfunctie is een fout: een test bewaakt dat, en de import faalt er luid op.

**NIPT-artefacten** (de lijst van terugkerende artefacten per assay) worden niet door de pakketimport gevuld; ze hebben een eigen beheer (hoofdstuk 15). De import neemt wel een gedeclareerd analysetype (bv. `monogenic_nipt`) en een assay per sample (bv. `nipt_cfdna`) over in de metadata, zodat de NIPT-context later wordt herkend (hoofdstuk 8).

De **runparameters** van de pipeline (genoombuild, welke caller welke variantklasse maakte, welke stappen liepen) staan in de kaart *Analysis pipeline* op de familiewerkruimte en in de sectie *Analysis pipeline settings* van het rapport. De **toolversies** verschijnen in de herkomstvoettekst en in de regel *Modules & versions* van het rapport.

**Waar in de code:** `backend/app/services/family_package_datasets.py` (de importfuncties per dataset) en `family_package_registration.py` (familie, samples en herkomst registreren); de weergave in `frontend/src/pages/families/PipelineSettingsPanel.tsx`.

## Jobs volgen en fouten afhandelen

Elke import is een rij in `family_import_jobs`, met een status die de databank bewaakt: `queued → validating → running → completed | failed`. De rij bewaart ook de validatiefouten en -waarschuwingen, de logregels, een samenvatting per dataset en de tijdstippen.

Een worker neemt een job atomair (`FOR UPDATE SKIP LOCKED`), zodat meerdere workers elkaar niet hinderen. Een job waarvan het levensteken (*heartbeat*) te oud is, geldt als vastgelopen en wordt opnieuw opgepakt.

De foutafhandeling is zo gebouwd dat er nooit ongemerkt een half-geïmporteerde familie achterblijft ("fail-clean"):

- Elke dataset wordt apart geïmporteerd. Faalt er één, dan wordt die teruggedraaid en gaat de import door met de rest.
- Is de familie **nieuw en is niets gelukt**, dan wordt de lege familie weer verwijderd, met haar ClickHouse-rijen.
- Is een deel gelukt, of ging het om een **bestaande** familie, dan blijven de gelukte datasets staan en krijgt de familie de vlag `import_incomplete`. De gedeeltelijke toestand is dus zichtbaar, en niet stilzwijgend "compleet".
- Bij elke fout eindigt de job op `failed`, nooit op een stille `completed`.
- Een mislukte SNV-import ruimt alleen haar eigen rijen op, niet bv. een eerder geïmporteerde GLIMPSE2-callset.

**Waar in de code:** `backend/app/services/family_package_jobs.py` (de joblevenscyclus) en `family_package_import.py` (de fail-clean-regels).

## Herkomst: het annotatiemanifest

Naast de hash per bestand houdt CoGA per familie één **annotatiemanifest** bij (`family_annotation_manifest`): per tool of databank de versie, met een detail en, waar nodig, de versie per modaliteit. Zo is elk resultaat later terug te voeren op de versies die het maakten. De regels:

- **Verversen bij een nieuwe import:** nieuwe versies overschrijven de oude per tool; wat de nieuwe invoer niet noemt, blijft staan (een nieuwe SV-import wist de SNV-versies dus niet).
- **Handwerk wint:** een door een beheerder gecureerd manifest (`manual`) wordt door een import nooit overschreven.
- **Samen met de data:** de schrijfactie maakt deel uit van dezelfde transactie als de import. De herkomst wordt dus bewaard als, en alleen als, de data die ze beschrijft ook bewaard wordt; een fout in de herkomst breekt de import niet.
- **Per modaliteit:** SNV, SV en repeats worden door verschillende pipelines geannoteerd en kunnen verschillende releases van dezelfde databank noemen (bv. twee GENCODE-versies). Daarom wordt ook de versie per modaliteit bewaard.

**Waar in de code:** `backend/app/services/annotation_manifest_service.py`.

## Het register van bronbestanden

Het traceerbaarheidsregister bij uitstek is **`raw_import_files`**: één rij per fysiek bronbestand, met bestandsnaam, soort, familie of sample, opslagpad, grootte, SHA-256 en bron. Een nieuwe import van hetzelfde bestand werkt de rij bij in plaats van een dubbel te maken. Pakketbestanden worden ter plaatse geregistreerd: CoGA verplaatst of verwijdert ze niet. Voor een pakket uit de cloud wordt de blijvende bucket-URI vastgelegd, niet de tijdelijke kopie.

Een auditor kan zo per familie nagaan: welke bestanden zijn ingelezen, met welke hash (opnieuw te controleren), in welke dataset; met welke tool- en databankversies (`family_annotation_manifest`); en hoe de job verliep, met status, tijdstippen, logregels en validatiebevindingen (`family_import_jobs`). Dat sluit de keten *geannoteerde VCF → opgeslagen data → herkomst → rapport*.

**Waar in de code:** `backend/app/services/raw_import_files_pg.py` en `family_package_registration.py`.

## De beheerpagina

De pagina *Package import* (`/package-import`) volgt de endpoints één op één:

1. **Familiemap kiezen** uit de gevonden pakketten, of een pad intypen.
2. **Doel kiezen:** een nieuwe of een bestaande familie, met een beleid voor bestaande data (`cancel`, `update` of `overwrite`).
3. **Manifest ontdekken:** een overzicht per dataset en een bewerkbaar voorbeeld van `manifest.yaml`, eventueel weg te schrijven.
4. **Valideren of importeren:** de keuze *Dry run* staat standaard aan. Voor een echte import is een project verplicht.
5. **Volgen:** de pagina ververst de status zolang de job loopt en toont tellers, fouten, waarschuwingen, het resultaat per dataset en de logregels. Eerdere imports blijven op te roepen.

`docs/data-import.md` beschrijft deze werkwijze met de dry-run eerst: de beheerder ziet de uitkomst van de controles voordat er iets naar de databank gaat.

**Waar in de code:** `frontend/src/pages/dashboard/FamilyPackageImportPanel.tsx`.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/family_imports.py` | De endpoints (alleen voor beheerders) |
| `backend/app/services/family_package_import.py` | Orkestratie, worker en de fail-clean-regels |
| `backend/app/services/family_package_source.py` · `backend/app/core/object_storage.py` | Allowlist, lokale map, S3 en GCS |
| `backend/app/services/family_package_validation.py` | De dry-run-controles |
| `backend/app/services/family_package_datasets.py` | De importfunctie per dataset |
| `backend/app/services/family_package_registration.py` | Familie en samples registreren, herkomst vastleggen |
| `backend/app/services/family_package_jobs.py` | Wachtrij, jobs nemen, heartbeat |
| `backend/app/services/vcf_header_provenance.py` · `annotation_manifest_service.py` | Versies uit de headers; het annotatiemanifest |
| `backend/app/services/raw_import_files_pg.py` | Het register van bronbestanden met SHA-256 |
| `frontend/src/pages/dashboard/FamilyPackageImportPanel.tsx` | De beheerpagina |
