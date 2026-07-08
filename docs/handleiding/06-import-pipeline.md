# 6. Package import — manifest, controles en traceerbaarheid

Dit hoofdstuk beschrijft hoe CoGA een volledig familiepakket met genoomdata in één gecontroleerde handeling inleest. Het volgt de weg van een map op schijf of in een cloud-bucket, via een *manifest* (een inhoudsopgave-bestand), langs een reeks veiligheids- en integriteitscontroles, tot de uiteindelijke wegschrijving in Postgres en ClickHouse. Aan bod komen welke controles vooraf gebeuren (de *dry-run*), hoe elk bronbestand herleidbaar wordt vastgelegd, en hoe een importtaak wordt bijgehouden en teruggerold als er iets misgaat. De rode draad is dat een auditor achteraf exact moet kunnen reconstrueren *welk bestand, met welke versies van welke tools, in welke tabel* terechtkwam — een IVDR-traceerbaarheidsvereiste.

Een paar begrippen die in dit hoofdstuk steeds terugkomen:

- **Manifest**: een YAML- of JSON-bestand (`manifest.yaml`) dat opsomt welke datasets een familie bevat en waar elk bestand staat. Vergelijkbaar met een paklijst.
- **PED**: het klassieke pedigree-bestand (zes kolommen) dat de familieleden en hun ouder-kindrelaties beschrijft.
- **VCF**: het standaard tekstformaat voor varianten. De `##`-regels bovenaan (de *header*) bevatten de versies van de gebruikte tools.
- **Dry-run**: een "proefdraai" die alles valideert maar niets wegschrijft.
- **Postgres vs. ClickHouse**: Postgres bewaart metadata en review-status; ClickHouse bewaart de grote hoeveelheden varianten (zie [hoofdstuk 3](03-databankstructuren.md)).

## De pijplijn in vogelvlucht

De hele importstroom is admin-only en loopt over de router `family_imports.py` (prefix `/family-imports`). Elk endpoint hangt aan `Depends(get_current_admin_user)`, zodat een gewone gebruiker deze functionaliteit niet kan aanroepen.

**Waar in de code:** `backend/app/routers/family_imports.py`. De endpoints (de "Service-functie" is de onderliggende functie waar de logica zit; de router is een dunne schil eromheen):

| Endpoint | Service-functie | Rol |
| --- | --- | --- |
| `GET /family-imports/packages` | `scan_family_import_packages` | Ontdekt importeerbare familiemappen |
| `POST /family-imports/manifest/discover` | `discover_family_package_manifest` | Bouwt een manifest-voorstel |
| `POST /family-imports/manifest/write` | `write_family_package_manifest` | Schrijft `manifest.yaml` weg |
| `POST /family-imports/validate` | `validate_family_package` | Onmiddellijke validatie (geen job) |
| `POST /family-imports` | `queue_family_import_job` | Zet een import- of dry-run-taak in de wachtrij |
| `GET /family-imports` en `GET /family-imports/{job_id}` | `list_family_import_jobs` / `get_family_import_job` | Volg de status |

De feitelijke import draait niet in het request zelf, maar in een **achtergrondwerker** (`family_package_import_worker`), zodat een groot pakket het webproces niet blokkeert. Die werker claimt de volgende taak (`claim_next_family_import_job`) en voert `run_family_import_job` uit; dat roept `execute_family_package_import` aan, die op zijn beurt `_execute_family_package_import_local` uitvoert: valideren → familie registreren → dataset voor dataset importeren.

**Waar in de code:** `backend/app/services/family_package_import.py` (`family_package_import_worker`, `run_family_import_job`, `execute_family_package_import`, `_execute_family_package_import_local`).

## Manifest-discovery vanuit S3, GCS of een lokale map

### De opslag-abstractie

CoGA leest brondata uit drie soorten locaties: een lokale map, een AWS **S3**-bucket, of **Google Cloud Storage** (GCS). Een *bucket* is de cloud-tegenhanger van een map. Welke back-end actief is, bepaalt de instelling `STORAGE_BACKEND` (`local`, `s3` of `gcs`). Alle scheme-verschillen (`s3://` vs. `gs://`) worden weggeabstraheerd in één module, zodat de rest van de importcode nooit hoeft te weten waar de bytes vandaan komen.

**Waar in de code:** `backend/app/core/object_storage.py`. Kernfuncties: `is_remote_uri`, `parse_remote_uri`, `download_prefix` (haalt alle objecten onder een prefix op) en `list_remote_package_candidates` (ontdekt familiemappen in een bucket). De cloud-SDK's (`boto3`, `google-cloud-storage`) worden *lazy* geïmporteerd, zodat een installatie de back-end die ze niet gebruikt ook niet hoeft te installeren.

Welke locaties mogen worden gescand, staat in de instelling `FAMILY_IMPORT_ROOTS` — een komma-gescheiden **allowlist** (standaard `/data/families`). Cloud-deployments zetten hier een `s3://…`-prefix. Deze allowlist is een beveiligingsgrens: een admin kan niet zomaar een willekeurig pad op de host laten inlezen.

**Waar in de code:** `backend/app/services/family_package_source.py`, functies `_authorized_local_roots`, `_authorized_s3_roots` en de bewaker `_ensure_authorized_package_path`. Die laatste "faalt open" (staat alles toe) *alleen* wanneer er helemaal niets is geconfigureerd (`family_import_roots=[]`) — een expliciete dev-modus. Zodra roots geconfigureerd zijn, wordt een pad buiten de allowlist met **HTTP 403** afgewezen. Het commentaar in de code documenteert dat een eerdere versie hier een lek had (een S3-only configuratie liet lokale paden ongemoeid door); dat is nu dichtgetimmerd.

### Herkende bestandstypen en het pakketlayout

Een geldig pakket is een map met een `manifest.yaml` (of `.yml`/`.json`) en/of een `*.ped`-bestand. Het `standard_v1`-naamgevingsschema kent vaste zoekpaden per dataset. De discovery-stap scant die paden en stelt een manifest-voorstel samen met een beschikbaarheidstabel.

**Waar in de code:** `backend/app/services/family_package_discovery.py` (`discover_family_package_manifest`, `NAMING_SCHEMES`) en de gedocumenteerde layout in `docs/data-import.md`.

De ondersteunde datasets zijn vastgelegd in de constante `SUPPORTED_DATASETS` (in `family_package_common.py`) — tien datasettypen — met per type een verwacht zoekpad (`{family_id}`/`{sample_id}` zijn plaatshouders):

| Manifest-sleutel | Inhoud | Voorbeeldpad |
| --- | --- | --- |
| `snv` | Small Variants (SNV/indels), VCF + optionele annotatie-TSV | `snv/{family_id}.annotated.vcf.gz` |
| `sv_needlr` | Structurele varianten (NeedlR) | `needlr/{family_id}.sv.annotated.vcf.gz` |
| `repeats_trgt` | Repeat-expansies (TRGT) | `repeats/{family_id}.trgt.vcf.gz` |
| `wisecondorx` | CNV coverage-bins en segmenten (per sample) | `wisecondorx/{sample_id}/bins.bed` |
| `qdnaseq` | CNV coverage-bins en segmenten (per sample) | `QDNAseq/{sample_id}/bins.csv` |
| `coverage` | Algemene coverage-BED per sample (alleen via manifest, geen vast zoekpad) | — |
| `apcad` | APCAD-intervallen (embryo) | `APCAD/{family_id}.apcad.vcf.gz` |
| `pcf` | PCF-segmenten, maternaal/paternaal (embryo) | `PCF/{sample_id}_pcf_mat_data.csv` |
| `haplotypes` | GLIMPSE2-gefaseerde varianten | `GLIMPSE2/{family_id}_phased_final.vcf.gz` |
| `paraphase` | Paraphase-resultaten (JSON) | `paraphase/{sample_id}.paraphase.json` |

### Hoe families en samples worden afgeleid

De **PED** is de bron van waarheid voor de familiestructuur. Discovery en validatie parsen het PED strikt (zes kolommen, precies één familie), en elke per-sample dataset moet naar een sample-ID uit het PED verwijzen. Het manifest kan het PED aanvullen met klinische status, dragerschap (`carrier_status`/`carrier_type`) en expliciete relaties (partnerschappen, ouder-kind), maar mag geen samples introduceren die niet in het PED staan.

**Waar in de code:** `backend/app/services/family_package_validation.py` (`_parse_ped_text_strict` via `load_validated_family_package`) en `family_package_manifest.py` (`_ped_members_for_import`, `_manifest_relationships`). Voor een import tegen een *bestaande* familie kan het PED zelfs worden gereconstrueerd uit de database, zodat geen PED-bestand nodig is — zie `db_pedigree_fallback` in `family_package_import.py`.

## Dry-run: alles valideren vóór er iets wordt weggeschreven

De dry-run is de veiligheidspoort van de hele import. Hij draait exact dezelfde validatie als een echte import, maar stopt vóór de eerste wegschrijving. De centrale functie is `load_validated_family_package`, die een `FamilyPackageValidationOut` teruggeeft (geldig ja/nee, plus lijsten met `errors`, `warnings` en per-dataset `datasets`-samenvattingen).

**Waar in de code:** `backend/app/services/family_package_validation.py`, functies `load_validated_family_package` en `validate_family_package`.

De controles, op volgorde:

1. **Pad-autorisatie** — de map moet binnen `FAMILY_IMPORT_ROOTS` vallen (anders `package_folder_not_allowed`).
2. **Bestaan en type** — de map bestaat en is een directory; het manifest bestaat (anders `package_folder_missing` / `package_folder_not_directory` / `manifest_missing`).
3. **Manifest-schema** — het manifest parst en `schema_version` moet `1` zijn (ontbreekt hij, dan een waarschuwing `manifest_schema_version_missing`; is hij anders, dan een fout `manifest_schema_version_unsupported`).
4. **PED-integriteit** — zes kolommen, precies één familie, sample-ID's uniek, `family_id` moet matchen (`ped_multiple_families`, `ped_family_mismatch`, …).
5. **Per-dataset** — voor elke dataset controleert een eigen validator dat de bestanden bestaan en dat gecomprimeerde VCF/BCF-bestanden een index hebben (`.tbi`/`.csi`/`.idx`); een ongecomprimeerde `.vcf` mag zonder index. Onbekende dataset-sleutels zijn *fouten* (`dataset_unsupported`); ontbrekende optionele datasets zijn *waarschuwingen* (`optional_dataset_missing`).
6. **Fenotypes (HPO)** — een phenotype-TSV moet formaat `hpo_tsv` hebben en naar PED-samples verwijzen; slechte rijen worden waarschuwingen, niet blokkerend.

**Waar in de code:** de per-dataset validators (`_validate_family_vcf_dataset`, `_validate_wisecondorx_dataset`, `_validate_paraphase_dataset`, …) in `family_package_validation.py`. Elke bevinding is een `FamilyImportValidationIssue` met een stabiele `code`, zodat de frontend en een auditor de fout eenduidig kunnen benoemen.

Een fout leidt tot `valid=False`; de import stopt dan onmiddellijk met de logregel *"Package validation failed; no data were imported."* — er wordt niets weggeschreven.

**Waar in de code:** `_execute_family_package_import_local` in `family_package_import.py` (de `if validation.errors:`-tak en de `if dry_run:`-tak).

## Data-controles en integriteit

### VCF-header-provenance

Bij elke VCF-import haalt CoGA de `##`-headerregels op en leidt daaruit af *welke tools en databankversies* de data hebben geproduceerd — de variant-caller (DeepVariant/GATK/Sniffles/Spectre/TRGT/Clair3/GLIMPSE), de annotatie-engine (VEP/snpEff/bcftools) en de daarin ingebedde databankreleases (gnomAD, ClinVar, dbNSFP, SpliceAI, …). Deze parsing is **best-effort en werpt nooit een fout**: een onherkenbare header levert simpelweg minder op, maar kan een import nooit laten mislukken.

**Waar in de code:** `backend/app/services/vcf_header_provenance.py`. Drie parsers werken samen: `extract_header_provenance` (de VCF-`##KEY=value`-vorm, inclusief de rijke `##VEP=`-regel), `extract_vep_tab_provenance` (voor families waar de annotatie in een aparte VEP-TSV zit) en `extract_info_description_provenance` (een conservatieve, ge-allowlist mijnbouw in `##INFO`-beschrijvingen, o.a. voor de NeedlR-SV-pijplijn); `merge_module_maps` voegt de opbrengsten samen. Zie ook `docs/annotation-provenance.md`.

### Hashing en checksums van ruwe bestanden

Voor traceerbaarheid berekent CoGA van elk bronbestand een **SHA-256-checksum** (een cryptografische vingerafdruk waarmee je later kunt bewijzen dat een bestand niet gewijzigd is). De hash wordt *streaming* berekend — in blokken van 1 MiB — zodat een VCF of CRAM van vele gigabytes nooit volledig in het geheugen wordt geladen.

**Waar in de code:** `backend/app/services/raw_import_files_pg.py`, functies `_hash_and_size` (op-schijf, streaming) en `verify_raw_import_file` (herberekent de hash en vergelijkt met de opgeslagen waarde). Die verificatie is begrensd: boven 2 GiB of na 30 s timeout wordt het overgeslagen en gerapporteerd als `too_large`, zodat een enorme CRAM een request niet minutenlang gijzelt.

### Veilige, begrensde download en upload

Twee aparte modules beschermen tegen een *decompression bomb* — een klein `.gz`-bestand dat bij uitpakken naar gigabytes opzwelt en het werkgeheugen uitput (een DoS-aanval).

- **Uploads/pakketbestanden** worden begrensd gelezen én uitgepakt met harde limieten op zowel de gelezen bytes als de uitgepakte omvang; overschrijding geeft HTTP 413. De gunzip-lus doorloopt bovendien álle gzip-members (bgzip/BGZF `.vcf.gz` bestaat uit vele blokken), zodat het bestand niet stilzwijgend tot zijn eerste blok wordt afgekapt.
  **Waar in de code:** `backend/app/services/upload_safety.py` (`decode_upload_text`, `read_path_text_bounded`, `_gunzip_bounded`).
- **Uitgaande referentie-downloads** hebben hun eigen begrensde variant; een te grote of ongeldige upstream-respons geeft hier een `ValueError` (server-/upstream-fout in plaats van 4xx).
  **Waar in de code:** `backend/app/services/bounded_download.py` (`download_bounded_bytes`, `gunzip_bounded`).

### Padveiligheid bij het stagen en oplossen van paden

Twee plekken beschermen tegen *path traversal* (een gemanipuleerd pad zoals `../../etc/passwd`):

- Bij het **stagen** van een S3-/GCS-pakket worden alle objectsleutels naar lokale doelen vertaald en wordt gecontroleerd dat elk doel binnen de staging-map blijft; een sleutel die eruit probeert te ontsnappen wordt geweigerd vóór er iets wordt geschreven.
  **Waar in de code:** `_plan_downloads` in `object_storage.py`.
- Elk **in het manifest gedeclareerd** bestandspad wordt opgelost en moet binnen de pakketmap blijven; `ped: /etc/passwd` of `../../etc/passwd` levert HTTP 400.
  **Waar in de code:** `_resolve_package_path` in `backend/app/services/family_package_common.py`.

Een remote pakket wordt eerst gestaged (gedownload naar een tijdelijke map) zodat de bestaande, pad-gebaseerde importlogica ongewijzigd draait; de tijdelijke map wordt na afloop opgeruimd.

**Waar in de code:** `staged_package_source_async` / `_stage_s3_package` in `family_package_source.py`.

## Wat komt in welke tabel

De import registreert eerst de familie-metadata en de provenance, en importeert daarna dataset voor dataset. Grofweg: kleine, hoog-volume variantrijen gaan naar **ClickHouse**; metadata, herkomst en per-sample brongegevens naar **Postgres**.

**Waar in de code:** `backend/app/services/family_package_datasets.py` (`_import_dataset` en de per-dataset importfuncties), die de bestaande loaders `upload_family_small_variant_file`, `replace_family_structural_variants`, `ingest_family_trgt_text`, enz. aanroepen.

| Bron in het pakket | Loader (functie) | Doeltabel(len) | Databank |
| --- | --- | --- | --- |
| `snv` VCF (+ VEP-TSV) | `upload_family_small_variant_file` | small variants | ClickHouse |
| `haplotypes` GLIMPSE2 VCF | `upload_family_small_variant_file` (`glimpse2`) | small variants + haplotype-interval-tracks | ClickHouse |
| `sv_needlr` VCF | `replace_family_structural_variants` | structural variants | ClickHouse |
| `wisecondorx`/`qdnaseq` bins/segments | `_import_wisecondorx_track` / `_import_copy_number_track` | interval-track-entries | ClickHouse |
| `apcad`/`pcf`/`coverage` | `_import_apcad_track_file` / `_import_pcf_segment_file` / `upload_bed_data` | interval-tracks (`apcad`, `apcad_pcf`, `coverage`) | ClickHouse |
| interval-track bron-metadata | `upsert_interval_track_source` | `sample_interval_track_sources` | Postgres |
| `repeats_trgt` VCF | `ingest_family_trgt_text` / `ingest_trgt_text` | `repeat_expansions` | Postgres |
| `paraphase` JSON | `_replace_sample_paraphase_rows` | `sample_paraphase_results` | Postgres |
| `phenotypes` (HPO) | `import_family_hpo_annotations` | `individual_hpo` | Postgres |
| familiestructuur (PED/manifest) | `_ensure_family_from_ped` | `families`, `samples`, `family_members`, `family_projects`, `sample_projects` | Postgres |
| elk ruw bronbestand | `record_raw_import_file` | `raw_import_files` | Postgres |
| tool-/databankversies | `merge_vcf_header_provenance` | `family_annotation_manifest` | Postgres |
| de importtaak zelf | `queue_family_import_job` / `_update_job_progress` | `family_import_jobs` | Postgres |

**Waar in de code:** de ClickHouse-loaders in `backend/app/services/clickhouse_variant_storage.py` (`insert_small_variant_records`, `replace_family_structural_variants`); de NeedlR-SV-parser is `_iter_needlr_structural_records` in `family_package_variants.py` (levert `StructuralVariantRecord`-objecten voor `replace_family_structural_variants`). De Postgres-schrijvers `ingest_family_trgt_text` (`repeat_expansion_pg.py`) en `_replace_sample_paraphase_rows` (`family_package_variants.py`).

Let op: **NIPT-artefacten** (`nipt_artifact_variants`, Postgres) worden *niet* door de pakket-import gevuld — die lopen via een aparte route (zie [hoofdstuk 8](08-filterpaginas-en-api.md)). De pakket-import promoveert wél een gedeclareerd `analysis_type` (bv. `monogenic_nipt`) en een per-sample `assay` (bv. `nipt_cfdna`) naar de familie-/sample-metadata, zodat de NIPT-context later herkend wordt.

**Waar in de code:** `_register_package_provenance` in `family_package_registration.py`; `nipt_artifact_pg.py` is de aparte NIPT-loader.

## Import-job-tracking en foutafhandeling

### De job-tabel en statussen

Elke import is een rij in `family_import_jobs`. De statussen zijn afgedwongen door een `CHECK`-constraint: `queued → validating → running → completed | failed`.

**Waar in de code:** `backend/db/schema/postgres/007_family_import_jobs.sql`. De rij bewaart naast de status ook `validation_errors`, `validation_warnings`, `logs`, `dataset_summaries` en `metadata` (JSONB), plus tijdstempels `requested_at`, `started_at`, `heartbeat_at`, `completed_at`.

De worker "claimt" de volgende taak atomair met `FOR UPDATE SKIP LOCKED`, zodat meerdere workers (instelbaar via `FAMILY_IMPORT_WORKER_COUNT`, standaard 1) elkaar niet in de weg zitten. Een taak waarvan de `heartbeat_at` te oud is (`FAMILY_IMPORT_STALE_HEARTBEAT`, 10 min) wordt als vastgelopen beschouwd en opnieuw geclaimd. Tijdens lange imports wordt de heartbeat/voortgang doorlopend bijgewerkt.

**Waar in de code:** `backend/app/services/family_package_jobs.py` (`claim_next_family_import_job`, `_update_job_progress`, `queue_family_import_job`). De autorisatie is fijnmazig: `get_family_import_job` en `list_family_import_jobs` tonen niet-admins alleen hun eigen jobs — al is de router zelf al admin-only.

### "Fail-clean": nooit een stil half-geïmporteerde familie

De foutafhandeling is expliciet ontworpen om nooit een misleidend-onvolledige toestand achter te laten. Elke dataset wordt in een eigen `try/except` geïmporteerd; faalt er één, dan volgt een `session.rollback()` en gaat de import door met de rest. Na afloop geldt:

- **Nieuwe familie én niets geïmporteerd** → de vers aangemaakte familie-*shell* wordt gecompenseerd (verwijderd), inclusief zijn ClickHouse-variant- en interval-rijen, zodat er niets partieels overblijft.
- **Deels gelukt, of een bestaande familie** → de succesvolle datasets blijven bewaard, maar de familie-metadata krijgt een `import_incomplete`-vlag, zodat de partiële toestand expliciet en auditeerbaar is in plaats van stilzwijgend als "compleet" opvraagbaar.
- In élk faalgeval wordt `error` gezet, waardoor de job-rij op `status='failed'` eindigt — nooit een stille `completed`.

**Waar in de code:** `_execute_family_package_import_local` in `family_package_import.py` (het uitgebreide "Fail-clean"-blok), met helpers `_delete_family_shell`, `_flag_family_import_incomplete` en `_clear_family_import_incomplete` in `family_package_registration.py`. Dezelfde compensatie-gedachte zit ook in de dataset-importers zelf: `_import_snv_dataset` en `_import_haplotypes_dataset` (in `family_package_datasets.py`) verwijderen bij een fout hun eigen (deels weggeschreven) rijen, maar scopen dat naar hun eigen `source` (`clair3` respectievelijk `glimpse2`) zodat een mislukte SNV-import nooit een reeds geïmporteerde GLIMPSE2-callset of haar haplotype-blokken wist.

## Provenance en het annotation manifest

Naast de per-bestand-hashing legt CoGA per familie een **annotation manifest** aan: één rij met een vrije JSONB-map `modules` van `{ toolKey: { version, detail, by_modality } }`. Dit is cruciaal voor latere rapport-traceerbaarheid — een resultaat moet herleidbaar zijn naar precies de tool- en databankversies die het produceerden (IVDR-vereiste).

**Waar in de code:** schema `backend/db/schema/postgres/030_family_annotation_manifest.sql` (kolommen `modules`, `source`, `recorded_by`, `recorded_at`, `assembly_id`; `source` is `'vcf_header'`, `'manifest'` of `'manual'`; `UNIQUE (family_id)` — precies één rij per familie). De schrijf- en leesdienst is `backend/app/services/annotation_manifest_service.py`.

De belangrijkste regels van `merge_vcf_header_provenance` (met helper `_refresh_modules`):

- **Verversen bij her-import** — nieuw geparste versies overschrijven de oude *per sleutel*, terwijl modules die de nieuwe invoer niet noemt behouden blijven (het her-importeren van alleen de SV-VCF wist de SNV-afgeleide VEP/gnomAD-versies niet).
- **Handmatige override wint altijd** — een door een admin gecureerd manifest (`source='manual'`) wordt door import nooit overschreven.
- **Transactie-veilig** — de schrijfactie loopt in een `SAVEPOINT` (`session.begin_nested()`, een genest, terugrolbaar punt binnen de transactie) en voegt zich bij de commit van de ingestie, zodat de provenance persistent wordt *dan en slechts dan als* de data die hij beschrijft dat ook wordt — en een provenance-fout een import nooit kan breken.
- **Per-modaliteit** — omdat SNV, SV en TRGT door verschillende pijplijnen worden geannoteerd (en zo verschillende releases van dezelfde databank kunnen noemen, bv. GENCODE 49 vs. 45), wordt naast de vlakke `version` ook `by_modality` bijgehouden.

De import-hooks roepen deze samenvoeging aan vlak nadat de varianten zijn weggeschreven: voor SNV onderaan `upload_family_small_variant_file` (modality `snv`), voor SV in `_import_sv_needlr_dataset` (modality `sv`, dat de header én de `##INFO`-beschrijvingen mijnt).

**Waar in de code:** `variant_upload_service.py` (het `merge_vcf_header_provenance`-blok onderaan `upload_family_small_variant_file`) en `family_package_datasets.py` (`_import_sv_needlr_dataset`).

## Logging en volledige traceerbaarheid

Het traceerbaarheidsregister bij uitstek is de tabel **`raw_import_files`**: één rij per fysiek bronbestand, met bestandsnaam, type, scope (`family` of `individual`), gekoppeld sample, opslagpad, byte-grootte, SHA-256-checksum en herkomst (`source`). De insertie is idempotent op `(family_id, sample_id, storage_path)` (een uniek index met een placeholder-UUID voor een leeg sample), zodat een her-import de bestaande rij ververst in plaats van dubbelen te maken.

**Waar in de code:** schema `backend/db/schema/postgres/017_raw_import_files.sql`; de schrijffunctie `record_raw_import_file` en het pakket-gedreven `_record_package_raw_files` in respectievelijk `raw_import_files_pg.py` en `family_package_registration.py`. Pakketbestanden worden *in place* geregistreerd (`managed=False`) — CoGA verplaatst of verwijdert ze niet; alleen web-uploads worden als beheerde kopie onder `data/raw_imports/` bewaard. Voor een S3-pakket wordt bovendien de duurzame `s3://`-URI vastgelegd (de staging-map verdwijnt immers na afloop).

Wat kan een auditor hierdoor achteraf reconstrueren? Per familie: welke ruwe bestanden zijn ingelezen, met welke checksum (verifieerbaar via `verify_raw_import_file`), in welke dataset; welke tool- en databankversies gebruikt zijn (`family_annotation_manifest`); en de volledige loop van de importtaak — status, tijdstempels, logregels, validatiefouten/-waarschuwingen en per-dataset resultaat (`family_import_jobs`). Samen sluit dit de keten van *geannoteerde VCF → weggeschreven data → herkomst → rapport*.

Naast deze registers logt de import ook een leesbare regel per stap (bv. *"Dataset snv: imported."*, *"Family package import completed."*), zichtbaar in de job-rij en in de frontend.

**Waar in de code:** de `logs`-lijst die door `_execute_family_package_import_local` wordt opgebouwd en via `_update_job_progress` wordt weggeschreven.

## De frontend: hoe de admin de import stuurt

De adminpagina bestaat uit een dunne pagina-schil (`PackageImportPage`) rond het eigenlijke paneel (`FamilyPackageImportPanel`).

**Waar in de code:** `frontend/src/pages/dashboard/PackageImportPage.tsx` en `frontend/src/pages/dashboard/FamilyPackageImportPanel.tsx` (bereikbaar via `/package-import`, geregistreerd in `frontend/src/index.tsx`).

De workflow in het paneel volgt de backend-endpoints één-op-één:

1. **Familiemap kiezen** — een dropdown gevuld door `GET /family-imports/packages` (ontdekte pakketten), of een pad intypen. Het PED wordt automatisch ontdekt.
2. **Doel bepalen** — nieuwe familie of bestaande familie, met een *existing-data policy* (`cancel` / `update` / `overwrite`) die als `conflict_mode` meegaat.
3. **Manifest ontdekken** (`/manifest/discover`) — toont een beschikbaarheidstabel per dataset en een bewerkbaar `manifest.yaml`-voorbeeld; optioneel wegschrijven met `/manifest/write`.
4. **Valideren of importeren** (`POST /family-imports`) — de *Dry run*-checkbox staat standaard aan; aan is dit een proefdraai ("Validate package"), anders een echte import ("Start import"). Een project kiezen is verplicht voor een echte import.
5. **Volgen** — de pagina *poll*t `GET /family-imports/{job_id}` (elke 2,5 s zolang de job actief is) en toont status, tellers, validatiefouten, waarschuwingen, een per-dataset tabel en de loglijst. "Recent family imports" (`GET /family-imports`) laat eerdere jobs opnieuw openen.

De verplichte dry-run-eerst-workflow (gedocumenteerd in `docs/data-import.md`) zorgt ervoor dat een admin de validatie-uitkomst ziet vóór er ook maar iets naar de databank gaat — de menselijke bevestigingsstap in een verder geautomatiseerde, herleidbare pijplijn.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/family_imports.py` | Admin-only API-endpoints voor scan, manifest, validatie en import-jobs |
| `backend/app/services/family_package_import.py` | Orkestratie: valideren → registreren → per dataset importeren; fail-clean; achtergrondworker |
| `backend/app/services/family_package_source.py` | Bronresolutie: allowlist-bewaking, S3/GCS-staging, manifest parsen |
| `backend/app/core/object_storage.py` | Opslag-abstractie (local/S3/GCS), begrensde prefix-download, pakketontdekking |
| `backend/app/services/family_package_validation.py` | Dry-run: manifest-/PED-/per-dataset-validatie met stabiele foutcodes |
| `backend/app/services/family_package_datasets.py` | Per-dataset importfuncties die de bestaande variant-/track-loaders aansturen |
| `backend/app/services/family_package_registration.py` | Familie/sample registreren, provenance vastleggen, compensatie/incompleet-vlag |
| `backend/app/services/family_package_jobs.py` | Job-levenscyclus: wachtrij, atomair claimen, voortgang/heartbeat |
| `backend/app/services/family_package_variants.py` | NeedlR-SV-parser en Paraphase-rijen naar Postgres |
| `backend/app/services/variant_upload_service.py` | SNV/GLIMPSE2-loader; haplotype-blokken; SNV-header-provenance |
| `backend/app/services/vcf_header_provenance.py` | Best-effort parser van tool-/databankversies uit VCF-headers |
| `backend/app/services/annotation_manifest_service.py` | Schrijft/leest `family_annotation_manifest`; refresh- en manual-wins-regels |
| `backend/app/services/raw_import_files_pg.py` | Provenance-register van ruwe bestanden: hashing, verificatie, opslag |
| `backend/app/services/upload_safety.py` / `bounded_download.py` | Begrensde, bomb-veilige (de)compressie van uploads en downloads |
| `backend/db/schema/postgres/007_family_import_jobs.sql` | Job-tabel met status-constraint en JSONB-logs/samenvattingen |
| `backend/db/schema/postgres/017_raw_import_files.sql` | Ruwe-bestand-provenance met unieke identiteit per (familie, sample, pad) |
| `backend/db/schema/postgres/030_family_annotation_manifest.sql` | Per-familie annotatie-/versiemanifest voor rapport-traceerbaarheid |
| `frontend/src/pages/dashboard/FamilyPackageImportPanel.tsx` | Adminpaneel: mapkeuze, manifest, dry-run, import starten en volgen |

Voor de bredere context van de databanktabellen zie [hoofdstuk 3](03-databankstructuren.md); voor toegangscontrole en rollen [hoofdstuk 2](02-beveiliging-rollen-rechten.md); voor hoe deze provenance uiteindelijk in het ondertekende rapport wordt bevroren [hoofdstuk 11](11-rapport-en-traceerbaarheid.md).
