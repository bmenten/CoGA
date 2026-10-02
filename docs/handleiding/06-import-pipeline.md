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

CoGA leest pakketten uit een lokale map, een AWS **S3**-bucket of **Google Cloud Storage** (GCS). Welke bron actief is, bepaalt `STORAGE_BACKEND`. Het verschil tussen `s3://` en `gs://` zit in één module; de rest van de importcode weet niet waar de bytes vandaan komen. Een pakket uit de cloud wordt eerst naar een tijdelijke map gekopieerd, zodat dezelfde importlogica draait; die map wordt daarna opgeruimd. De alignments (`.cram`, `.crai`, `.bam`, `.bai` en een `.csi` van een alignment) blijven in de bucket: geen importer leest ze, en een CRAM van een volledig genoom is tientallen gigabytes. Alleen de dataset `alignments` mag naar zo'n bestand verwijzen; de import controleert dat het object bestaat en legt zijn URI vast. Omdat de tijdelijke map verdwijnt, noemen de joblog, het validatierapport, de herkomst van de familie en het register van bronbestanden de map en de objecten in de bucket. Heeft het manifest geen `family_id`, dan krijgt de familie de naam van de map in de bucket, zoals bij een lokale map.

Welke locaties gescand mogen worden, staat in `FAMILY_IMPORT_ROOTS`: een **allowlist**. Een pad buiten die lijst wordt geweigerd (`HTTP 403`), zodat een beheerder niet zomaar een willekeurig bestand op de server kan laten inlezen. Alleen als de lijst leeg is, staat de controle open; dat is een bewuste ontwikkelmodus.

**Waar in de code:** `backend/app/core/object_storage.py` (de opslaglaag) en `backend/app/services/family_package_source.py` (de allowlist en het kopiëren).

## Wat een pakket bevat

Een geldig pakket is een map met een manifest en/of een PED-bestand. Het manifest kent vijftien soorten datasets:

- **Varianten:** `snv` (small variants, met optionele annotatietabel), `sv_needlr` (structurele varianten), `cnv` (CNV-calls met hun signaalbestanden), `mito` (mitochondriale varianten), `repeats_trgt` (repeat-expansies).
- **Tracks:** `wisecondorx` en `qdnaseq` (CNV-dekking en -segmenten), `coverage`, `apcad` en `pcf` (embryo-analyses), `haplotypes` (een gefaseerde familie-VCF van GLIMPSE2 of SHAPEIT5).
- **Overige:** `paraphase` (paraloge genen), `alignments` (CRAM/BAM voor de genoombrowser), `qc` (sequencing-QC) en `pipeline_info` (de run-registratie van de pipeline: toolversies en parameters).

De verwachte mapindeling per dataset staat in `docs/data-import.md`. De ontdekkingsstap zoekt die paden af en toont wat hij vond. De long-read-pipeline zet de toolversie in bestandsnamen; een zoekpad mag daarom een `*` bevatten, maar het manifest bevat altijd het concrete pad dat gevonden werd, nooit het patroon.

**De PED is de bron van waarheid voor de familie.** De controle leest haar strikt: zes kolommen, precies één familie. Elke dataset per sample moet naar een lid van de familie verwijzen. Het manifest mag klinische status, dragerschap en expliciete relaties toevoegen, en onder `family.add_members` een lid dat de PED mist: dat wordt gelezen als een extra PED-rij, zonder ouders, en doorloopt dezelfde controles. Een lid dat al in de PED staat, mag er niet opnieuw in, en een relatie mag alleen leden noemen. Bij een import in een *bestaande* familie mag de PED ontbreken: dan wordt de stamboom uit de databank gebruikt.

**De PGT-pipeline (nf-cmgg/copgtm)** schrijft haar uitvoer per tool weg, met de PED in `ped/combined.ped`. Die PED geeft de embryo's het geslacht dat ngs-bits uit de reads afleidde, waardoor CoGA ze uit de PED alleen niet als embryo herkent, en ze mist de index: de verwant wiens haplotypes de twee haplotypes van de aangetaste ouder van elkaar onderscheiden. De ontdekkingsstap leest beide uit de samplesheet van de pipeline (`dashboard/samplesheet.csv`): de embryo's krijgen de rol embryo, en een index die de PED mist, wordt onder `family.add_members` toegevoegd als verwant. Hoe de index verwant is, staat in geen enkel pipelinebestand. De ontdekkingsstap stelt daarom een relatie voor op basis van wat KING mat tussen de index en het koppel: het kind van het koppel als KING de index eerstegraads verwant meet met beide ouders, anders een verwant van onbekende graad (`family.relationships.relatives`) van de ouder met wie KING verwantschap meet, of van de aangetaste ouder als KING geen verwantschap ziet. De waarschuwing citeert KING en zegt welke relatie werd voorgesteld. Zonder relatie blijft het haplotype van de index grijs; met een relatie van onbekende graad kleurt CoGA de index alleen als die een ouder of kind van die ouder blijkt: als hij langs bijna elk chromosoom een van haar haplotypes deelt. Een verdere verwant blijft grijs, omdat zulke gedeelde stukken op geïmputeerde low-pass genotypes niet betrouwbaar te onderscheiden zijn van wat onverwanten toevallig delen. De klinische status van de index blijft onbekend tot die wordt ingevuld. De ouder die de pipeline volgde, wordt de aangetaste ouder (`metadata.pgt.affected_parents`); met het overervingsmodel ingevuld (`metadata.pgt.inheritance_model`) krijgt die ouder de status die het model vraagt: aangetast bij AD en XLD en voor een vader bij XLR, bewezen drager bij AR en voor een moeder bij XLR. Een status die voor de ouder onder `family.members` staat, gaat voor. De PCF-segmentatie leest CoGA alleen uit haar tabel (`apcad/<embryo>_pcf_mat_data.csv` en `_pcf_pat_data.csv`): een run die de PCF enkel in een HTML-plot tekent, krijgt geen PCF-track.

**Waar in de code:** `backend/app/services/family_package_discovery.py` (ontdekken), `family_package_common.py` (de datasetlijst) en `family_package_manifest.py` (de PED en het manifest lezen).

## De dry-run: alles controleren vóór er iets wordt weggeschreven

De dry-run is de veiligheidspoort van de import. Hij voert exact dezelfde controles uit als een echte import, maar stopt vóór de eerste schrijfactie. De controles, in volgorde:

1. **Pad** — de map moet binnen `FAMILY_IMPORT_ROOTS` liggen.
2. **Bestaan** — de map bestaat en het manifest bestaat.
3. **Manifestversie** — `schema_version` moet `1` zijn (ontbreekt ze, dan volgt een waarschuwing).
4. **PED** — zes kolommen, één familie, unieke sample-id's, de juiste familie-id; een lid uit `family.add_members` staat niet al in de PED, en elke relatie uit het manifest noemt een lid.
5. **Per dataset** — de bestanden bestaan (een alignment van een pakket uit de cloud: in de bucket), en een gecomprimeerde VCF/BCF heeft een index (`.tbi`, `.csi` of `.idx`); een ongecomprimeerde `.vcf` mag zonder. Een onbekende dataset is een **fout**; een ontbrekende kerndataset (`snv` of `sv_needlr`) geeft een **waarschuwing**.
6. **Fenotypes (HPO)** — een fenotypetabel moet naar samples uit de PED verwijzen; slechte rijen worden waarschuwingen.

Elke bevinding heeft een vaste `code`, zodat de UI en een auditor een fout eenduidig kunnen benoemen. Is er één fout, dan stopt de import met "Package validation failed; no data were imported." en wordt er niets weggeschreven.

**Waar in de code:** `backend/app/services/family_package_validation.py`.

## Integriteit van de gegevens

- **Herkomst uit de VCF-header.** Bij elke VCF-import leest CoGA de `##`-regels en leidt daaruit af welke caller (bv. DeepVariant, Sniffles, TRGT, GLIMPSE), welke annotatietool (VEP, snpEff, bcftools) en welke databankversies (gnomAD, ClinVar, dbNSFP, SpliceAI, …) de data maakten. Dat is *best effort* en faalt nooit: een onbekende header levert minder op, maar laat de import niet mislukken. **Waar in de code:** `backend/app/services/vcf_header_provenance.py`.
- **Een SHA-256 per bronbestand.** Van elk bronbestand wordt een SHA-256-vingerafdruk berekend, in blokken, zodat ook een CRAM van vele gigabytes niet in het geheugen hoeft. Een beheerder kan die hash later opnieuw laten controleren; voor zeer grote bestanden (boven een vaste grootte- of tijdsgrens) slaat de controle over en meldt ze `too_large`. Bij een pakket uit de cloud wordt de tijdelijke kopie gehasht, en krijgt elk bestand ook het record van de opslag: grootte, generatie (GCS) of versie (S3), ETag en de checksums van de opslag, elk met zijn algoritme. Een alignment die in de bucket blijft, heeft geen SHA-256. De controle achteraf vergelijkt een bestand in de bucket met dat record, zonder het opnieuw te hashen: het object moet nog bestaan, met dezelfde grootte en generatie of versie. **Waar in de code:** `backend/app/services/raw_import_files_pg.py`.
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
| `qc`, `alignments` | De metadata van het sample (`samples.metadata`); de KING-tabel van de PGT-pipeline in de metadata van de familie | Postgres |
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

Een job gaat pas naar `running` als hij de familie heeft vastgelegd die hij importeert (`family_id`), en dat gebeurt vóór de import iets van die familie schrijft: daaraan herkent een ondertekening een lopende import (hoofdstuk 11, poort 0b). Lukt dat niet, dan stopt de import zonder te schrijven en eindigt de job op `failed`, met de reden. De latere updates van de job (logregels, samenvatting per dataset, heartbeat) zijn informatief: mislukt er een, dan loopt de import gewoon door, want de status en de familie blijven staan.

Een worker neemt een job atomair (`FOR UPDATE SKIP LOCKED`), zodat meerdere workers elkaar niet hinderen. Een lopende job schrijft elke minuut een levensteken (*heartbeat*). Is dat tien minuten oud, dan is het proces dat de job draaide gestopt (een herstart, een crash, geen geheugen meer), en neemt de volgende worker de job over:

- Was de import nog niet begonnen de familie te schrijven (de job stond nog op `validating`), dan draait de worker hem opnieuw van bij het begin. De logregels van de eerdere poging blijven staan.
- Was hij dat wel (`running`), dan eindigt de job op `failed`, onderbroken, en draait hij niet opnieuw: een nieuwe run van bij het begin maakt niet ongedaan wat hij al schreef. Het log en de samenvatting per dataset blijven zoals de import ze achterliet.

De foutafhandeling is zo gebouwd dat er nooit ongemerkt een half-geïmporteerde familie achterblijft ("fail-clean"):

- Elke dataset wordt apart geïmporteerd. Faalt er één, dan wordt die teruggedraaid en gaat de import door met de rest.
- Is de familie **nieuw en is niets gelukt**, dan wordt de lege familie weer verwijderd, met haar ClickHouse-rijen.
- Mislukt een `overwrite` van een **bestaande** familie, dan wordt de familie teruggezet naar haar toestand van vóór de import.
- **Om beurten:** elke schrijfactie op de varianten van een familie (een upload, een verwijdering, een import) neemt eerst het schrijfslot van die familie en houdt het vast tot ze is vastgelegd; een import houdt het vast van vóór haar snapshot tot ze klaar is of de familie heeft teruggezet. Twee schrijfacties op dezelfde familie lopen dus na elkaar, ook vanuit verschillende workers, en het terugzetten kan geen upload ongedaan maken die tijdens de import binnenkwam: die wacht tot de import klaar is. Zolang de importjob van een familie loopt of in de wachtrij staat, of een schrijver het slot houdt, kan haar rapport niet ondertekend worden; een ondertekening deelt het slot zolang ze de familie leest (hoofdstuk 11, poort 0b).
- In elk ander geval blijven de gelukte datasets staan en krijgt de familie de vlag `import_incomplete`: de mislukte en de gelukte datasets, het tijdstip en het id van de importjob. De fout per dataset staat in die job; de vlag neemt de fouttekst niet over. De gedeeltelijke toestand is dus zichtbaar, en niet stilzwijgend "compleet": elke familiepagina toont *Import incomplete*, en het rapport kan pas ondertekend worden na een erkenning met een reden (hoofdstuk 11). De vlag blijft staan tot een import elke mislukte dataset opnieuw heeft geïmporteerd, voor dezelfde samples en (bij de small variants) dezelfde `source_format`; een import die slaagt zonder die dataset laat de vlag staan en zegt dat in zijn log. Een `update` volstaat: de rijen van de mislukte dataset werden teruggedraaid of opgeruimd, dus de update vindt er geen en importeert de dataset volledig (een dataset waarvan rijen bleven staan, slaat hij over, en dat telt niet). Mislukt een latere import, dan blijven de mislukte datasets van de vorige in de vlag staan, elk met de job waarin zijn fout staat (`failed_jobs`).
- **Een import die halverwege stopt**, omdat zijn proces weg is, voert niets van het bovenstaande meer uit. Daarom zet een import een merkteken op de familie vóór hij er iets van schrijft: een item in `import_unfinished` in de familiemetadata, met de job, het begin en de datasets (een nieuwe familie wordt er meteen mee aangemaakt). Hij noteert daar elke dataset die hij afrondt, en haalt het item weg als hij eindigt. Blijft het staan, dan noemt het een import die gestopt is, en de datasets die hij niet afrondde: die kunnen half geschreven zijn of ontbreken. Elke familiepagina toont dan *Import incomplete*, en het rapport kan pas ondertekend worden na een erkenning met een reden. Alleen een import die met `overwrite` slaagt en die datasets opnieuw importeert, voor dezelfde samples en (bij de small variants) dezelfde `source_format`, wist het item: meer vervangt een overwrite niet. Een `update` kan dat niet: die slaat een dataset over waarvoor al data bestaan, ook half geschreven data. Faalt een import nadat hij de familie markeerde maar vóór zijn eerste dataset (bv. bij het registreren), dan schreef hij geen van zijn datasets: zijn item wordt dan de vlag `import_incomplete`, met al zijn datasets als mislukt.
- **De back-up van een gestopte `overwrite`:** een `overwrite` van een bestaande familie kopieert eerst haar variant- en trackrijen naar back-uptabellen in ClickHouse (`<assembly>/SNAPSHOT/<importjob>/…`), om de familie bij een fout terug te zetten, en verwijdert ze als hij eindigt. Stopt zijn proces, dan verwijdert de worker die de job beëindigt die tabellen, en bij elke start verdwijnt elke back-up waarvan geen lopende import eigenaar is. De familie wordt er niet mee teruggezet: het Postgres-deel van de back-up zat in het gestopte proces, en intussen kan er naar de familie geschreven zijn.
- Bij elke fout eindigt de job op `failed`, nooit op een stille `completed`.
- Een mislukte SNV-import ruimt alleen haar eigen rijen op, niet bv. een eerder geïmporteerde GLIMPSE2-callset.

**Waar in de code:** `backend/app/services/family_package_jobs.py` (de joblevenscyclus, en een job overnemen of als onderbroken beëindigen), `family_package_import.py` (de fail-clean-regels en de heartbeat) en `family_package_registration.py` (de vlag en het merkteken `import_unfinished`).

## Herkomst: het annotatiemanifest

Naast de hash per bestand houdt CoGA per familie één **annotatiemanifest** bij (`family_annotation_manifest`): per tool of databank de versie, met een detail en, waar nodig, de versie per modaliteit. Zo is elk resultaat later terug te voeren op de versies die het maakten. De regels:

- **Verversen bij een nieuwe import:** nieuwe versies overschrijven de oude per tool; wat de nieuwe invoer niet noemt, blijft staan (een nieuwe SV-import wist de SNV-versies dus niet).
- **Handwerk wint:** een manifest dat met de hand vervangen werd, staat altijd als `manual` en wordt door een import nooit overschreven. Er is geen scherm voor; alleen een beheerder kan het vervangen, via de API (`PUT /api/families/{family_id}/annotation-manifest`). Het manifest zelf is geen append-only tabel, maar elke vervanging komt in het klinische auditspoor, met het manifest ervoor en erna (hoofdstuk 11).
- **Om beurten:** een import en een vervanging van dezelfde familie nemen hetzelfde slot voor ze het manifest lezen, en een import houdt het vast tot ze is vastgelegd. Een vervanging kan dus niet tussen het lezen en het schrijven van een import vallen; ze wacht tot een lopende import van die familie klaar is.
- **Samen met de data:** de schrijfactie maakt deel uit van dezelfde transactie als de import. De herkomst wordt dus bewaard als, en alleen als, de data die ze beschrijft ook bewaard wordt; een fout in de herkomst breekt de import niet.
- **Per modaliteit:** SNV, SV en repeats worden door verschillende pipelines geannoteerd en kunnen verschillende releases van dezelfde databank noemen (bv. twee GENCODE-versies). Daarom wordt ook de versie per modaliteit bewaard.

**Waar in de code:** `backend/app/services/annotation_manifest_service.py`.

## Het register van bronbestanden

Het traceerbaarheidsregister bij uitstek is **`raw_import_files`**: één rij per fysiek bronbestand, met bestandsnaam, soort, familie of sample, opslagpad, grootte, SHA-256 en bron. Een nieuwe import van hetzelfde bestand werkt de rij bij in plaats van een dubbel te maken. Pakketbestanden worden ter plaatse geregistreerd: CoGA verplaatst of verwijdert ze niet. Voor een pakket uit de cloud wordt de blijvende bucket-URI vastgelegd, niet de tijdelijke kopie, samen met het record van de opslag (`metadata.store_object`).

Een auditor kan zo per familie nagaan: welke bestanden zijn ingelezen, met welke hash of welk opslagrecord (opnieuw te controleren), in welke dataset; met welke tool- en databankversies (`family_annotation_manifest`); en hoe de job verliep, met status, tijdstippen, logregels en validatiebevindingen (`family_import_jobs`). Dat sluit de keten *geannoteerde VCF → opgeslagen data → herkomst → rapport*.

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
