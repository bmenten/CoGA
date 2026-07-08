# 11. Rapport & volledige traceerbaarheid

In dit hoofdstuk komt alles samen. Beschreven wordt hoe een casus in CoGA wordt afgetekend ("sign-out") tot een **bevroren, geversioneerd en gehasht rapport-snapshot**, dat onlosmakelijk verbonden is met de software-, annotatie- en referentieversies die het produceerden. Aan bod komen de **gates** (drempels) die vervuld moeten zijn voordat aftekenen mag — de classificatie-drift-check en de Sample-QC-erkenning — en de manier waarop elke stap wordt vastgelegd in een **append-only, hash-geketende** audit- en sign-out-trail met externe **integriteitsankers**. De rode draad is de *volledige traceerbaarheidsketen*: van het ruwe importbestand met zijn hash, via het annotatie-manifest en de variant in ClickHouse, tot het ondertekende rapport en zijn extern verifieerbare anker. Dit is de kern van de explainability- en medico-legale boodschap voor het review board.

Een paar begrippen die telkens terugkomen:

- **Snapshot** — een bevroren momentopname: een JSON-object (JSON = een tekstformaat om gestructureerde data in op te slaan) dat exact vastlegt wat op dat moment gold, zodat het later herleidbaar en herproduceerbaar blijft.
- **Hash** — een korte, vaste "vingerafdruk" (SHA-256) van een stuk data. Wijzigt de data ook maar één teken, dan wijzigt de hash volledig. Zo wordt manipulatie *detecteerbaar*.
- **Append-only** — alleen toevoegen; regels kunnen nooit worden gewijzigd of verwijderd. Afgedwongen op databankniveau met een *trigger* (een stukje databankcode dat vóór elke wijziging automatisch uitgevoerd wordt en de wijziging kan blokkeren).

## Het rapport zelf: opbouw en weergave

Het klinische rapport wordt in de frontend opgebouwd. Er zijn twee rapportpagina's:

- **`frontend/src/pages/families/FamilyReportPage.tsx`** — het standaard familierapport (kiembaan-/small variants + structurele varianten). Deze pagina haalt de varianten op die getagd zijn met de review-tag **`report`** (`GET /families/{id}/small-variants?review_tag=report` en de structurele tegenhanger `GET /families/{id}/structural-variants?review_tag=report`), plus per gerapporteerd gen het genprofiel (`GET /genes/profile`) en de HPO-annotaties (fenotype-termen) van de familie (`GET /families/{id}/hpo`). De prozahelpers (variant-zin, segregatie-zin, ACMG-criteria) staan apart in `frontend/src/pages/families/reportNarrative.ts` — o.a. `buildVariantSentence`, `buildSegregationSentence` en `collectReportCriteria` — en worden los getest, zodat de formulering veilig kan evolueren.
- **`frontend/src/pages/families/FamilyNiptReportPage.tsx`** — het aparte monogene-NIPT-rapport (prenataal, uit celvrij DNA in het bloed van de moeder). Het toont de geschatte foetale fractie, de on-target coverage-QC (kwaliteitscontrole van de sequencing-dekking) en de kandidaatvarianten. Dit rapport is een aparte weergave omdat NIPT een andere klinische logica volgt (zie hoofdstuk [08-filterpaginas-en-api.md](08-filterpaginas-en-api.md)).

De opbouw van elke variantsectie (variantbeschrijving, classificatiemotivatie met de aanvaarde ACMG/AMP-criteria, gencontext, fenotype/HPO-overlap, analistnotitie) is beschreven in de rapporttemplate-documentatie `docs/report-template.md` (zie de sectie "What each variant section contains"). Exporteren gebeurt via de browser-printdialoog (**Print report**); print-stijlen verbergen de UI-chrome en houden variantkaarten bijeen, zodat er een schone PDF ontstaat.

> **Belangrijk (decision support).** Het rapport is samengesteld uit data die al in CoGA zit; het is *beslissingsondersteuning* en moet door een gekwalificeerd klinisch wetenschapper worden bevestigd vóór klinisch gebruik. Die disclaimer staat letterlijk in beide pagina's (CSS-klasse `report-disclaimer`): "ACMG/AMP classifications are decision support and must be confirmed by a qualified clinical scientist before clinical use."

**Waar in de code:** `frontend/src/pages/families/FamilyReportPage.tsx` (component `FamilyReportPage`) en `FamilyNiptReportPage.tsx` (`FamilyNiptReportPage`); prozahelpers in `reportNarrative.ts`; documentatie in `docs/report-template.md`.

### Drie dingen die met het rapport meereizen

Naast de varianten toont `FamilyReportPage` drie provenance-elementen (provenance = herkomst/oorsprong), elk gevoed door een eigen endpoint uit `backend/app/routers/families_reports.py`:

| Element | Wat het toont | Endpoint / service |
| --- | --- | --- |
| **Provenance-footer** | Generatietijdstip + de annotatie/referentie-**moduleversies** (assembly, VEP, ClinVar, gnomAD, dbNSFP, SpliceAI, GENCODE, Monarch, HPO …), inclusief per-modaliteit-divergentie (bv. `GENCODE 49 (snv), 45 (sv)`) | `GET /{id}/annotation-manifest` → `get_family_annotation_manifest` |
| **Evidence-drift-banner** | Waarschuwing: classificaties waarvan de onderliggende annotatie wijzigde sinds ze werden gemaakt | `GET /{id}/classification-drift` → `evaluate_classification_drift` |
| **Klinische audittrail** | Onveranderlijke lijst van wie wat classificeerde/tagde, wanneer, en wat er wijzigde (before → after) | `GET /{id}/clinical-audit` → `list_clinical_audit` |

Deze drie zijn tegelijk de *ingrediënten* die bij aftekenen worden bevroren (zie verder).

## De sign-out flow: hoe een casus wordt afgetekend

Aftekenen gebeurt via **`POST /families/{id}/report/sign-out`** (endpoint `sign_out_family_report_endpoint`), dat delegeert aan **`sign_out_report`** in `backend/app/services/report_signout_service.py`. De kern van die functie:

1. **Snapshot samenstellen** — `build_report_snapshot` verzamelt: de familiecontext, het annotatie-manifest, de drift-evaluatie, de Sample-integrity-QC, en de gerapporteerde reviews (`_reported_reviews`: alle rijen uit `small_variant_reviews` met de tag `report`, elk met hun ACMG-klasse, criteria, notitie en het bevroren `acmg_evidence_snapshot`).
2. **Gate 1 — drift** (zie hieronder).
3. **Gate 2 — Sample-QC** (zie hieronder).
4. **Versie + hash-keten bepalen onder een per-familie advisory lock** (`pg_advisory_xact_lock`) — een tijdelijk slot in de databank per familie, zodat versiekeuze, ketenkop-lezing en insert atomair (als één ondeelbaar geheel) gebeuren; gelijktijdige aftekeningen kunnen elkaar niet in de wielen rijden.
5. **Content-hash + row-hash berekenen** en de nieuwe rij **append-only inserten** in `report_signouts` als de volgende `version`.
6. **Klinisch audit-event schrijven** (`record_clinical_event`, `action="sign_out"`) — in dezelfde transactie — en dan committen.

**Wie mag tekenen.** Aftekenen is een geauthenticeerde actie: elk endpoint hangt af van `get_current_user` en alle databanktoegang loopt via `build_family_metadata_context`, dat de casus *scopet* (beperkt) op het project van de gebruiker (projectgebonden toegangscontrole — zie hoofdstuk [02-beveiliging-rollen-rechten.md](02-beveiliging-rollen-rechten.md)). De identiteit van de ondertekenaar wordt zowel als foreign key (`signed_out_by_id`, een verwijzing naar het gebruikersaccount) als *gedenormaliseerd* vastgelegd (`signed_out_by`, uit username/email), zodat de ondertekenaar herkenbaar blijft zelfs na accountverwijdering.

**Amendementen.** Elke sign-out is een nieuwe `version` (`_next_version` = `MAX(version)+1`); een bestaande ondertekende versie wordt nooit gemuteerd. In de UI heet de knop dan ook "Amend sign-out" zodra er al een sign-out bestaat.

**Waar in de code:** `backend/app/services/report_signout_service.py`, functie `sign_out_report`; endpoint in `backend/app/routers/families_reports.py`.

### Gate 1 — classificatie-drift moet groen of erkend zijn

Drift betekent: de *evidence* (het bewijs) achter een ACMG-classificatie is veranderd sinds die classificatie werd gemaakt — bijvoorbeeld een nieuwe ClinVar- of gnomAD-release. `evaluate_classification_drift` (in `classification_drift_service.py`) vergelijkt per gerapporteerde classificatie de **bevroren** `acmg_evidence_snapshot` met de *huidige* annotatie in ClickHouse. De autoritatieve driftsleutel is de **annotation-set-hash**: verschilt die van de bevroren waarde, dan is er drift (`status = "drifted"`). Belangrijke fail-safes (in de functie `_diff`):

- Ontbreekt een van beide hashes, dan is de binding *niet* verifieerbaar-ongewijzigd → status **`unknown`**, die door de gate als drift telt (in plaats van stilzwijgend "current"). Zonder deze fail-safe zou een classificatie zonder hash ongehinderd door de gate glippen.
- Een gerapporteerde classificatie **zonder** bevroren snapshot kan niet drift-geverifieerd worden; `build_report_snapshot` markeert die apart als **`no_snapshot`** zodat de gate ook die dwingt te erkennen (#332).

In `sign_out_report` geldt: als `drifted_count > 0` en de aanroeper heeft `acknowledge_drift` niet gezet → **HTTP 409** (statuscode voor "conflict"). Pas met expliciete erkenning gaat aftekenen door; die erkenning (`acknowledged_drift: true`) wordt in het snapshot en dus in de content-hash gebakken. De classificatie-evidence en drift-logica zelf horen bij hoofdstuk [10-tagging-en-acmg-classificatie.md](10-tagging-en-acmg-classificatie.md).

**Waar in de code:** `report_signout_service.sign_out_report` (de 409-check op `drifted_count`); `classification_drift_service.evaluate_classification_drift` en `_diff`.

### Gate 2 — Sample-QC-erkenning moet groen of erkend zijn

De Sample-integrity-QC detecteert sample- of pedigree-verwisselingen (een verwisseld staal of een fout in de stamboom — TF-06 gevaar H4, geclassificeerd als catastrofaal, S5). `get_family_sample_integrity_qc` levert de checks (geslacht, verwantschap, Mendeliaanse consistentie, NIPT-paterniteit/categorie); de filterpagina-kant hoort bij hoofdstuk [08-filterpaginas-en-api.md](08-filterpaginas-en-api.md). Bij aftekenen blokkeert deze gate in twee gevallen:

1. **Harde fail** — een *gedetecteerde* mismatch (`overall_status == "fail"`, in `_QC_BLOCKING_STATUSES`).
2. **Onverifieerbare swap-check** — een verwisseling die zich als *ontbrekende data* manifesteert (een sample afwezig in de callset, te weinig informatieve sites) laat de relevante check niet uitkomen op "fail". De helper `_unverifiable_swap_checks` vlagt zulke *geasserteerde* pedigree-relaties (ouder-kind, sibling, NIPT-lijn, en het geslacht van een sample zonder verwantschapsanker) apart (#330), zodat ze niet stilzwijgend afgetekend kunnen worden.

Blokkeert de gate en is `acknowledge_qc` niet gezet → **HTTP 409** met een gestructureerde `detail` (`gate: "sample_qc"`, een boodschap `_qc_gate_message`, een `qc_summary` en de lijst `unverifiable_checks`). Erkennen vereist bovendien een **niet-lege reden** (`qc_acknowledgement_reason`), anders **HTTP 422** ("onverwerkbare invoer"). Die reden wordt — net als het QC-verdict zelf — in de content-hash bevroren en in het audit-event opgenomen. In de UI opent hiervoor een aparte modal (`qcGate` in `FamilyReportPage.tsx`) die de gebruiker dwingt een reden te typen.

**Waar in de code:** `report_signout_service.sign_out_report` (het `qc_blocks`-blok, 409/422); helpers `_unverifiable_swap_checks`, `_qc_gate_message`, `_qc_failure_summary`; frontend-modal in `FamilyReportPage.tsx` (`submitQcAcknowledgement`).

## Het bevroren snapshot en waaraan het gebonden is

`build_report_snapshot` bouwt het object dat wordt bevroren. Het bevat:

| Veld | Inhoud |
| --- | --- |
| `family_id`, `assembly` | familie-identifier en referentie-assembly (het gebruikte referentiegenoom) |
| `modules` | het volledige annotatie/referentie-manifest (per-modaliteit) |
| `software` | `{version: settings.app_version, git_sha: settings.git_sha}` — de **exacte softwarebuild** die het snapshot maakte |
| `drift` | `checked`, `drifted_count` en de gesorteerde `drifted`-lijst (incl. `no_snapshot`-gevallen) |
| `sample_qc` | de volledige, deterministische Sample-integrity-QC (`_canonical_sample_qc`) |
| `reported_variants` | elke gerapporteerde variant met ACMG-klasse, criteria, tags, notitie én zijn bevroren `evidence_snapshot` |

Bij het feitelijke aftekenen worden hier nog `version`, `generated_at`, `signed_out_by` en de erkennings-vlaggen (`acknowledged_drift`, `acknowledged_qc`, `qc_acknowledgement_reason`) aan toegevoegd. Zo is het snapshot gebonden aan **drie versie-assen tegelijk**:

- **Software** — via `software.version` + `git_sha` (build-time constanten, dus deterministisch, altijd hetzelfde voor dezelfde build).
- **Annotatie/pipeline** — via het bevroren `modules`-manifest én, per classificatie, de annotation-set-hash in het evidence-snapshot.
- **Referentie** — via de assembly en de platform-referentielaag in het manifest (assembly-release, Monarch-release, gelezen uit de referentietabellen).

**Waarom herproduceerbaar.** De content-hash wordt berekend met `canonical_hash` (een SHA-256 over een *canonieke*, op sleutel gesorteerde JSON-codering, `hash_chain.canonical_json`). Alle bevroren waarden zijn zuivere functies van deterministisch-geordende input (geen tijdstempels-in-de-inhoud, geen toevalsgetallen), dus identieke klinische inhoud levert altijd dezelfde hash. Lijsten worden expliciet gesorteerd op stabiele sleutels (bv. `drifted` op `variant_id`) omdat `sort_keys` in de JSON-codering alleen dict-sleutels (veldnamen) ordent, geen lijstvolgorde.

**Ontkoppeling van ClickHouse.** Het snapshot *embed* (bevat een ingebedde kopie van) de variant- en evidence-waarden die het nodig heeft. Een latere ClickHouse-herbouw of `annotation_version`-wissel kan een ondertekend rapport dus niet veranderen; bij het tonen van een ondertekend rapport wordt ClickHouse niet opnieuw bevraagd.

**Waar in de code:** `report_signout_service.build_report_snapshot`; canonicalisatie in `backend/app/services/hash_chain.py` (`canonical_json`, `canonical_hash`). Schema van de tabel: `backend/db/schema/postgres/033_report_signouts.sql` (kolommen `version`, `content_hash`, `snapshot`, `UNIQUE(family_id, version)`); evidence-snapshot: `031_classification_evidence_snapshot.sql`.

## Append-only, hash-geketende audit- en sign-out-trail

CoGA houdt **twee** verschillende auditlogs bij, met verschillende doelen:

- **HTTP-toegangslog** — `audit_log_events` (`004_audit_logs.sql`), geschreven door `backend/app/services/audit_log_pg.py`: elke HTTP-request (methode, pad, statuscode, gebruiker). Infrastructureel, afgeleid uit method+path+body.
- **Klinische actielog** — `clinical_audit_events` (`032_clinical_audit_events.sql`), geschreven door `backend/app/services/clinical_audit_service.py`: semantische klinische acties (classificatie, tag toegevoegd/verwijderd, notitie bewerkt, rapport afgetekend), mét veld-niveau `before`/`after`. Deze wordt geschreven **in dezelfde transactie** als de wijziging zelf (`record_review_changes`, `diff_review_changes`), zodat de trail nooit uit de pas loopt met de data.

Beide zijn op databankniveau **append-only**: een trigger blokkeert `DELETE` volledig en `UPDATE` behalve de ene toegestane uitzondering — het `ON DELETE SET NULL`-cascaden dat de foreign keys `user_id`/`actor_id`/`family_id` op NULL zet als een account of familie verwijderd wordt (de gedenormaliseerde velden bewaren dan nog de identiteit). De vergelijking gebeurt kolom-agnostisch (`to_jsonb(NEW) - 'actor_id' - 'family_id'` vergeleken met dezelfde uitdrukking op de oude rij), zodat nieuw toegevoegde kolommen automatisch beschermd blijven.

**Waar in de code:** triggers in `029_audit_log_immutable.sql`, `032_clinical_audit_events.sql`, `033_report_signouts.sql` (en `041_integrity_anchors.sql`, zie verder).

### Wat een hash-keten is — en waarom ze manipulatie zichtbaar maakt

Append-only-triggers *verhinderen* dat de normale applicatiepaden een regel wijzigen. Maar een geprivilegieerde databankgebruiker die de trigger kan uitschakelen, zou dat kunnen omzeilen. Daartegen legt CoGA een **hash-keten** over de twee klinische tabellen.

Stel u de regels voor als schakels in een ketting. Elke regel krijgt een `row_hash`: een SHA-256 over de *onveranderlijke inhoud van die regel* **plus de `row_hash` van de vorige regel** (`chain_row_hash(prev_hash, payload)` = `SHA-256(prev_hash ‖ "\n" ‖ canonical(payload))`; de allereerste regel gebruikt de tekst `GENESIS`). Gevolg:

- Wijzig je één regel, dan klopt zijn `row_hash` niet meer → detecteerbaar.
- Verwijder of herschik je een regel, dan wijst de `prev_hash` van de volgende regel niet meer naar de juiste voorganger → detecteerbaar.

`verify_chain` (in `hash_chain.py`) herloopt de keten en herberekent elke schakel; `verify_clinical_audit_chain` en `verify_report_signout_chain` doen dat per familie. Voor de sign-out-keten wordt daarnaast bij *elke leesactie* de `content_hash` opnieuw tegen het snapshot berekend (`get_report_signout` zet een `verified`-vlag en logt bij mismatch een `ERROR` met de tekst "possible tampering").

Twee ontwerpkeuzes zijn cruciaal voor robuustheid:

- De keten is **gepartitioneerd op de onveranderlijke `family_identifier`** (de menselijke familie-id), niet op de muteerbare `family_id` (de UUID die het cascaden op NULL zet). Zo blijft de getekende historie van een verwijderde familie verifieerbaar.
- De gehashte payload **sluit de FK-kolommen uit** die het cascaden mag nullen, en bindt in plaats daarvan de gedenormaliseerde identiteit. Een legitieme account-/familieverwijdering breekt de keten dus niet.

**Eerlijke reikwijdte (belangrijk voor het review board).** De hash-keten is **tamper-EVIDENT, niet tamper-proof** (manipulatie wordt *zichtbaar*, maar niet *onmogelijk* gemaakt). Ze detecteert manipulatie door iedereen die de keten *niet kan herberekenen*. Maar de tabel-*eigenaar* (tot de niet-eigenaar-runtime-rol `coga_app` volledig is uitgerold, is dat de app-DB-rol zelf — zie `040_app_runtime_role_privileges.sql`) kan de trigger uitschakelen, een interne regel bewerken en vervolgens `row_hash`/`prev_hash` voor die regel én alle opvolgers herrekenen tot een zelf-consistente keten. Dat sluit het volgende blok — de externe ankers — af. Deze reikwijdte staat expliciet in de docstrings van `hash_chain.py` en `integrity_anchor_service.py` en moet in regulator-taal zo geformuleerd blijven ("tamper-evident tegen een database-only tegenstander, tussen bewaarde ankers"), nooit "tamper-proof" of "immutable".

**Waar in de code:** `backend/app/services/hash_chain.py` (`chain_row_hash`, `verify_chain`, `ChainVerification`); ketenschrijving in `clinical_audit_service.record_clinical_event` en `report_signout_service.sign_out_report`; hash-kolommen toegevoegd in `038_clinical_audit_hash_chain.sql` en `039_report_signouts_hash_chain.sql`.

## Integriteitsankers: de keten extern verifieerbaar maken

Om ook de "eigenaar die de trigger uitschakelt en herketent" detecteerbaar te maken, bestaat er een **extern getekend anker**. `backend/app/services/integrity_anchor_service.py` (`create_integrity_anchor`) doet periodiek het volgende:

1. **Alle keten-koppen vastleggen** (`_capture_heads`): voor elke familie, voor beide tabellen (`report_signouts` en `clinical_audit_events`), de hoogte (`height`) en de `head_row_hash`.
2. Deze koppen **canoniek hashen** tot een `anchor_root`, ze aan het vorige anker ketenen (`prev_anchor_hash` → `anchor_hash`), en het geheel **ondertekenen met een Ed25519-privésleutel** (een moderne digitale-handtekening­techniek) die in app-config/omgeving zit — **nooit in de databank**.
3. Het anker **append-only** wegschrijven in `integrity_anchors` (`041_integrity_anchors.sql`).

Waarom dit werkt: een eigenaar zonder de privésleutel kan een geketende regel wel *herrekenen*, maar kan **geen geldig getekend anker vervalsen**. De divergentie tussen de live keten en het laatst *getekende* anker wordt dan zichtbaar voor een verifier die de databank niet vertrouwt. `verify_against_latest_anchor` (koppen van het laatste anker vs. de live ketens) en `verify_anchor_chain` (de volledige ankerketen + alle handtekeningen) geven statussen als `ok`, `diverged`, `chain_broken`, `signature_invalid`, `unknown_key` en `unverifiable_unsigned`.

De docstring van de service formuleert de trustgrens eerlijk: de aanpak detecteert interne herketening/inkorting tussen bewaarde ankers, maar verdedigt **niet** tegen een tegenstander die de tekensleutel bezit (host-compromittering — daarvoor is een HSM, een hardware-sleutelmodule, nodig), en detecteert het wissen van de *laatste* ankers alleen als er een out-of-band bewaarde kopie is. Die out-of-band export is bewust een nog niet-gekoppelde naad (`export_anchor`, momenteel een no-op — een functie die nog niets doet). De tabel is bovendien strikt vergrendeld voor de runtime-rol: `GRANT SELECT, INSERT` gevolgd door `REVOKE UPDATE, DELETE, TRUNCATE` (regels 52-53 van `041`, in lijn met de runtime-rol uit `040`).

### Integriteit van de variantopslag (ClickHouse)

De ankers dekken de Postgres-ketens. De grootschalige variantopslag in ClickHouse wordt bewaakt door **`backend/app/services/clickhouse_integrity_monitor.py`**: kort na opstart en daarna op een vast interval draait `run_integrity_sweep` de controle `check_clickhouse_variant_integrity` over elke assembly. Bij status `corrupt` of `missing` (in `_ALERT_STATUSES`) escaleert het naar een `ERROR`-log — de alert-haak — *vóór* de corruptie zich als query-500's manifesteert. Het laatste resultaat per assembly wordt gecached voor admin/health-surfacing (`last_integrity_results`).

**Waar in de code:** `backend/app/services/integrity_anchor_service.py` (`create_integrity_anchor`, `verify_against_latest_anchor`, `verify_anchor_chain`); schema `041_integrity_anchors.sql`; `backend/app/services/clickhouse_integrity_monitor.py` (`run_integrity_sweep`).

## De volledige traceerbaarheidsketen, stap voor stap

Dit is het hart van de explainability-boodschap: elk gerapporteerd resultaat is herleidbaar tot exact wat het produceerde. De keten, met per stap de plaats in de code:

1. **Ruw bestand → hash.** Bij import wordt elk bronbestand vastgelegd in `raw_import_files` met `file_name`, `storage_path`, `file_size`, `source` en een **`sha256`**-integriteitshash. *(`backend/db/schema/postgres/017_raw_import_files.sql`; importpijplijn — hoofdstuk [06-import-pipeline.md](06-import-pipeline.md).)*
2. **Annotatie-manifest.** De `##`-headers van de VCF-bestanden worden geparsed en per familie opgeslagen in `family_annotation_manifest` (bron `vcf_header`, `manifest` of `manual`); versies verversen bij re-import, maar een handmatig gecureerde (`manual`) manifest wint en wordt nooit overschreven. *(`backend/app/services/annotation_manifest_service.py` — `merge_vcf_header_provenance`, `get_family_annotation_manifest`; schema `030`; zie `docs/annotation-provenance.md`.)*
3. **Variant in ClickHouse.** Elke variant draagt een `annotationSetHash` — een inhoudsvingerafdruk die vastpint welke annotatie-set gold. *(zie hoofdstuk [03-databankstructuren.md](03-databankstructuren.md).)*
4. **Review / ACMG-evidence-snapshot.** Bij elke ACMG-save wordt de geziene evidence (annotation-set-hash, ClinVar-significantie, moduleversies) bevroren in `small_variant_reviews.acmg_evidence_snapshot`. *(`031_classification_evidence_snapshot.sql`; hoofdstuk [10-tagging-en-acmg-classificatie.md](10-tagging-en-acmg-classificatie.md).)*
5. **Gating.** Voor sign-out worden **drift** (bevroren vs. huidige annotation-set-hash) en **Sample-QC** (swap-detectie) geëvalueerd; niet-groen moet expliciet worden erkend, bij QC met een verplichte reden. *(`report_signout_service.sign_out_report`; `classification_drift_service`; `sample_integrity_service`.)*
6. **Bevroren rapport.** Het snapshot — manifest, software-`version`+`git_sha`, driftstatus, Sample-QC, gerapporteerde varianten met evidence — wordt content-gehasht en append-only weggeschreven als nieuwe `version`. *(`report_signout_service.build_report_snapshot` + insert in `report_signouts`; schema `033`.)*
7. **Hash-geketende sign-out + anker.** De sign-out-regel wordt in de per-familie hash-keten gehangen (`row_hash`/`prev_hash`), er wordt een `sign_out`-event in de klinische audittrail geschreven, en periodiek verzegelt een Ed25519-getekend integriteitsanker de keten-koppen extern. *(`hash_chain`, `clinical_audit_service.record_clinical_event`, `integrity_anchor_service`; schema's `038`/`039`/`041`.)*

Zo loopt een auditor van de SHA-256 van het ruwe bestand, via de versies die het annoteerden en de exacte evidence achter elke classificatie, tot een ondertekend rapport waarvan de integriteit extern verifieerbaar is.

## Veiligheid & traceerbaarheid — waar het wordt afgedwongen

| Waarborg | Afgedwongen in |
| --- | --- |
| Toegangscontrole / projectscoping op alle rapportendpoints | `families_reports.py` (`get_current_user`) + `build_family_metadata_context` |
| Gate op classificatie-drift vóór sign-out (409 tenzij erkend) | `report_signout_service.sign_out_report` |
| Gate op Sample-QC vóór sign-out (409, en 422 zonder reden) | `report_signout_service.sign_out_report`, `_unverifiable_swap_checks` |
| Onveranderlijkheid van audit- en sign-out-regels (append-only) | DB-triggers in `029`, `032`, `033`, `041` |
| Manipulatiedetectie binnen een keten (per familie) | `hash_chain.py`, ketenkolommen `038`/`039` |
| Externe, sleutelgebaseerde verzegeling van keten-koppen | `integrity_anchor_service.py`, schema `041` |
| Runtime-rol zonder UPDATE/DELETE op de append-only tabellen | `040_app_runtime_role_privileges.sql`, `041` |
| Integriteitsbewaking variantopslag (ClickHouse) | `clickhouse_integrity_monitor.py` |
| Herverificatie content-hash bij elke leesactie van een sign-out | `report_signout_service.get_report_signout` (`verified`) |

## IVDR-koppeling

Deze hele keten is de technische invulling van de traceerbaarheidseis onder IVDR (in-house IVD, Artikel 5(5); device-grens "geannoteerde VCF → ondertekend klinisch rapport"). Het ontwerpdossier en de fasering (Phase 0-3 + de P1-4-ankerlaag, alle vier de fasen geïmplementeerd) staan in **`docs/clinical-traceability.md`**; de annotatie-provenance in **`docs/annotation-provenance.md`**. Voor het regelgevende dossier verwijzen we naar de technical file in **`docs/regulatory/`**, met name **TF-06** (risicomanagement — het H4 sample-swap-gevaar dat de Sample-QC-gate adresseert) en **TF-09** (verificatie & validatie / traceerbaarheid). Houd bij het citeren de eerlijke trustgrens aan: **tamper-evident tegen een database-only tegenstander, tussen bewaarde ankers** — niet "tamper-proof".

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/families_reports.py` | Rapport-/traceerbaarheidsendpoints: manifest, drift, clinical-audit, sign-out, Sample-QC |
| `backend/app/services/report_signout_service.py` | Sign-out flow, snapshot-opbouw, drift- + QC-gates, content-hash + ketenschrijving |
| `backend/app/services/hash_chain.py` | Canonieke JSON/SHA-256, `chain_row_hash`, ketenverificatie |
| `backend/app/services/clinical_audit_service.py` | Append-only, hash-geketende klinische actielog (before/after) |
| `backend/app/services/integrity_anchor_service.py` | Ed25519-getekende externe ankers over de keten-koppen |
| `backend/app/services/classification_drift_service.py` | Drift-evaluatie (bevroren vs. huidige annotation-set-hash) |
| `backend/app/services/annotation_manifest_service.py` | Per-familie annotatie/referentie-manifest (provenance-footer) |
| `backend/app/services/clickhouse_integrity_monitor.py` | Geplande integriteitsbewaking van de variantopslag |
| `backend/app/services/audit_log_pg.py` | HTTP-toegangslog (`audit_log_events`) |
| `backend/db/schema/postgres/032_clinical_audit_events.sql` | Klinische audittabel + append-only trigger |
| `backend/db/schema/postgres/033_report_signouts.sql` | Sign-out-snapshottabel + append-only trigger |
| `backend/db/schema/postgres/038_…` / `039_…_hash_chain.sql` | Hash-keten-kolommen op beide tabellen |
| `backend/db/schema/postgres/040_app_runtime_role_privileges.sql` | Restrictieve runtime-rol (`coga_app`), REVOKE UPDATE/DELETE |
| `backend/db/schema/postgres/041_integrity_anchors.sql` | Ankertabel + trigger + runtime-rolrechten |
| `backend/db/schema/postgres/031_classification_evidence_snapshot.sql` | Bevroren ACMG-evidence per classificatie |
| `backend/db/schema/postgres/017_raw_import_files.sql` | Ruwe-bestandprovenance met SHA-256 |
| `frontend/src/pages/families/FamilyReportPage.tsx` | Familierapport, provenance-footer, drift-banner, sign-out + QC-modal |
| `frontend/src/pages/families/FamilyNiptReportPage.tsx` | Monogeen-NIPT-rapport |
| `frontend/src/pages/families/reportNarrative.ts` | Prozahelpers voor de rapportzinnen (los getest) |
| `docs/clinical-traceability.md` / `docs/report-template.md` / `docs/annotation-provenance.md` | Ontwerp- en templatedocumentatie |
