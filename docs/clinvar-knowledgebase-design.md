# Ontwerp: ClinVar als eigen kennisbron van CoGA

Status: **beslist door de eigenaar op 2026-11-27** (B1–B13, §9) en gebouwd. B13 vervangt B7 en de
terugval op de annotatie van B8, B9, B11 en B12: voor small variants toont en gebruikt CoGA alleen
de ClinVar van zijn eigen release. QA heeft het ontwerp nog niet beoordeeld; de vragen voor QA
staan in §10.

## 1. Aanleiding

Zonder eigen release kent CoGA van een small variant alleen de ClinVar-classificatie uit de
geannoteerde VCF: het ClinVar-bestand dat de pipeline met VEP `--custom` annoteert
(`ClinVar_CLNSIG`), en zonder zo'n bestand de ClinVar van VEP's cache
([annotation-provenance.md](annotation-provenance.md), "The ClinVar release"). Die waarde wordt bij
de import gelezen en staat vast per callset. Dat heeft drie gevolgen:

- **Een open analyse staat op de ClinVar van haar pipelinerun.** Een familie die in mei
  geannoteerd werd, toont in november nog ClinVar van mei. Een nieuwere classificatie bereikt
  CoGA alleen als de pipeline opnieuw annoteert en de familie opnieuw geïmporteerd wordt; de
  drift-check toont dan het verschil ([clinical-traceability.md](clinical-traceability.md),
  "Evidence drift").
- **De Variant Explorer zet releases naast elkaar.** Elke rij wordt gelezen tegen haar eigen
  annotatie, dus twee families met dezelfde variant kunnen een andere ClinVar-classificatie
  tonen.
- **Na de sign-out is er geen signaal.** Herclassificeert ClinVar een gerapporteerde variant,
  dan merkt CoGA dat niet op.

seqr lost dit op door ClinVar los te koppelen van de import: ClinVar is een aparte tabel in
ClickHouse, per release herladen en bij het zoeken aan de varianten gekoppeld, zonder één
variant opnieuw te annoteren.

CoGA laadt al zelf kennisbronnen, telkens met de release of de SHA-256 van het bestand
([TF-08](regulatory/TF-08-soup-register.md)): HPO, Monarch, DGV, en van ClinVar zelf al twee
bestanden. De klinische CNV-kennisbank leest ClinVar's *variant summary*, en de genpagina
toont ClinVar's gen-aandoeningrelaties (`gene_condition_source_id`), die het ondertekende record
per gerapporteerd gen bevriest. Dit ontwerp voegt ClinVar voor small variants toe op dezelfde
manier.

## 2. Doel en grenzen

Doel:

- **D1.** Elke small variant toont de classificatie in de ClinVar-release die CoGA geladen heeft,
  en alleen die (B13).
- **D2.** Een filter vindt de varianten die in CoGA's release een bepaalde classificatie hebben,
  en een variant die in CoGA's release (likely) pathogenic is, valt niet weg door een frequentie-
  of consequentiefilter.
- **D3.** Een classificatie en een sign-out bevriezen welke CoGA-release en welke waarde
  getoond werden; een nieuwere release met een andere waarde is drift.
- **D4.** De Variant Explorer leest voor het hele cohort één release.

Grenzen:

- CoGA annoteert verder niets zelf. VEP, gnomAD, dbNSFP en SpliceAI blijven uit de pipeline
  komen.
- CoGA koppelt alleen op het exacte allel (chromosoom, positie, REF, ALT), nooit op rsID of
  positie alleen. Dat volgt de keuze die al geldt voor de VEP-cache: ClinVar op rsID-niveau kan
  bij een ander allel horen.
- Een ondertekende analyse verandert nooit. Een heranalyse is een nieuwe analyse.
- De somatische en oncogeniciteitsvelden van ClinVar (`ONC`, `SCI`) vallen buiten dit ontwerp.

## 3. Wie ClinVar leest

Elke lezer leest alleen CoGA's release (B13); de tabel zegt waar en hoe. De import vergelijkt de
geannoteerde waarde alleen met CoGA's kopie van dezelfde release (§6.6). De kolom *Fase* zegt in
welke fase de lezer CoGA's release ging lezen (§8.1).

| Lezer | Waar | Wat het leest | Fase |
| --- | --- | --- | --- |
| Import | [variant_annotation_parser.py](../backend/app/services/variant_annotation_parser.py), [annotation_table_parser.py](../backend/app/services/annotation_table_parser.py) | `ClinVar_CLNSIG`/`CLNSIG` → `clinvar`; `CLIN_SIG` → `clinvar_vep_cache` (of `clinvar` zonder ClinVar-bestand); review-status, aandoeningen, AlleleID, VariationID; per allel van een multi-allelisch record (`alt_allele`) | — |
| Opslag | [clickhouse_variant_rows.py](../backend/app/services/clickhouse_variant_rows.py), [clickhouse_variant_storage.py](../backend/app/services/clickhouse_variant_storage.py) | alle sleutels in `variants/details.annotationsJson`, waar de import ze vergelijkt (§6.6); de annotatietabellen en `entries.transcripts` houden geen ClinVar | — |
| Filters (familie, Explorer, NIPT) | [clickhouse_variant_queries.py](../backend/app/services/clickhouse_variant_queries.py) | `clinvar` (opnemen) en `exclude_clinvar` (uitsluiten): `hasAny(classification_terms, …)` in CoGA's release, op de allel-sleutels van de rij (`entries.allele_keys`); de **P/LP-override** (`clinvar_pathogenic_override`, standaard aan): een P/LP-variant passeert de consequentie-, frequentie- en in-silico-filters; de salient-markering op het genoomspoor | override: 1; rest: 2 |
| Ranking | [variant_prioritization.py](../backend/app/services/variant_prioritization.py), [variant_ranking_cache.py](../backend/app/services/variant_ranking_cache.py) | `pathogenicity_score` op de classificatie van de getoonde ALT in CoGA's release; de cache-sleutel bevat die release (`clinvar_coga_release`) | 2 |
| ACMG | `frontend/src/lib/acmg/evaluate.ts`, `frontend/src/lib/clinvar.ts` | PP5/BP6, altijd *consider*; mtDNA: `evaluateMito.ts` | 2 |
| NIPT | [nipt_service.py](../backend/app/services/nipt_service.py), [nipt_diagnostic_filters.py](../backend/app/services/nipt_diagnostic_filters.py), [nipt_artifact_pg.py](../backend/app/services/nipt_artifact_pg.py) | triage ("ClinVar pathogenic: not excluded as recurrent"), diagnostische kandidaten (strikt P/LP), en de bescherming van de artefactlijst: de classificatie in CoGA's release | 2 |
| Evidence-snapshot en drift | [small_variant_review_acmg.py](../backend/app/services/small_variant_review_acmg.py), [classification_drift_service.py](../backend/app/services/classification_drift_service.py) | bevriest per ALT de waarde in CoGA's release (`clinvar_coga`); drift vergelijkt die classificatie | 1 |
| Manifest en ondertekend record | [annotation_manifest_service.py](../backend/app/services/annotation_manifest_service.py), [report_signout_service.py](../backend/app/services/report_signout_service.py), `signedReportRecord.ts`, `SignedFamilyReport.tsx` | modules `clinvar` en `clinvar_vep_cache` (release uit de bestandsnaam); de snapshot per gerapporteerde review; de sectie `reference_data` (genreferentie, klinische CNV's) | 1 |
| Tonen | `VariantOut` in [schemas/variants.py](../backend/app/schemas/variants.py); `SmallVariantCards.tsx`, `smallVariantResultUtils.ts`, `SmallVariantTable.tsx`, `AcmgEvidencePanel.tsx`, `GlobalSmallVariantTable.tsx`, het rapportverhaal | classificatie, sterren, aandoeningen, VariationID-link, kleur in tabel en spoor | 1 |

## 4. Bron

ClinVar's eigen VCF, per assembly, van NCBI:

- `https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar_YYYYMMDD.vcf.gz`, en hetzelfde
  onder `vcf_GRCh37/`, elk met een `.md5`. Op 2026-11-23 was de nieuwste
  `clinvar_20261122.vcf.gz`, 192 MB, met `##fileDate=2026-11-22` en `##reference=GRCh38`.
- NCBI maakt elke week een nieuw bestand. Alleen de maandrelease (de eerste donderdag van de
  maand) wordt gearchiveerd.
- Eén rij per variant, één ALT per rij: een enkelvoudig allel met precieze grenzen, korter dan
  10 kb, in VCF-vorm met ankerbase (`1 66926 AG A`). Chromosomen zonder `chr`, mtDNA als `MT`.
  Een rij zonder ALT (`.`) slaat CoGA over. Het bijbehorende `_papu`-bestand (PAR op Y,
  alternatieve loci, patches) laadt CoGA niet.
- Een variant die alleen als deel van een haplotype geclassificeerd is (*included variant*),
  heeft geen `CLNSIG` maar `CLNSIGINCL`. CoGA toont die als "alleen als deel van een
  combinatie", zonder classificatie.

Waarom de VCF en niet de *variant summary*: het is precies het bestand dat de pipeline met
`--custom` annoteert. Daardoor kan CoGA voor dezelfde release nagaan dat zijn eigen koppeling
exact dezelfde waarde geeft als de pipeline (§6.6).

Voor een assembly zonder ClinVar-VCF (T2T) is er geen CoGA-release en dus geen ClinVar; het
detailpaneel van de kaart toont "niet beschikbaar voor deze assembly".

## 5. Laden en releasebeheer

### 5.1 De laadjob

Een laadjob zoals die van DGV (`dgv_load_jobs`), in een nieuwe tabel `clinvar_load_jobs`
([02_reference.sql](../backend/db/schema/postgres/02_reference.sql)): assembly, release, URL,
de MD5 van NCBI, de SHA-256 die CoGA berekent, gelezen bytes, records gelezen, opgeslagen en
overgeslagen (per reden), status, aanvrager, worker en hartslag. Eén actieve job tegelijk.

De job:

1. downloadt het bestand en de `.md5`, en weigert een bestand waarvan de MD5 niet klopt;
2. berekent de SHA-256;
3. leest de release uit `##fileDate`, en weigert een bestand waarvan die datum niet de datum in
   de bestandsnaam is, of waarvan `##reference` niet de assembly is;
4. leest elk record: chromosoom, positie, REF, ALT, `ID` (VariationID), `ALLELEID`, `CLNSIG`,
   `CLNSIGCONF`, `CLNREVSTAT`, `CLNDN`, `GENEINFO`, `CLNSIGINCL`;
5. berekent per record de allel-sleutel (§7);
6. schrijft alles in de partitie van die release (§5.2), telt de opgeslagen rijen na, en
   registreert de release pas dan in `reference_dataset_imports` (`dataset_type = 'clinvar'`,
   met release, URL en SHA-256).

Een job die mislukt, verwijdert zijn partitie (`ALTER TABLE … DROP PARTITION`): geen mutatie,
en er blijft niets half geladen zichtbaar. De huidige release is de nieuwste die volledig
geladen is.

### 5.2 Opslag in ClickHouse

Eén tabel per assembly, naast de andere small-variant-tabellen:

```sql
CREATE TABLE `GRCh38/SNV_INDEL/clinvar` (
    release              Date,                          -- de partitie
    allele_key           UInt64,                        -- §7
    variantId            String,                        -- het allel in minimale vorm
    variation_id         UInt32,
    allele_id            UInt32,
    classification       String,                        -- CLNSIG zoals gepubliceerd
    classification_terms Array(LowCardinality(String)), -- CLNSIG in termen (_status_terms)
    conflicting          String,                        -- CLNSIGCONF
    review_status        LowCardinality(String),
    conditions           Array(String),
    genes                Array(String),
    included_only        Bool                           -- alleen CLNSIGINCL
)
ENGINE = MergeTree
PARTITION BY release
ORDER BY allele_key
```

- `classification_terms` wordt gemaakt met `_status_terms` (kleine letters, spaties, gesplitst
  op `/`, `,`, `\|`, …), zoals `_status_filter_terms` de woorden van een filter normaliseert,
  zodat beide dezelfde woorden testen.
- De sterren worden bij het tonen afgeleid uit `review_status`, zoals nu
  ([clinvar_review_status.py](../backend/app/services/clinvar_review_status.py)).
- ClickHouse en niet Postgres, omdat de filters in ClickHouse lopen en de tabel daar op de
  allel-sleutel met de rijen van een familie samenkomt. Welke release de huidige is, staat in
  Postgres.

### 5.3 Wanneer een nieuwe release geladen wordt

- **Bij de eerste start** op een databank zonder ClinVar voor een assembly laadt CoGA de
  nieuwste release, zoals voor HPO en Monarch (schakelaar `REFERENCE_BOOTSTRAP_CLINVAR`).
- **Elke week automatisch (B3).** Een lus zoals die van de ClickHouse-integriteitscontrole
  ([clickhouse_integrity_monitor.py](../backend/app/services/clickhouse_integrity_monitor.py))
  kijkt elke dag of NCBI een nieuwer weekbestand heeft, en zet dan een laadjob in de wachtrij,
  als aanvrager `clinvar-weekly`. Een nieuwe release komt zo binnen een dag na haar publicatie
  binnen. Een beheerder kan een lading ook zelf starten.
- **Vrijgave na controles (B3b).** Een volledig geladen release wordt meteen de huidige, als:
  de MD5 klopt, `##fileDate` de datum in de bestandsnaam is, `##reference` de assembly is, en
  ze niet meer dan 5 % minder records heeft dan de huidige. Faalt een controle, dan blijft de
  huidige release de huidige, wordt de nieuwe partitie verwijderd, en krijgt elke actieve
  beheerder een mail (zoals bij een account dat op goedkeuring wacht) met een melding op de
  beheerpagina.
- **Bewaren (B4):** de huidige en de vorige release (voor "wat veranderde in deze release").
  Wordt een nieuwe release de huidige, dan wordt de partitie van de release daarvoor
  verwijderd. De traceerbaarheid hangt daar niet van af: een snapshot bevriest de waarde zelf.

## 6. Lezen, tonen en traceerbaarheid

### 6.1 Eén release per verzoek

Een verzoek leest de huidige release één keer uit Postgres, geeft ze als parameter aan elke
ClickHouse-query mee, en zet ze in zijn antwoord. Zo toont één pagina nooit twee releases, ook
niet als een beheerder intussen een nieuwe vrijgeeft.

### 6.2 Tonen

- `VariantOut` krijgt per allel een `clinvar_coga`: release, classificatie, review-status,
  sterren, aandoeningen, VariationID, of de reden dat er geen waarde is (*niet in deze release*,
  *geen release geladen*, *niet beschikbaar voor deze assembly*).
- Op de variantkaart één waarde, gewoon *ClinVar* genoemd, zonder datum: de classificatie van
  het getoonde allel in CoGA's release, met sterren, review-status en aandoeningen, gelinkt naar
  de VariationID (B13). Het detailpaneel toont de waarde van elk allel, of de reden dat er geen is.
  De ClinVar van de annotatie (het ClinVar-bestand of VEP's cache) wordt nergens getoond.
- De kleur van kaart en tabelrij volgt die waarde. De Explorer toont per rij de classificaties
  van de ALT's van de variant in de release; de export de classificatie en de sterren van het
  getoonde allel.

De pagina zoekt de waarden op per allel-sleutel voor de varianten die ze toont, zoals ze nu de
details opzoekt.

### 6.3 Filters

- **Het filter *ClinVar status* (`clinvar`)** leest CoGA's release (B13): het houdt een variant
  waarvan een allel een gevraagde status heeft. Het test
  `arrayExists(k -> k IN (SELECT allele_key FROM … WHERE release = … AND hasAny(classification_terms, …)), e.allele_keys)`
  en leest alleen de rijen van de familie. Er is geen tweede ClinVar-filter.
- **De uitgesloten statussen (`exclude_clinvar`)** laten een variant alleen weg als elk allel een
  uitgesloten status heeft (`arrayAll`): een multi-allelische variant met één pathogeen allel valt
  nooit weg door de benigne status van een ander allel.
- **De P/LP-override** laat een variant door als CoGA's release een allel ervan (likely)
  pathogenic noemt (B1). Zonder dat valt een variant die pas na de annotatie pathogenic werd,
  weg door de frequentie- of consequentiefilters, en dat is het gevaar dat dit ontwerp moet
  afdekken (H1).
- Zonder release voor de assembly heeft geen variant ClinVar: het statusfilter houdt niets, de
  uitsluiting laat niets weg en de override laat niets extra door.

### 6.4 Evidence-snapshot en drift

- Een classificatie bevriest `clinvar_coga`, per allel van het record: release, classificatie,
  review-status, sterren, VariationID, of de reden dat er geen waarde is. De geannoteerde ClinVar
  wordt niet bevroren en niet vergeleken (B13).
- De drift-check vergelijkt dat met de huidige release. Een andere classificatie is `drifted`,
  met `changed: clinvar_coga` en de waarde van → naar, en houdt de sign-out tegen tot ze erkend
  is, zoals elke drift nu (REQ-TRACE-006).
- Een andere review-status of een ander aantal sterren bij dezelfde classificatie is geen drift:
  de drift-check toont het van → naar, maar de sign-out gaat door (B5).

### 6.5 Manifest en ondertekend record

- Het manifest krijgt in de platformlaag een module `clinvar_coga` (*ClinVar (CoGA)*): release,
  URL en SHA-256 uit `reference_dataset_imports`. Een eigen sleutel, want bij dezelfde sleutel
  wint de pipelinelaag van de platformlaag. `REFERENCE_MODULE_KEYS` krijgt `clinvar_coga`, zodat
  een ondertekend record ook "niet geladen bij de sign-out" vastlegt.
- De sectie `reference_data` van het ondertekende record noemt de ClinVar-release naast de
  genreferentie en de klinische CNV's.
- De snapshot van elke gerapporteerde review draagt `clinvar_coga`. Dat wordt ingelezen in
  `signedReportRecord.ts` en getoond in `SignedFamilyReport.tsx`
  ([clinical-traceability.md](clinical-traceability.md), "Adding a section to the signed
  record").
- Het laden en vrijgeven van een release is een beheeractie in `audit_log_events`.

### 6.6 Concordantie met de pipeline

Elke callset die met een ClinVar-bestand geannoteerd is, is een controle op CoGA's koppeling:
staat dezelfde release in CoGA, dan moet CoGA voor elk allel dezelfde classificatie, dezelfde
review-status en dezelfde VariationID vinden als de pipeline.

- **Bij de validatie:** een release laden en ze vergelijken met de geannoteerde
  validatiefamilies. Elk verschil krijgt een reden (allelrepresentatie, included variant,
  `_papu`). Het aanvaardingscriterium is 100 % of elk verschil verklaard; dat is bewijs voor
  [TF-09](regulatory/TF-09-verification-validation.md).
- **Bij elke import (B6):** staat de release van het ClinVar-bestand van de callset in CoGA (de
  huidige of de vorige), dan telt de import de overeenkomsten en verschillen en bewaart hij die
  bij de import. De importpagina toont elk verschil met de reden. Staat de release niet in CoGA,
  dan noteert de import *niet vergeleken: release niet geladen*.

### 6.7 Na de sign-out: heranalysesignaal (fase 3)

Een lijst, alleen lezen: ondertekende analyses waarvan een geclassificeerde of gerapporteerde
variant in de huidige CoGA-release een andere ClinVar-classificatie heeft dan bevroren. CoGA
verandert niets aan de analyse; wie de lijst opvolgt en hoe, bepaalt de labo-procedure. Er gaat
geen mail uit (B10).

## 7. Variant-identiteit en koppeling

CoGA's eigen sleutel volstaat niet voor de koppeling:

- `variantId` is `chrom-pos-ref-alt` zoals de VCF het schrijft, met alleen `chr` weggenomen
  (`chrM` wordt `M`, `MT` blijft `MT`), en `key` is een hash van de assembly en die `variantId`
  ([clickhouse_variant_ids.py](../backend/app/services/clickhouse_variant_ids.py)).
- CoGA normaliseert de allelen niet: geen inkorting, geen uitlijning naar links, en een
  multi-allelisch record blijft één rij met `ALT` als `G,T`.

ClinVar schrijft één allel per rij. Daarom krijgen beide kanten een **allel-sleutel**: de hash van
de assembly en het allel in minimale vorm.

- **Minimale vorm, zonder referentiegenoom:** chromosoom canoniek (`MT` → `M`), dan per ALT het
  gemeenschappelijke achtereinde van REF en ALT weg, dan het gemeenschappelijke begin weg op één
  ankerbase na (de positie schuift mee). Zo wordt het tweede allel van `1 100 AT A,ATT` het allel
  `1 100 A AT`, zoals ClinVar het schrijft.
- **`entries.allele_keys Array(UInt64)`:** de import schrijft per rij de allel-sleutels van haar
  ALT's. Het filter en de Explorer lezen die kolom; de pagina berekent ze voor de varianten die
  ze toont.
- **Uitlijning naar links:** daarvoor is het referentiegenoom nodig, en dat heeft CoGA niet. Een
  indel in een herhaling die de pipeline anders uitlijnt dan ClinVar, krijgt dus geen
  koppeling. De concordantie van §6.6 meet hoe vaak dat gebeurt. Is het niet nul, dan wordt
  uitlijning naar links (`bcftools norm -f`) een eis aan de pipeline, niet iets wat CoGA doet.

## 8. Gevolgen

### 8.1 Fasering

| Fase | Inhoud | Waarde van record voor de andere lezers |
| --- | --- | --- |
| 1 | Laden (§5); tonen, nieuw filter, P/LP-override, snapshot, drift, manifest, ondertekend record, concordantie (§6.1–6.6); `allele_keys` (§7) | de annotatie |
| 2 | Elke andere lezer leest **CoGA's release, en alleen die** (B13): de filters `clinvar` en `exclude_clinvar` en de ranking (met de release in de cache-sleutel; B8), PP5/BP6 en de bescherming van de NIPT-artefactlijst (B9), de NIPT-triage en de diagnostische NIPT-kandidaten (B11), de mtDNA-criteria die ClinVar lezen (`evaluateMito.ts`; MITOMAP blijft uit de annotatie komen; B12), het genoomspoor en de export. Een allel dat de release niet bevat, heeft geen ClinVar | CoGA's release |
| 3 | Heranalysesignaal na de sign-out (§6.7) | — |

De NIPT-triage en de diagnostische kandidaten volgen de vastgezette criteria van de R
NIPT-M v0.7.3-pipeline (REQ-NIPT-011, REQ-NIPT-020), die de geannoteerde ClinVar lezen. Met B11 en
B13 leest CoGA daar ClinVar uit zijn eigen release en wijkt het daarvan af. Fase 2 vraagt daarom
een nieuwe validatie van de NIPT-kandidaten tegen de R-pipeline (§8.2).

Met B13 is CoGA's release de waarde van record, dus kan de pipeline ClinVar weglaten; dan vervalt
wel de concordantie van §6.6. Dan verandert
de annotatieset van een variant niet meer met elke ClinVar-release (ClinVar zit nu in de hash van
de annotatieset), en kan een import een variant waarvan dezelfde annotatie al opgeslagen is,
overslaan. Dat is een volgende stap, niet dit ontwerp.

### 8.2 Technisch dossier

- **TF-02:** de grens blijft *geannoteerde VCF → bevroren analyse*. CoGA legt er een eigen
  geladen kennisbron naast, zoals voor HPO, Monarch, DGV, de klinische CNV-kennisbank en
  ClinVar's gen-aandoeningrelaties. De invoer *Reference data* noemt ClinVar al.
- **TF-08:** de rij ClinVar beschrijft de eigen download: per assembly de wekelijkse VCF van
  NCBI, release uit `##fileDate`, MD5 van NCBI en SHA-256 bewaard, de nieuwste bij de eerste
  start, daarna elke week automatisch na de controles van §5.3.
- **TF-06:** H8 krijgt een maatregel erbij (één actuele ClinVar, drift op CoGA's release), en H1
  ook (de filters, de uitsluiting en de P/LP-override lezen CoGA's release). Twee nieuwe gevaren:
  - *ClinVar-record aan het verkeerde allel gekoppeld.* Maatregelen: alleen het exacte allel in
    minimale vorm, de concordantie van §6.6 bij elke import.
  - *Een defecte release wordt zonder menselijke tussenkomst de huidige.* Maatregelen: de
    controles van §5.3 (MD5, release, assembly, aantal records), de vorige release blijft dan de
    huidige, een mail aan de beheerders, de release op elk scherm en in elke snapshot.
- **TF-09a:** nieuwe eisen voor het laden met integriteitscontrole, de wekelijkse vrijgave, het
  tonen, de filters en de override, en het bevriezen in snapshot en manifest; klasse C. In fase 2
  veranderen REQ-NIPT-011 en REQ-NIPT-020: ze lezen CoGA's release (B11, B13).
- **TF-09 en TF-09c:** de concordantie als verificatie, en een synthetisch ClinVar-bestand in
  de end-to-end-fixture.
- **TF-10 en TF-11 (fase 2):** de diagnostische NIPT-kandidaten opnieuw valideren, omdat ze
  afwijken van de vastgezette R-criteria (B11).
- **TF-18:** een wijziging van niveau **major**: nieuwe klinische evidentie op het scherm, een
  andere uitkomst van de filters met de override, en een nieuwe referentiebron.

### 8.3 Documentatie en tests

- `database.md` (de nieuwe tabellen en `entries.allele_keys`), `data-import.md` (ClinVar
  laden), `annotation-provenance.md` (een derde ClinVar-bron), `clinical-traceability.md`
  (snapshot en drift), en in fase 2 `acmg-classification.md`, `variant-ranking-cache.md` en
  `monogenic-nipt.md`.
- De handleiding, in het Nederlands en het Engels: de variantkaart, het filter en de
  beheerpagina.
- Tests met een synthetisch ClinVar-bestand: laden (MD5, SHA-256, release, telling, een
  mislukte job laat niets achter), minimale vorm en allel-sleutel (multi-allelisch, `MT`/`M`,
  ankerbase), filter en override, snapshot, drift, ondertekend record, en een integratietest
  met echte ClickHouse.

### 8.4 Middelen

- 192 MB download per assembly per release; de opslag per release in ClickHouse wordt bij de
  eerste lading gemeten.
- `entries.allele_keys` kost minstens 8 bytes per allel per rij, ongecomprimeerd; de
  schaalbenchmark ([clickhouse-scale-benchmark.md](clickhouse-scale-benchmark.md)) meet wat het
  bij 1.000 genomen kost.
- De VM haalt al bestanden bij NCBI voor de klinische CNV-kennisbank; er is geen nieuwe
  uitgaande verbinding nodig.

## 9. Beslissingen van de eigenaar (2026-11-27)

| | Vraag | Beslissing |
| --- | --- | --- |
| B1 | Wat doet fase 1? | Tonen naast de annotatie, het nieuwe filter, snapshot en drift, en de P/LP-override leest ook CoGA's release |
| B2 | Welke bron? | De wekelijkse VCF van NCBI |
| B3 | Hoe komt een nieuwere release binnen? | De nieuwste bij de eerste start, daarna elke week automatisch (§5.3) |
| B3b | Wordt een automatisch geladen release meteen de huidige? | Ja, als de controles van §5.3 slagen; anders blijft de vorige de huidige en krijgen de beheerders een mail |
| B4 | Welke releases blijven bewaard? | De huidige en de vorige |
| B5 | Welke drift door een nieuwe CoGA-release houdt de sign-out tegen? | Alleen een andere classificatie; een andere review-status of andere sterren wordt getoond |
| B6 | Controleert elke import de concordantie (§6.6)? | Ja, als de release van de pipeline in CoGA staat; anders *niet vergeleken* |
| B7 | Hoe heten de twee waarden op het scherm? | *ClinVar (annotatie, datum)* en *ClinVar (CoGA, datum)*. Vervangen door B13 |
| B8 | Fase 2: wat lezen de filters `clinvar` en `exclude_clinvar` en de ranking? | CoGA's release waar het allel erin staat, anders de annotatie. De terugval op de annotatie is vervangen door B13 |
| B9 | Fase 2: wat lezen PP5/BP6 en de bescherming van de NIPT-artefactlijst? | CoGA's release waar het allel erin staat, anders de annotatie. De terugval op de annotatie is vervangen door B13 |
| B10 | Fase 3: komt er een heranalysesignaal? | Een lijst per project, alleen lezen, opgevolgd volgens de labo-procedure; geen mail |
| B11 | Fase 2: wat lezen de NIPT-triage en de diagnostische NIPT-kandidaten? | CoGA's release waar het allel erin staat, anders de annotatie, met een nieuwe validatie (§8.1). De terugval op de annotatie is vervangen door B13 |
| B12 | Fase 2: wat lezen de mtDNA-criteria (`evaluateMito.ts`), die MITOMAP en ClinVar uit de mutserve-annotatie lezen? | Voor ClinVar: CoGA's release waar het allel erin staat (chrM staat in ClinVar's VCF als `MT`, §7), anders de annotatie; MITOMAP ongewijzigd. De terugval op de annotatie is vervangen door B13 |
| B13 | Welke ClinVar toont en gebruikt CoGA voor een small variant? (2026-11-27, vervangt B7 en de terugval op de annotatie van B8, B9, B11 en B12) | Eén ClinVar: de classificatie in de ClinVar-release die CoGA zelf laadt. De ClinVar van de annotatie van een callset (het ClinVar-bestand van de pipeline of VEP's cache) wordt nergens getoond of gebruikt; de import vergelijkt ze alleen met CoGA's kopie van dezelfde release (§6.6). Een allel dat de release niet bevat, heeft geen ClinVar. Op de kaart één waarde, *ClinVar*, zonder datum. De uitgesloten statussen laten een variant alleen weg als elk allel er een heeft. Structurele varianten en CNV's houden hun eigen bronnen |

## 10. Vragen voor QA

QA heeft het ontwerp nog niet beoordeeld. Deze punten vragen het oordeel van QA. Het antwoord op
V1 kan B3 nog doen verschuiven.

| | Vraag | Waarom |
| --- | --- | --- |
| V1 | Mag een wekelijkse ClinVar-release de huidige worden binnen een procedure die één keer gevalideerd is (de controles van §5.3 en de concordantie van §6.6), zonder dat elke release een major-wijziging is? | [TF-18](regulatory/TF-18-change-configuration-management.md) §4 noemt een nieuwe referentiedata-release een typische major, met klinische opvolgvalidatie. Met B3 gebeurt dat elke week. Een ja vraagt een aanpassing van TF-18; een nee maakt B3 onbruikbaar in klinisch gebruik. |
| V2 | Hoe scoren de twee nieuwe gevaren van §8.2, en volstaan hun maatregelen? Volstaan de gewijzigde maatregelen bij H1 en H8? | [TF-06](regulatory/TF-06-risk-management-plan.md) |
| V3 | Is "100 % of elk verschil verklaard" het aanvaardingscriterium voor de concordantie (§6.6), en met welke validatiefamilies en welke release wordt het getoetst? | [TF-09](regulatory/TF-09-verification-validation.md) |
| V4 | Wat zijn de scope en de criteria van de opvolgvalidatie: technisch en klinisch voor het geheel, en apart de hervalidatie van de NIPT-kandidaten tegen R NIPT-M v0.7.3 (B11) en van de mtDNA-criteria (B12)? | De bouw is een major-wijziging; [TF-10](regulatory/TF-10-performance-evaluation-plan.md) en [TF-11](regulatory/TF-11-performance-evaluation-report.md) |
| V5 | Keurt QA de nieuwe eisen en de gewijzigde REQ-NIPT-011 en REQ-NIPT-020 goed, met klasse C? | [TF-09a](regulatory/TF-09a-software-requirements-specification.md) en [TF-09b](regulatory/TF-09b-requirements-traceability-matrix.md) |
| V6 | Keurt QA het ontwerp goed? | Zoals het ontwerp van de review van repeat expansions en Paraphase ([repeat-paraphase-review-design.md](repeat-paraphase-review-design.md)) |
