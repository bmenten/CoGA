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
| `POST /family-imports` | Een import of dry-run in de wachtrij zetten (een `project_id` dat geen project noemt, krijgt `404` nog voor de job bestaat) |
| `GET /family-imports` en `GET /family-imports/{job_id}` | De status volgen |

De import zelf draait niet binnen het webverzoek maar in een **achtergrondworker**, zodat een groot pakket de server niet blokkeert. De worker neemt de volgende job, valideert het pakket, registreert de familie en importeert dan dataset per dataset.

**Waar in de code:** `backend/app/routers/family_imports.py`; de orkestratie en de worker in `backend/app/services/family_package_import.py`.

## Waar het pakket vandaan komt

CoGA leest pakketten uit een lokale map, een AWS **S3**-bucket of **Google Cloud Storage** (GCS). De bron volgt uit het pad zelf: een pad met `s3://` of `gs://` wordt uit die bucket gelezen, elk ander pad is een lokale map (`STORAGE_BACKEND` bepaalt alleen waar de genoombrowser en de QC-rapporten hun bestanden lezen). Het manifest ontdekken en *Write manifest.yaml* werken alleen op een lokale map: een pakket in een bucket brengt zijn manifest mee. Het verschil tussen `s3://` en `gs://` zit in één module; de rest van de importcode weet niet waar de bytes vandaan komen. Een pakket uit de cloud wordt eerst naar een tijdelijke map gekopieerd, zodat dezelfde importlogica draait; die map wordt daarna opgeruimd. De alignments (`.cram`, `.crai`, `.bam`, `.bai` en een `.csi` van een alignment) blijven in de bucket: geen importer leest ze, en een CRAM van een volledig genoom is tientallen gigabytes. Alleen de dataset `alignments` mag naar zo'n bestand verwijzen; de import controleert dat het object bestaat en legt zijn URI vast. Omdat de tijdelijke map verdwijnt, noemen de joblog, het validatierapport, de herkomst van de familie en het register van bronbestanden de map en de objecten in de bucket. Heeft het manifest geen `family_id`, dan krijgt de familie de naam van de map in de bucket, zoals bij een lokale map.

Welke locaties gescand mogen worden, staat in `FAMILY_IMPORT_ROOTS`: een **allowlist**. Een pad buiten die lijst wordt geweigerd, zodat een beheerder niet zomaar een willekeurig bestand op de server kan laten inlezen: de controle meldt de fout `package_folder_not_allowed`, en het wegschrijven van een manifest of een bucket buiten de lijst krijgt `HTTP 403`. Alleen voor een lokale map staat de controle open als de lijst leeg is; dat is een bewuste ontwikkelmodus. Een bucket moet altijd in de lijst staan.

**Waar in de code:** `backend/app/core/object_storage.py` (de opslaglaag) en `backend/app/services/family_package_source.py` (de allowlist en het kopiëren).

## Wat een pakket bevat

Een geldig pakket is een map met een manifest en/of een PED-bestand. Het manifest kent vijftien soorten datasets:

- **Varianten:** `snv` (small variants, met optionele annotatietabel), `sv_needlr` (structurele varianten), `cnv` (CNV-calls met hun signaalbestanden), `mito` (mitochondriale varianten), `repeats_trgt` (repeat-expansies).
- **Tracks:** `wisecondorx` en `qdnaseq` (CNV-dekking en -segmenten), `coverage`, `apcad` en `pcf` (embryo-analyses), `haplotypes` (een gefaseerde familie-VCF van GLIMPSE2 of SHAPEIT5).
- **Overige:** `paraphase` (paraloge genen), `alignments` (CRAM/BAM voor de genoombrowser), `qc` (sequencing-QC) en `pipeline_info` (de run-registratie van de pipeline: toolversies en parameters).

De verwachte mapindeling per dataset staat in `docs/data-import.md`. De ontdekkingsstap zoekt die paden af en toont wat hij vond. De long-read-pipeline zet de toolversie in bestandsnamen; een zoekpad mag daarom een `*` bevatten, maar het manifest bevat altijd het concrete pad dat gevonden werd, nooit het patroon.

**De PED is de bron van waarheid voor de familie.** Een manifest zonder PED noemt al zijn leden onder `family.add_members` (zie het long-read-koppel hieronder). De controle leest de PED strikt: minstens zes kolommen, precies één familie. Elke dataset per sample moet naar een lid van de familie verwijzen. Het manifest mag klinische status, dragerschap en expliciete relaties toevoegen, en onder `family.add_members` een lid dat de PED mist: dat wordt gelezen als een extra PED-rij, met zijn `father` en `mother` als die gegeven zijn, en doorloopt dezelfde controles. Een lid dat al in de PED staat, mag er niet opnieuw in, en een relatie mag alleen leden noemen. Bij een import in een *bestaande* familie mag de PED ontbreken: dan wordt de stamboom uit de databank gebruikt.

**De PGT-pipeline (nf-cmgg/copgtm)** schrijft haar uitvoer per tool weg, met de PED in `ped/combined.ped`. Die PED geeft de embryo's het geslacht dat ngs-bits uit de reads afleidde, waardoor CoGA ze uit de PED alleen niet als embryo herkent, en ze mist de index: de verwant wiens haplotypes de twee haplotypes van de aangetaste ouder van elkaar onderscheiden. De ontdekkingsstap leest beide uit de samplesheet van de pipeline (`dashboard/samplesheet.csv`): de embryo's krijgen de rol embryo, en een index die de PED mist, wordt onder `family.add_members` toegevoegd. Hoe de index verwant is, staat in geen enkel pipelinebestand. De ontdekkingsstap stelt daarom een relatie voor op basis van wat KING mat tussen de index en het koppel: het kind van het koppel (proband, met beide ouders) als KING de index eerstegraads verwant meet met beide ouders, anders een verwant van onbekende graad (`family.relationships.relatives`) van de ouder met wie KING verwantschap meet, of van de aangetaste ouder als KING geen verwantschap ziet. De waarschuwing citeert KING en zegt welke relatie werd voorgesteld. Zonder relatie blijft het haplotype van de index grijs. Met een relatie van onbekende graad kleurt CoGA de index langs het hele genoom als die een ouder of kind van die ouder blijkt: als hij langs bijna elk chromosoom een van haar haplotypes deelt. Een verdere verwant (een broer of zus, een tante, een neef) deelt een haplotype maar over stukken, en zo'n stuk is op geïmputeerde low-pass genotypes niet merker per merker te vinden: het lijkt te veel op wat onverwanten toevallig delen. Zo'n verwant leest CoGA daarom alleen bij de ROI, zoals PGT-M een verre referentie leest: uit de informatieve merkers aan beide kanten van de ROI. Tonen de 3 Mb aan elke kant allebei hetzelfde haplotype van de ouder, dan kleurt CoGA dat haplotype bij de verwant over de ROI en die 3 Mb; de rest blijft grijs, net als waar een kant het niet duidelijk toont. De ouder die de pipeline volgde, wordt de aangetaste ouder (`metadata.pgt.affected_parents`) en haar index de index (`metadata.pgt.indexes`). Met het overervingsmodel ingevuld (`metadata.pgt.inheritance_model`) krijgen beide de status die het model vraagt: de ouder aangetast bij AD en XLD en voor een vader bij XLR, bewezen drager bij AR en voor een moeder bij XLR; de index aangetast bij AD, XLD en AR en voor een man bij XLR, bewezen draagster voor een vrouw bij XLR. De validatie toont elke afgeleide status. Een status die voor het lid onder `family.members` (of voor de toegevoegde index onder `family.add_members`) staat, gaat voor. De PCF-segmentatie leest CoGA alleen uit haar tabel (`apcad/<embryo>_pcf_mat_data.csv` en `_pcf_pat_data.csv`): een run die de PCF enkel in een HTML-plot tekent, krijgt geen PCF-track.

**De monogene NIPT (de NIPT-M-pipeline)** levert per ouder een VCF met één sample: die van het plasma van de moeder (cfDNA) en die van de vader, beide gemaakt met Mutect2 in tumour-only-modus. Daarnaast is er per sample een tabel met de dekking per capture-target (`coverage_<sample>.txt`). Vindt de ontdekkingsstap geen familie-VCF, dan zoekt hij zo'n paar: een kind uit de PED zonder eigen VCF (de foetus) waarvan de moeder en de vader elk een VCF met één sample hebben die naar hen genoemd is (`<sample>.vcf.gz` of `<sample>.<iets>.vcf.gz`, in de pakketmap of een map dieper). Het sample van de moeder geldt als het plasma. Het voorstel krijgt `analysis_type: monogenic_nipt`, de assay `nipt_cfdna` voor het plasma, de twee VCF's onder `datasets.snv.per_sample` en de dekkingstabellen onder `datasets.coverage.per_sample` als `target_table`, met een waarschuwing (`nipt_pair_detected`) die zegt welk sample als plasma en welk als vader werd genomen: de beheerder kijkt beide na vóór de import. Eén gecombineerde VCF met een kolom voor de vader en een voor het plasma kan ook nog, als gewone `family_vcf`.

**Een koppel voor dragerschapsscreening op de long-read-pipeline (nf-core/lrsvar)** heeft geen PED en geen gezamenlijke callset: elke partner heeft eigen bestanden (`snv/<sample>/annotation/<sample>_annot.vcf.gz`, `sv/<sample>/needlr/<sample>_sv_phased.needLR.<versie>.vcf.gz`, `repeats/<sample>/<sample>_tr.vcf.gz`, `paraphase/<sample>/<sample>.paraphase.json`). De importpagina toont zo'n map toch: een map met de mappen per sample van de pipeline geldt als pakket. Zonder PED neemt de ontdekkingsstap de leden uit die mappen en zet ze onder `family.add_members`, zodat het manifest geen PED nodig heeft. Het geslacht van elk lid is het karyotype waarmee TRGT zijn repeats genotypeerde (`--karyotype XX` of `XY` in de `##trgtCommand` van de TRGT-VCF): de pipeline neemt dat uit haar samplesheet, het is dus het geregistreerde geslacht, dat de Sample-QC met de reads vergelijkt. Twee leden van verschillend geslacht worden als koppel voorgesteld (`family.relationships.couples`, context `carrier screening`), de vrouw als moeder en de man als vader; de waarschuwing `ped_proposed_from_folders` zegt wat werd voorgesteld. De twee SNV-bestanden worden samen gelezen als de primaire callset van de familie (`snv.per_sample` met `source_format: clair3`): een site die beide partners dragen, wordt één rij met beide calls; een partner zonder record op de variant van de ander wordt daar als referentie gelezen, zoals een gezamenlijke VCF een gedekte site roept; DeepVariants `RefCall`- en `NoCall`-records blijven alleen bewaard waar de andere partner een variant heeft. Elk SNV-bestand moet het sample van zijn entry bevatten (het bestand van de partner wordt geweigerd), de bestanden moeten dezelfde volgorde van `##contig`-regels hebben en daarin gesorteerd zijn, en een import die de opgeslagen callset vervangt, moet het bestand brengen van elk sample met calls daarin. Zonder `source_format: clair3` (en buiten `analysis_type: monogenic_nipt`) weigert de controle een SNV-callset per sample, zodat een NIPT-paar waarvan het analysetype vergeten werd nooit als kiembaancalls gelezen wordt. De NeedlR-bestanden gaan onder `sv_needlr.per_sample`; een bestand waarvan de `Query_ID` een ander sample noemt, wordt geweigerd voordat er iets geschreven is.

**Waar in de code:** `backend/app/services/family_package_discovery.py` (ontdekken), `family_package_common.py` (de datasetlijst), `family_package_manifest.py` (de PED en het manifest lezen), `family_package_nipt.py` (het NIPT-paar), `family_package_long_read.py` (de leden van een long-read-pakket zonder PED) en `per_sample_small_variants.py` (de SNV-bestanden per sample als één callset).

## De dry-run: alles controleren vóór er iets wordt weggeschreven

De dry-run is de veiligheidspoort van de import. Hij voert dezelfde pakketcontroles uit als een echte import, maar stopt vóór de eerste schrijfactie. Twee verschillen: met `cancel` is een bestaande familie of een bestaand sample in een dry-run een waarschuwing en bij een echte import een fout (`existing_family_or_samples`); en dat het pakket bij `update` of `overwrite` dezelfde sample-ID's heeft als de bestaande familie, toetst pas het registreren, dus alleen een echte import. De controles, in volgorde:

1. **Pad** — de map moet binnen `FAMILY_IMPORT_ROOTS` liggen.
2. **Bestaan** — de map bestaat en het manifest bestaat.
3. **Manifestversie** — `schema_version` moet `1` zijn (ontbreekt ze, dan volgt een waarschuwing).
4. **ID's** — de familie-ID (uit het manifest, anders de mapnaam) en elke sample-ID (in de PED of de opgeslagen stamboom, onder `family.add_members`, en bij het ontdekken als naam van een map per sample van een long-read-pakket of in de samplesheet van de PGT-pipeline) is afdrukbare tekst zonder spaties. De witruimte errond valt weg; een stuurteken (een regeleinde, een tab, een escape, een NUL) of witruimte erin is een fout (`family_id_invalid`, `sample_id_invalid`). Dan leest de controle niets meer van het pakket, en de ontdekkingsstap stelt geen manifest voor. Een aanvraag die met zo'n ID een bestaande familie noemt, krijgt `400` nog voor de job bestaat. Dezelfde regel weigert zo'n ID met een `400` in de Family Builder, bij een PED-upload en bij het bewerken van leden (hoofdstuk 15).
5. **PED** — minstens zes kolommen, één familie, unieke sample-id's, geldige codes voor geslacht en status, de juiste familie-id, en elke ouder staat in de PED met het juiste geslacht; een lid uit `family.add_members` staat niet al in de PED; elke relatie uit het manifest noemt een lid, en een verwant van onbekende graad (`relatives`) is niet aan zichzelf gekoppeld, noch aan wie al zijn ouder, kind of partner is; de aangetaste ouder (`metadata.pgt.affected_parents`) is een ouder in de PED, en de index (`metadata.pgt.indexes`) een ander lid.
6. **Per dataset** — de bestanden bestaan (een alignment van een pakket uit de cloud: in de bucket); een gecomprimeerde familie-VCF van `snv`, `sv_needlr` of `repeats_trgt` heeft een index (`.tbi`, `.csi` of `.idx`), een ongecomprimeerde `.vcf` en een bestand per sample mogen zonder, en een index die het manifest noemt, moet bestaan. Een onbekende dataset is een **fout**; een ontbrekende kerndataset (`snv` of `sv_needlr`) geeft een **waarschuwing**. Een SNV-callset per sample (`snv.per_sample`, zonder `family_vcf`) mag alleen bij `analysis_type: monogenic_nipt`, of als primaire callset van een long-read-koppel met `source_format: clair3`; anders is ze een fout (`dataset_per_sample_unsupported`), zodat een NIPT-paar nooit als kiembaancalls gelezen wordt. Elk van zijn VCF's bevat precies één sample (`dataset_vcf_not_single_sample`) en heeft geen index nodig. Een dekkingstabel (`target_table`) noemt in haar kopregel de kolommen `chromosome`, `start`, `end`, `attribute` en `mean` (`coverage_target_table_columns`).
7. **Fenotypes (HPO)** — een fenotypetabel moet naar samples uit de PED verwijzen; slechte rijen worden waarschuwingen.

Elke bevinding heeft een vaste `code`, zodat de UI en een auditor een fout eenduidig kunnen benoemen. Is er één fout, dan stopt de import met "Package validation failed; no data were imported." en wordt er niets weggeschreven.

**Waar in de code:** `backend/app/services/family_package_validation.py`; de regel voor ID's in `family_identifiers.py`.

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
| `snv` van een NIPT-paar (één VCF per sample) | Small variants, callset `nipt`: één rij per variant met de call van elk sample, met de FILTER-waarden en callermetrieken van die call | ClickHouse |
| `sv_needlr`, `cnv` (VCF) | Structurele varianten; bij `cnv` ook dekking, kopieaantal en allelfractie als interval-tracks | ClickHouse |
| `mito` `sv_vcf` (Sniffles2, chrM) | Structurele varianten, callset `mito_sv`, met de heteroplasmie per sample; per sample vervangen | ClickHouse |
| `wisecondorx`, `qdnaseq`, `apcad`, `pcf`, `coverage` | Interval-tracks | ClickHouse |
| `coverage` met `target_table` (NIPT) | Interval-track `target_coverage`: per capture-target de gemiddelde diepte, het gen en de overige kolommen van de tabel | ClickHouse |
| Bron van elke interval-track | `sample_interval_track_sources` | Postgres |
| `repeats_trgt` (VCF) | `repeat_expansions` | Postgres |
| `paraphase` (JSON) | `sample_paraphase_results` | Postgres |
| `qc`, `alignments` | De metadata van het sample (`samples.metadata`); de KING-tabel van de PGT-pipeline in de metadata van de familie | Postgres |
| `haplotypes` | De fasecorrecties van de ouders (`haplotype_phase_corrections`, bij elke import vervangen) en de lezing van de haplotype-oorsprong door de PGT-pipeline (`pipeline_haplotype_origin`), in de metadata van de familie | Postgres |
| `pipeline_info` | `family_annotation_manifest` en de metadata van de familie | Postgres |
| Fenotypes (HPO) | `individual_hpo` | Postgres |
| Familiestructuur (PED, manifest) | `families`, `samples`, `family_members`, `family_relationships`, `family_structure_versions` (een eerste versie), `family_projects`, `sample_projects` | Postgres |
| Elk ruw bronbestand | `raw_import_files` | Postgres |
| Tool- en databankversies | `family_annotation_manifest` | Postgres |
| De importjob zelf | `family_import_jobs` | Postgres |

**Een bestand per sample** (`repeats_trgt`, `mito` en `cnv` onder `per_sample`, de SNV-bestanden van een long-read-koppel, en een losse TRGT-upload) wordt gelezen uit de kolom van dat sample (`per_sample_vcf_column`): de kolom die naar het sample genoemd is (`<sample>_sort`), waar ze ook staat; een enkele kolom zonder samplenaam (HiFiCNV's `Sample0`) hoort bij het sample. Noemt de kolom een ander sample, van deze familie of een andere, en geen kolom dit sample, dan wordt dat bestand geweigerd voor er iets van geschreven is, en faalt de dataset. `mito` en de SNV-bestanden controleren al hun bestanden voor ze er één schrijven; bij `repeats_trgt` en `cnv` kan een eerder bestand van dezelfde dataset al geschreven zijn. `vcf_sample` op de entry is het uitdrukkelijke woord van de beheerder: de kolom die het noemt wordt gelezen. Zo kan een labo dat zijn buisjes nagekeken heeft, een sample aan een bestand koppelen dat naar een ander buisje genoemd is. Een NIPT-paar valt bewust buiten deze regel: de ene kolom van elk bestand hoort bij het sample van zijn entry.

Elke datasetsoort heeft een eigen importfunctie in een register. Een soort zonder importfunctie is een fout: een test bewaakt dat, en de import faalt er luid op.

**NIPT-artefacten** (de lijst van terugkerende artefacten per assay) worden niet door de pakketimport gevuld; ze hebben een eigen beheer (hoofdstuk 15). De import neemt wel een gedeclareerd analysetype (bv. `monogenic_nipt`), een assay per sample (bv. `nipt_cfdna`) en het capturepanel van een sample (`assay_panel`, dat de artefactenlijst afbakent) over in de metadata, zodat de NIPT-context later wordt herkend (hoofdstuk 8).

**De import van een NIPT-paar** leest eerst de VCF van het plasma (het sample met de assay `nipt_cfdna`, anders de moeder), wat de volgorde in het manifest ook is. Daarna voegt hij elk ander bestand per sample samen met de opgeslagen rijen (`overwrite_scope="samples"`, zoals bij `mito`): een bestand vervangt alleen de calls van zijn eigen sample. De VCF van de vader is grotendeels ruis op laag niveau. De import houdt van de vader alleen een record met een call vanaf 15% alt-reads (`NIPT_PATERNAL_KEEP_MIN_VAF`, onder de 20% van een heterozygote call, zodat geen genotype verloren gaat) of op een positie waar het plasma een call heeft (een MNV over een van zijn basen); de andere telt hij per sample als `skipped_by_filter`. Omdat elk bestand één sample bevat, horen de FILTER-waarden en de callermetrieken van een record (TLOD, FS, mapping quality, …) bij die ene call; ze worden bij de call bewaard voor de kwaliteitsfilter en de de-novotriage. In `update`-modus slaat de import het paar over als de familie al NIPT-calls heeft. Een dekkingstabel wordt de track `target_coverage` van het sample, in de plaats van de vorige.

De **runparameters** van de pipeline (genoombuild, welke caller welke variantklasse maakte, welke stappen liepen) staan in de inklapbare sectie *Analysis pipeline settings* op de familiewerkruimte en in dezelfde sectie van het rapport. De **toolversies** verschijnen in de herkomstvoettekst en in de regel *Modules & versions* van het rapport.

**Waar in de code:** `backend/app/services/family_package_datasets.py` (de importfuncties per dataset) en `family_package_registration.py` (familie, samples en herkomst registreren); de weergave in `frontend/src/pages/families/PipelineSettingsPanel.tsx`.

## Jobs volgen en fouten afhandelen

Elke import is een rij in `family_import_jobs`, met een status die de databank bewaakt: `queued → validating → running → completed | failed`. De rij bewaart ook de validatiefouten en -waarschuwingen, de logregels, een samenvatting per dataset en de tijdstippen.

Een job gaat pas naar `running` als hij de familie heeft vastgelegd die hij importeert (`family_id`), en dat gebeurt vóór de import iets van die familie schrijft: daaraan herkent een ondertekening een lopende import (hoofdstuk 11, poort 0b). Lukt dat niet, dan stopt de import zonder te schrijven en eindigt de job op `failed`, met de reden. De latere updates van de job (logregels, samenvatting per dataset, heartbeat) zijn informatief: mislukt er een, dan loopt de import gewoon door, want de status en de familie blijven staan.

**Voortgang en resterende tijd.** Elke dataset krijgt in zijn samenvatting een `progress`: wanneer hij begon en eindigde. De importers die tellen wat ze lezen (de small variants: de SNV-VCF, de SNV-bestanden van een long-read-koppel, de twee bestanden van een NIPT-paar en de geïmputeerde genotypes; en APCAD) melden ook hoeveel bytes van hun bestanden ze gelezen hebben, van een gzip-bestand de gecomprimeerde. Daaruit volgt het deel dat gelezen is, en de resterende tijd: de rest van de bestanden aan het tempo sinds de eerste melding. De tijd daarvoor telt niet mee, want dan leest de importer nog niets (een VEP-tabel inlezen, de rijen van een overwrite wissen). Een schatting komt er pas na een halve minuut lezen. Ze geldt voor de dataset die loopt; de datasets erna worden niet geschat, en de pagina zegt hoeveel er volgen.

Een worker neemt een job atomair (`FOR UPDATE SKIP LOCKED`), zodat meerdere workers elkaar niet hinderen. Een lopende job schrijft elke minuut een levensteken (*heartbeat*). Is dat tien minuten oud, dan is het proces dat de job draaide gestopt (een herstart, een crash, geen geheugen meer), en neemt de volgende worker de job over:

- Was de import nog niet begonnen de familie te schrijven (de job stond nog op `validating`), dan draait de worker hem opnieuw van bij het begin. De logregels van de eerdere poging blijven staan.
- Was hij dat wel (`running`), dan eindigt de job op `failed`, onderbroken, en draait hij niet opnieuw: een nieuwe run van bij het begin maakt niet ongedaan wat hij al schreef. Het log en de samenvatting per dataset blijven zoals de import ze achterliet.

De foutafhandeling is zo gebouwd dat er nooit ongemerkt een half-geïmporteerde familie achterblijft ("fail-clean"):

- Elke dataset wordt apart geïmporteerd. Faalt er één, dan wordt zijn Postgres-werk teruggedraaid en gaat de import door met de rest. Wat hij al in ClickHouse schreef, ruimen alleen de small variants en de haplotypes zelf op; van een andere dataset kan een deel blijven staan, en dan geldt wat hieronder volgt (verwijderen, terugzetten of de vlag `import_incomplete`).
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

De pagina *Package Import* (`/package-import`) leidt door deze stappen. Ze gebruikt `POST /family-imports/validate` niet: *Validate package* zet een dry-run-job in de wachtrij.

1. **Familiemap kiezen** uit de gevonden pakketten, of een pad intypen.
2. **Doel kiezen:** een nieuwe of een bestaande familie, met een beleid voor bestaande data (`cancel`, `update` of `overwrite`).
3. **Manifest ontdekken:** een overzicht per dataset en een bewerkbaar voorbeeld van `manifest.yaml`, eventueel weg te schrijven. Wat de ontdekkingsstap voorstelt en de beheerder moet nakijken (het NIPT-paar, het koppel uit een long-read-pakket, de koppeling van een PGT-index), staat onder *Check before writing the manifest*.
4. **Valideren of importeren:** de keuze *Dry run* staat standaard aan. Voor een echte import is een project verplicht.
5. **Volgen:** de pagina ververst de status zolang de job loopt en toont tellers, fouten, waarschuwingen, het resultaat per dataset en de logregels. Eerdere imports blijven op te roepen. Per dataset toont ze hoe lang hij duurde, en van de dataset die loopt het deel van zijn bestanden dat gelezen is en ongeveer hoe lang hij nog duurt, aan het tempo tot dan (zie hierboven).

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
| `backend/app/services/family_package_nipt.py` · `nipt_target_coverage.py` · `vcf_call_metrics.py` | Monogene NIPT: het paar ontdekken en in volgorde importeren met de filter op de VCF van de vader, de dekkingstabel lezen, de callermetrieken per call |
| `backend/app/services/family_package_registration.py` | Familie en samples registreren, herkomst vastleggen |
| `backend/app/services/family_package_jobs.py` | Wachtrij, jobs nemen, heartbeat |
| `backend/app/services/import_progress.py` | Voortgang per dataset en de resterende tijd |
| `backend/app/services/vcf_header_provenance.py` · `annotation_manifest_service.py` | Versies uit de headers; het annotatiemanifest |
| `backend/app/services/raw_import_files_pg.py` | Het register van bronbestanden met SHA-256 |
| `frontend/src/pages/dashboard/FamilyPackageImportPanel.tsx` | De beheerpagina |
