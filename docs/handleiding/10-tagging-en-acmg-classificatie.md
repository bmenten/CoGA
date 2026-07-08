# 10. Variant-tagging & semi-automatische ACMG-classificatie

Dit hoofdstuk beschrijft hoe een analist in CoGA varianten *labelt* (taggen) en *classificeert*, en hoe de tool daarbij assisteert met een **semi-automatische ACMG/AMP-classificator**. De kern is dat CoGA geen "auto-classifier" is: de software evalueert elk classificatiecriterium **vooraf** uit de beschikbare variant-, trio- en gendata, positioneert het op een puntenschaal, maar laat *elk* criterium overrijden door de reviewer. De uiteindelijke klasse en het puntentotaal worden altijd **op de server herberekend**, de gebruikte bewijsstukken worden **bevroren** (evidence-snapshot), en als het onderliggende bewijs later verandert wordt dat als *classification drift* gedetecteerd — wat de rapportvrijgave blokkeert (zie hoofdstuk 11). We behandelen zowel Small Variants (SNV/indel) als kopie-aantalvarianten (CNV), en zowel de frontend-evaluatielogica als de backend-scoring en -persistentie.

Enkele begrippen die vaak terugkomen:
- **ACMG/AMP-criteria**: een gestandaardiseerde set bewijsregels (Richards et al., 2015) met codes als `PVS1`, `PM2`, `BA1`. Een `P`-prefix duidt op pathogeen bewijs, een `B`-prefix op benigne bewijs.
- **Tag**: een label dat aan een variant hangt (bv. "Report", "Send for validation", "Pathogenic - class 5").
- **Snapshot** (momentopname): een bevroren kopie van de data zoals ze op een bepaald moment was.
- **JSONB**: een Postgres-kolomtype dat een volledig JSON-document opslaat en doorzoekbaar maakt.
- **Upsert**: één bewerking die een rij *invoegt als ze nog niet bestaat* en anders *bijwerkt*.

---

## Twee onafhankelijke assen: fenotype en dragerschap

Voordat we bij classificatie komen: CoGA houdt op *familielid*-niveau twee volledig **losstaande** eigenschappen bij, die door de review- en ACMG-logica apart worden gelezen:

- **Klinische status (fenotype)** — `clinical_status`: `unknown` / `unaffected` / `affected`. Dit zegt of het individu de aandoening *heeft*.
- **Dragerschap** — `carrier_status`: `unknown` / `not_carrier` / `carrier`. Dit zegt of het individu de variant/het risico-allel *draagt*, ongeacht of het ziek is.

Deze scheiding is klinisch essentieel: bij recessieve aandoeningen is een ouder vaak *drager maar niet aangedaan*. De ACMG-segregatielogica (PP1/BS4, zie verder) redeneert daarom over de combinatie van beide assen, niet over één samengevoegde "rol".

**Waar in de code:** het datamodel in `frontend/src/lib/apiTypes.ts` (`clinical_status` en `carrier_status` als aparte velden op een familielid). De ACMG-familiecontext in `AcmgClassificationModal.tsx` markeert een lid als "aangedaan" via `Boolean(member.affected) || member.clinical_status === 'affected'`.

---

## Deel 1 — Variant-tagging

### Wat wordt vastgelegd

Bij het reviewen van een Small Variant legt CoGA per variant, per familie, minstens drie dingen vast:

| Element | Betekenis |
| --- | --- |
| **classification** | De vrije-tekstlabel van de klasse (bv. "VUS - class 3"). |
| **tags** | Een lijst met tag-sleutels (bv. `review`, `report`, `acmg_class_4`). |
| **note** | Een vrije notitie / rationale van de reviewer. |

Voor elke tag wordt bovendien **tag-metadata** bijgehouden: *wie* de tag zette en *wanneer* (`updated_by` / `updated_at` per tag). Dat maakt tagging traceerbaar tot op de individuele tag.

**Waar in de code:** de opslaglaag is `upsert_small_variant_review` in `backend/app/services/small_variant_review_pg.py`. De per-tag-herkomst wordt geserialiseerd door `_serialize_tag_metadata` (in datzelfde bestand) en samengevoegd door de helper `_merge_tag_metadata` uit `backend/app/services/review_pg_utils.py`.

### Project-gescoopte tag-definities (schema 006)

Welke tags *bestaan* wordt bepaald door **tag-definities**. Er zijn twee soorten:

- **Systeemtags** (ingebouwd): vast in `DEFAULT_SMALL_VARIANT_TAGS` in `backend/app/services/small_variant_review_tags.py`. Deze omvatten collaboratie-tags (`review`, `send_for_validation`, `validated`, `report`, `excluded`, …) én de classificatietags (`acmg_class_1`..`acmg_class_5`, de VUS-subtiers `acmg_vus_hot` / `acmg_vus_warm` / `acmg_vus_cold`, en `secondary_finding`). Systeemtags kunnen **niet** worden bewerkt of verwijderd.
- **Custom tags**: door een admin aangemaakt, opgeslagen in de tabel `small_variant_tag_definitions`.

Custom tags hebben een **scope**: `global` (zichtbaar in alle projecten) of `project` (gebonden aan één project). Een project-tag kan bovendien met extra projecten *gedeeld* worden via de koppeltabel `small_variant_tag_definition_project_links`. Dit hele scope-mechanisme is toegevoegd in migratie 006.

**Waar in de code:** `backend/db/schema/postgres/006_project_scoped_variant_tags.sql` voegt de kolommen `scope` en `project_id` toe met CHECK-constraints (globaal ⇒ geen project; project ⇒ verplicht project) en maakt de koppeltabel aan. De zichtbaarheidslogica (welke tags mag deze reviewer in deze familie/projecten gebruiken) zit in `list_small_variant_tag_definitions` in `small_variant_review_tags.py`.

> **Traceerbaarheid & idempotentie:** schema 006 bevat een expliciete waarschuwing (commentaarblok vanaf regel 24) dat een eerdere *backfill*-`UPDATE` is verwijderd. Omdat de schema-initialisatie élk `.sql`-bestand bij *elke* opstart opnieuw uitvoert (er is geen migratie-ledger), zette die onvoorwaardelijke UPDATE bij elke herstart alle project-tags terug naar `global` — stille dataverlies. Les: schema-bestanden moeten idempotent zijn en mogen geen destructieve datamutaties bevatten. Een nieuwe database heeft géén backfill nodig, want de kolom `scope` krijgt de default `global`.

### Validatie bij het opslaan

Bij het opslaan van een review controleert de backend dat *elke* opgegeven tag ook echt bestaat in de toegestane set voor die familie/projecten. Onbekende tags leveren een `400`-fout. Deze query wordt lui uitgevoerd: alleen wanneer de payload daadwerkelijk tags bevat, wordt de (relatief dure) join naar de toegestane tags uitgevoerd — hij draait hooguit één keer per opslag.

**Waar in de code:** de geneste helper `_allowed_tags()` en de `unknown_tags`-check in `upsert_small_variant_review` (`small_variant_review_pg.py`); dezelfde controle staat in `upsert_structural_variant_review` voor structurele varianten.

### Toegangscontrole op tag-beheer

Custom tags aanmaken/bewerken/verwijderen mag **alleen een admin**. Bovendien controleert `_ensure_projects_visible` dat een niet-admin geen tag koppelt aan een project waartoe hij geen toegang heeft (via `metadata_project_ids`).

**Waar in de code:** `create_small_variant_tag_definition`, `update_small_variant_tag_definition`, `delete_small_variant_tag_definition` in `small_variant_review_tags.py` (elke functie begint met `if user.role != "admin": raise HTTPException(403, …)`). Verwijderen is een *soft delete* (`is_active = FALSE`), geen fysieke verwijdering — belangrijk voor traceerbaarheid.

### Filter-presets

Naast tags kan een analist zijn filterinstellingen bewaren als **preset**, met scope `family` (alleen deze familie) of `global` (alle families van deze eigenaar). Presets zijn eigenaar-gebonden: verwijderen mag alleen de eigenaar.

**Waar in de code:** `backend/app/services/small_variant_review_presets.py` (`save_small_variant_filter_preset`, `list_small_variant_filter_presets`, `delete_small_variant_filter_preset` — deze laatste bevat de `row["owner"] != user.username`-check die niet-eigenaars met een `403` weert).

### De repository-laag

De ruwe SQL (SELECT/INSERT/UPDATE/DELETE op `small_variant_reviews`) is afgezonderd in een repository-module, los van de businesslogica. Hier zit de gedeelde kolomlijst (`_SMALL_VARIANT_REVIEW_SELECT`) en de helperfuncties voor compound-het-groepen (twee varianten in *trans* op hetzelfde gen).

**Waar in de code:** `backend/app/services/small_variant_review_repository.py` (o.a. `_fetch_review_row`, `_insert_review_row`, `_update_review_row`, `_clear_compound_het_group`). De INSERT/UPDATE bevatten ook de ACMG-kolommen (`acmg`, `acmg_point_total`, `acmg_class`, `acmg_evidence_snapshot`) die we hieronder bespreken.

### De eenvoudige review-dialoog

Naast de volledige ACMG-classificator bestaat er een lichtere review-dialoog waarin de analist enkel een klasse aanvinkt, standaard-/custom-tags toggelt en een notitie schrijft — zonder de criteria door te lopen. Ook toont die dialoog een Exomiser-achtige prioriteitsscore (variantimpact + zeldzaamheid + segregatie + fenotype-match) als beslissingssteun.

**Waar in de code:** `frontend/src/pages/families/SmallVariantReviewDialog.tsx` (met de `PriorityBreakdown`-subcomponent).

---

## Deel 2 — De semi-automatische ACMG-classificator (SNV)

### Het idee: beslissingssteun, geen automaat

Vanuit een variantkaart of tabelrij opent **ACMG classify** een modal. Die:
1. **pre-evalueert** de ACMG-criteria uit data die CoGA al heeft;
2. positioneert elk criterium in één van vier toestanden;
3. laat de analist elk criterium bevestigen, aanpassen of overrijden;
4. scoort de geaccepteerde criteria op een groen→rood puntenschaal;
5. herberekent bij het opslaan alles **server-side**.

De canonieke documentatie van hoe elk criterium wordt gepositioneerd, staat in `docs/acmg-classification.md`; dat document benoemt zichzelf expliciet als *decision support, not an autoclassifier*.

### De vier criteriumtoestanden

| Toestand | UI | Betekenis | `disposition`-waarde |
| --- | --- | --- | --- |
| **Applied** | aangevinkt, groen | Data steunt het duidelijk; telt mee in de score. | `applies` |
| **Consider** | ● amber, niet aangevinkt | Relevant maar niet doorslaggevend signaal. | `consider` |
| **Argues against** | ✕ rood, niet aangevinkt | Data wijst de andere kant op (bv. in-silico benigne bij PP3). | `contraindicated` |
| **Not applicable** | grijs, doorstreept | Kan niet gelden voor dit varianttype; toch klikbaar. | `not_applicable` |

Alle vier zijn overrijdbaar: klikken op een criterium togglet het altijd.

**Waar in de code:** de toestanden zijn getypeerd als `AcmgDisposition` in `frontend/src/lib/acmg/types.ts`. De weergave zit in `CriterionRow` binnen `AcmgClassificationModal.tsx`.

### Welke criteria automatisch worden voorgeëvalueerd, en waaruit

De pure evaluatiefunctie `evaluateAcmg` leest een subset van de variantvelden plus optionele gen-, fenotype- en familiecontext, en produceert een lijst *suggesties*. Ze is bewust **side-effect-vrij**: ze wijzigt niets, ze stelt alleen voor.

**Waar in de code:** `frontend/src/lib/acmg/evaluate.ts` (functie `evaluateAcmg`).

De belangrijkste automatische regels (drempels als benoemde constanten bovenaan `evaluate.ts`, kalibratie volgens ClinGen 2022):

| Criterium | Databron | Regel (samengevat) |
| --- | --- | --- |
| **PVS1** | consequence + LOFTEE + ClinGen-dosage | Predicted-null effect (`stop_gained`, `frameshift_variant`, canonieke splice, `start_lost`, `transcript_ablation`). *Very strong* als LOFTEE=`HC` én het LOF-mechanisme bewezen is (ClinGen "sufficient evidence"); anders *Strong* (applies); is het LOF-mechanisme onbevestigd, dan *Strong* als **Consider**. |
| **PM2** | gnomAD-frequentie | Afwezig of AF < 1×10⁻⁴ → Supporting (ClinGen-downgrade). |
| **BA1** | gnomAD-frequentie | AF ≥ 5% → *stand-alone* benigne override. |
| **BS1 / BS2** | gnomAD-frequentie / homozygoten | AF 1–5% → BS1 (Strong); homozygoten aanwezig → BS2 (Strong als gen recessief, anders Consider/Supporting). |
| **PP2** | gnomAD missense-Z | Missense in constrained gen (Z ≥ 3.09). |
| **PP3 / BP4** | REVEL, SpliceAI, AlphaMissense | REVEL/SpliceAI-drempels bepalen de sterkte; PP3 flag't BP4 als *argues against* en omgekeerd. |
| **BP7** | consequence + SpliceAI | Synoniem zonder splice-impact (SpliceAI < 0.1); mét voorspelde splice-impact wordt BP7 juist *argues against*. |
| **PP5 / BP6** | ClinVar | ClinVar meldt de variant pathogeen → PP5 (BP6 contra); benigne → BP6 (PP5 contra). |
| **PP4** | Monarch-fenotypescore of HPO-overlap | Gen↔proband-fenotype-specificiteit; sterkte schaalt met de Monarch-score (≥0.6 Moderate, ≥0.3 Supporting), anders directe HPO-overlap op Supporting. |
| **PM6 / PS2 / PP1 / BS4** | trio-genotypes | *De novo* (afwezig bij beide sequenced ouders) → PM6 (PS2 blijft manueel); ≥2 aangedane dragers → PP1 (Consider); aangedaan familielid zónder variant → BS4 (Consider). |

De frequentie-, in-silico- en molecular-consequence-blokken markeren de *niet-passende* criteria bovendien expliciet als `not_applicable`, zodat de werkset eerlijk blijft (bv. bij een missense-variant worden PVS1, PM4, BP3 en BP7 grijs).

De gendata komt uit `GET /genes/profile` (ClinGen-dosage, GenCC-overervingsmodus, gen-HPO), de proband-HPO uit `GET /families/{id}/hpo`. Deze worden in de modal opgehaald en via `toGeneContext` naar de evaluator gevoed.

**Waar in de code:** het ophalen en samenstellen van context (`useQuery` voor gene-profile en HPO; `familyContext`, `probandHpo`, `toGeneContext`) staat in `AcmgClassificationModal.tsx`. Criteria die menselijke input vereisen die CoGA niet uit de annotatie kan afleiden (onder meer PS1, PS3, PM1, PM3, PM5, PS4, BP2) worden **nooit** automatisch als *applies* voorgesteld en zijn in `criteria.ts` gemarkeerd met `autoEvaluable: false`.

### De statische criteria-catalogus

Alle 28 ACMG/AMP-criteria (code, richting, standaardsterkte, toegestane sterktes) staan in één catalogus. Dit is de "single source of truth" voor de UI en de scorer.

**Waar in de code:** `frontend/src/lib/acmg/criteria.ts` (`ACMG_CRITERIA`, `ACMG_CRITERIA_BY_CODE`).

### De puntenschaal en de vijf klassen

CoGA gebruikt het **Tavtigian/ClinGen Bayesiaanse puntensysteem**. Elk geaccepteerd criterium draagt punten bij volgens zijn toegepaste sterkte; benigne criteria zijn negatief:

| Sterkte | Pathogeen | Benigne |
| --- | ---: | ---: |
| Supporting (PP/BP) | +1 | −1 |
| Moderate (PM) | +2 | −2 |
| Strong (PS/BS) | +4 | −4 |
| Very strong (PVS1) | +8 | — |

Het getekende totaal mapt op vijf klassen: **≥10** Pathogeen (klasse 5) · **6…9** Likely Pathogenic (4) · **0…5** VUS (3) · **−1…−6** Likely benign (2) · **≤−7** Benign (1). `BA1` (AF ≥ 5%) is een **stand-alone override**: één geaccepteerde BA1 maakt de variant benigne, ongeacht de rest.

De VUS-band (0–5) wordt volgens de MAGI-ACMG-aanpak nog opgesplitst in subtiers naar nabijheid van de LP-drempel: **4–5 hot**, **2–3 warm**, **0–1 cold**. Deze tier is `null` voor niet-VUS.

**Waar in de code (frontend):** `frontend/src/lib/acmg/score.ts` — `computeClassification`, `classKeyForPoints` (interne helper), `vusTierForPoints`. De sterkte→punten-tabel staat als `STRENGTH_POINTS` in `criteria.ts`.

**Waar in de code (backend):** `backend/app/services/acmg_points.py` — spiegelt de frontend exact: `STRENGTH_POINTS`, `class_key_for_points`, `vus_tier_for_points`, `compute_classification`. De twee implementaties zijn **parity-getest** (identieke drempels; het commentaar verwijst naar elkaars functie).

### Overrijdbaarheid en herberekening op de server

Elke rij in de modal heeft een checkbox (accepteren) en een sterkte-dropdown (`allowedStrengths`). Wijzigt de analist iets, dan wordt dat lokaal als *edit* gemarkeerd zodat een laat binnenkomende gene/HPO-query zijn werk niet overschrijft (`markEdited` / de `analystEdited`-ref, issue #337).

Cruciaal voor veiligheid: bij het opslaan stuurt de frontend wél zijn berekende totaal mee, maar de **backend negeert dat en herberekent**. `_normalize_acmg_payload` valideert elke criteriumcode (`is_valid_code`) en -sterkte (`VALID_STRENGTHS`) — onbekende waarden geven een `400` — en roept `acmg_points.compute_classification` aan. Zo hangt een opgeslagen classificatie **nooit** af van de browser.

**Waar in de code:** `handleSave` in `AcmgClassificationModal.tsx` (bouwt de `criteria`-payload en de auto-managed `acmg_class_*` / `acmg_vus_*` tags); server-herberekening in `_normalize_acmg_payload` in `backend/app/services/small_variant_review_acmg.py`.

### De schaalbalk

De groen→rood balk toont de klasse (als label), het puntentotaal, de VUS-tier-chip en een pijl op de positie van het totaal. Bij een BA1-override staat de pijl helemaal links op benigne en toont hij "BA1" in plaats van een puntenwaarde.

**Waar in de code:** `frontend/src/pages/families/AcmgScaleBar.tsx` (o.a. `positionPct`, `standAloneBenign`).

### Mitochondriale varianten (mtDNA)

Opent men de classificator op een variant uit de mtDNA-analyse (`variant.chr === 'MT'` mét `variant.mito`), dan routeert de modal naar een **aparte** evaluator volgens de ClinGen/McCormick-2020 mtDNA-specificaties. mtDNA is haploïd en maternaal overgeërfd, dus: geen *de novo*-logica (PS2/PM6 → `not_applicable`), striktere frequentiedrempels (`MT_BA1_AF = 0.005`), PVS1 alleen in eiwitcoderende loci, PP3/BP4 `not_applicable` (geen mt-predictoren geladen), en segregatie via de maternale lijn + heteroplasmie. De puntenschaal, de vijf klassen en de VUS-tiers zijn identiek — alleen de pre-evaluatie verschilt.

**Waar in de code:** `frontend/src/lib/acmg/evaluateMito.ts` (functie `evaluateMitoAcmg`). De selectie tussen de nucleaire en de mt-evaluator gebeurt in het seed-effect van `AcmgClassificationModal.tsx`.

### Van suggesties naar werkselecties

De brug tussen de evaluator en de modal is `buildInitialSelections`: opgeslagen selecties (een eerdere classificatie) hebben voorrang op verse suggesties (de evaluator ververst enkel hun evidence-tekst), en bij meerdere suggesties voor één code wint de hoogste dispositie (`applies` > `consider` > `contraindicated` > `not_applicable`).

**Waar in de code:** `frontend/src/lib/acmg/index.ts` (`buildInitialSelections`).

---

## Deel 3 — CNV-ACMG (structurele varianten)

Kopie-aantalvarianten worden geclassificeerd volgens de **ClinGen-2019 CNV-standaard** (Riggs et al., 2020) — een fundamenteel ander puntensysteem dan de SNV-ACMG:

- De bewijs-secties en gewichten **verschillen tussen verlies (deletie) en winst (duplicatie)**. Er zijn dus twee aparte catalogi (`CNV_LOSS_CRITERIA` / `CNV_GAIN_CRITERIA`).
- Punten zijn **continu** (bv. +0.90, −0.60) in plaats van discrete sterkten, en veel criteria hebben een **toegestaan bereik** (`min`/`max`, of `minPoints`/`maxPoints` in de frontend) waarbinnen de reviewer een waarde kiest.
- De klasse-drempels werken op ronde fracties: **≥0.99** P · **0.90–0.98** LP · **−0.89…0.89** VUS · **−0.98…−0.90** LB · **≤−0.99** B.

De auto-evaluator is bewust conservatief: hij vuurt alleen op signalen die de SV-payload betrouwbaar draagt (gen-overlap, gen-constraint pLI, geannoteerde overerving, aantal overlappende genen voor sectie 3). De sectie-3-genstelling verschilt per event-type: bij deletie/duplicatie (`all`-modus) tellen alle overlappende genen mee en scoort de evaluator de tier automatisch; bij inversie/translocatie/insertie (`disrupted`-modus) tellen alleen door breekpunten *verstoorde* genen en blijft de tier op 3A staan met een uitleg voor de reviewer.

Net als bij SNV **klemt en herberekent** de server: `clamp_points` snoeit elke ingediende waarde binnen het toegestane bereik van dat criterium vóór het optellen, zodat een opgeslagen classificatie nooit een client-totaal vertrouwt. De numerieke invoervelden in de modal klemmen de waarde ook al lokaal binnen `minPoints`/`maxPoints`.

**Waar in de code:**
- Frontend: `frontend/src/lib/cnvAcmg/criteria.ts` (catalogi + `CNV_CLASS_LABELS`), `evaluate.ts` (`evaluateCnv`, `cnvGeneCountTier`, `cnvKindForType`, `cnvGeneCountMode`), `score.ts` (`computeCnvClassification`, `classKeyForPoints`, met interne puntenklemming).
- Backend: `backend/app/services/cnv_acmg_points.py` (`CNV_LOSS_CRITERIA`, `CNV_GAIN_CRITERIA`, `clamp_points`, `compute_classification`) — parity met de frontend.
- UI: `frontend/src/pages/families/CnvAcmgClassificationModal.tsx` (kies loss/gain, per criterium een checkbox + numeriek puntenveld + notitie), met `CnvScaleBar`.
- Persistentie: `backend/app/services/structural_variant_review_pg.py` (`_normalize_cnv_acmg_payload`, `upsert_structural_variant_review`).

**Verschil met SNV in één zin:** SNV gebruikt discrete sterkten met vaste punten en een stand-alone BA1-override; CNV gebruikt continue, geklemde puntenbereiken die per verlies/winst verschillen en géén BA1-analoog kennen.

---

## Deel 4 — Persistentie & het evidence-snapshot

### De ACMG-kolommen (schema 025 / 026)

De volledige per-criterium-blob wordt als JSONB bewaard, samen met het herberekende totaal en de klasse-sleutel (gedenormaliseerd voor filteren en samenvattingen):

| Kolom | Tabel | Schema |
| --- | --- | --- |
| `acmg` (JSONB), `acmg_point_total` (INTEGER), `acmg_class` (TEXT) | `small_variant_reviews` | `025_acmg_classification.sql` |
| `cnv_acmg` (JSONB), `cnv_point_total` (DOUBLE PRECISION), `cnv_class` (TEXT) | `structural_variant_reviews` | `026_structural_acmg_classification.sql` |

De blob bevat de criteria (code, sterkte/punten, evidence-tekst, `accepted`-vlag, `auto_suggested`-vlag), het `point_total`, de `classification`-label en voor SNV de `vus_tier`. Zo is een classificatie **reproduceerbaar en auditeerbaar**: men kan exact terugzien welke criteria met welke sterkte en welk bewijs zijn toegepast.

Bij het opslaan schrijft de modal de berekende klasse ook terug als review-tag (`acmg_class_N`, plus `acmg_vus_<tier>` voor een VUS), zodat kaarten en samenvattingen ze oppikken. Herclassificeren buiten de VUS-band laat de tier-tag automatisch weg.

**Waar in de code:** de auto-managed tags worden in `handleSave` (`AcmgClassificationModal.tsx`) toegevoegd; de opslag loopt via `upsert_small_variant_review` en de repository-`_insert_review_row` / `_update_review_row`.

### Het bevroren evidence-snapshot (schema 031)

Dit is de kern van klinische traceerbaarheid. Op het moment van classificeren vriest CoGA de *evidence* in waarop de classificatie steunde, in de JSONB-kolom `acmg_evidence_snapshot`:

- `annotation_version` en `annotation_set_hash` — de identiteit van de annotatieset; de hash is de goedkope **drift-sleutel** (verandert zodra *enige* annotatie verandert);
- `clinvar` — de meest beslissingsrelevante waarde, uit de per-transcript-annotaties;
- `captured_at` — het tijdstip.

**Waar in de code:** `build_evidence_snapshot` in `backend/app/services/small_variant_review_acmg.py`; het snapshot wordt gevuld in `upsert_small_variant_review` (`acmg_evidence_snapshot = build_evidence_snapshot(variant, now) if normalized_acmg else None`). De kolom komt uit `backend/db/schema/postgres/031_classification_evidence_snapshot.sql`.

### De onveranderlijke klinische audittrail

Naast het snapshot schrijft elke *Small-Variant*-review-wijziging een before→after-record in de **append-only** `clinical_audit_events`-tabel, in dezelfde transactie als de wijziging: *wie* welke variant classificeerde/taggde, *wanneer*, en *wat* veranderde (klasse, criteria-codes, tags, notitie). Dit is de klinische *actie*-log, los van de HTTP-*toegangs*-log `audit_log_pg`. De records worden bovendien in een **hash-chain** aan elkaar gebonden (per familie), zodat wissen, herordenen of bewerken van een record detecteerbaar is.

**Waar in de code:** `record_review_changes` (aangeroepen als `_audit()` in `upsert_small_variant_review`, vóór de `commit`), met `diff_review_changes` en `record_clinical_event` in `backend/app/services/clinical_audit_service.py`; de hashketen zit in `chain_row_hash` / `verify_chain` uit `backend/app/services/hash_chain.py`.

---

## Deel 5 — Classification drift

Een ondertekend rapport moet aantoonbaar gebonden zijn aan het bewijs achter elke gerapporteerde classificatie. Maar annotaties evolueren: een nieuwe ClinVar-/gnomAD-/annotatierelease kan het bewijs onder een reeds gemaakte classificatie doen verschuiven. CoGA detecteert dit als **drift**.

De drift-service vergelijkt, per ACMG-geclassificeerde variant, het **bevroren snapshot** met de **huidige** annotatie. Ze berekent "current" met exact dezelfde snapshot-builder (`build_evidence_snapshot`), zodat frozen en current op identieke wijze worden geëxtraheerd. De uitkomst is één van:

| Status | Betekenis |
| --- | --- |
| `current` | Ongewijzigd (hashes gelijk). |
| `drifted` | Annotatie-hash veranderd sinds de classificatie. |
| `variant_missing` | De variant zit niet meer in de dataset. |
| `unknown` | Eén van beide hashes ontbreekt → binding niet verifieerbaar. |

Een subtiel maar belangrijk veiligheidsdetail: als een hash ontbreekt, wordt de status **niet** stilzwijgend als `current` gerapporteerd maar als `unknown` — en de sign-out-gate telt `unknown` als échte drift (vereist bevestiging). Een eerdere bug liet een hash-loos "current"-record de gate klaren, waardoor een verouderde classificatie ongemerkt in een rapport bevroor; het commentaar in `_diff` documenteert deze *fail-safe*.

**Waar in de code:** `backend/app/services/classification_drift_service.py` — `evaluate_classification_drift` (haalt alle reviews met een snapshot op, vergelijkt met `get_small_variant_family_record`), `_diff` (de statuslogica, inclusief de `unknown`-fail-safe), `_current_evidence`.

**Waarom dit rapport-gating triggert:** een gedetecteerde drift betekent dat een reviewer zijn classificatie opnieuw moet bekijken vóór ondertekening. De rapportvrijgave wordt hierop geblokkeerd tot de drift is erkend. De volledige rapport-gating en het traceerbaarheidskader worden behandeld in [hoofdstuk 11 — Rapport & volledige traceerbaarheid](11-rapport-en-traceerbaarheid.md).

---

## Deel 6 — Adminbeheer van tags

De UI voor tag-definities zit onder Administratie. Een admin kiest een project-context (of "alle projecten"), maakt custom tags aan (label, scope global/project, primair project, gedeelde projecten, groep, kleur, beschrijving), en bewerkt/verwijdert ze in een tabel. Ingebouwde systeemtags worden getoond maar zijn "Not editable". Verwijderen vraagt een bevestiging (`window.confirm`).

**Waar in de code:** `frontend/src/pages/admin/AdminVariantTagsPage.tsx` (met `createTagMutation` / `updateTagMutation` / `deleteTagMutation` tegen `/admin/variant-tags`). De server dwingt de admin-only-regel en de projectzichtbaarheid af in `small_variant_review_tags.py` (zie Deel 1).

---

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `docs/acmg-classification.md` | Canonieke referentie van de pre-evaluatie-/exclusieregels per criterium. |
| `frontend/src/lib/acmg/criteria.ts` | Statische catalogus van de 28 ACMG/AMP-criteria + sterkte→punten. |
| `frontend/src/lib/acmg/evaluate.ts` | Nucleaire auto-evaluator (variant/gen/fenotype/trio → suggesties). |
| `frontend/src/lib/acmg/evaluateMito.ts` | mtDNA-specifieke auto-evaluator (McCormick 2020). |
| `frontend/src/lib/acmg/score.ts` | Puntenscorer + klasse- en VUS-tier-mapping (frontend). |
| `frontend/src/lib/acmg/index.ts` | `buildInitialSelections`: suggesties + opgeslagen selecties samenvoegen. |
| `frontend/src/lib/cnvAcmg/{criteria,evaluate,score}.ts` | ClinGen-2019 CNV-catalogi, auto-suggestie en geklemde scorer. |
| `frontend/src/pages/families/AcmgClassificationModal.tsx` | SNV-classificatie-UI (context ophalen, criteria toggelen, opslaan). |
| `frontend/src/pages/families/AcmgScaleBar.tsx` | Groen→rood puntenschaalbalk met VUS-tier-chip. |
| `frontend/src/pages/families/CnvAcmgClassificationModal.tsx` | CNV-classificatie-UI (loss/gain, per criterium punten). |
| `frontend/src/pages/families/SmallVariantReviewDialog.tsx` | Lichte review-dialoog (klasse/tags/notitie + prioriteitsscore). |
| `frontend/src/pages/admin/AdminVariantTagsPage.tsx` | Adminbeheer van (project-gescoopte) custom tags. |
| `backend/app/services/acmg_points.py` | Server-herberekening SNV-punten/klasse/VUS-tier (parity met frontend). |
| `backend/app/services/cnv_acmg_points.py` | Server-herberekening CNV-punten met bereikklemming. |
| `backend/app/services/small_variant_review_acmg.py` | ACMG-payload valideren, evidence-snapshot bouwen. |
| `backend/app/services/small_variant_review_pg.py` | Upsert van Small-Variant-reviews (tags, notitie, ACMG, audit). |
| `backend/app/services/small_variant_review_repository.py` | Ruwe SQL voor `small_variant_reviews` (incl. ACMG-kolommen). |
| `backend/app/services/small_variant_review_tags.py` | Tag-definities: systeemtags, custom tags, scope, admin-checks. |
| `backend/app/services/small_variant_review_presets.py` | Filter-presets (family/global, eigenaar-gebonden). |
| `backend/app/services/structural_variant_review_pg.py` | Upsert van CNV-reviews + CNV-ACMG-validatie/herberekening. |
| `backend/app/services/classification_drift_service.py` | Detecteert dat evidence sinds classificatie is gewijzigd (drift). |
| `backend/app/services/clinical_audit_service.py` | Append-only, hash-chained klinische audittrail van reviews. |
| `backend/app/services/hash_chain.py` | Tamper-evidence hashketen (`chain_row_hash`, `verify_chain`). |
| `backend/db/schema/postgres/006_project_scoped_variant_tags.sql` | Project-scope + koppeltabel voor tag-definities. |
| `backend/db/schema/postgres/025_acmg_classification.sql` | ACMG-kolommen op `small_variant_reviews`. |
| `backend/db/schema/postgres/026_structural_acmg_classification.sql` | CNV-ACMG-kolommen op `structural_variant_reviews`. |
| `backend/db/schema/postgres/031_classification_evidence_snapshot.sql` | `acmg_evidence_snapshot`-kolom (drift-sleutel). |
