# Ontwerpvoorstel: review van repeat expansions en Paraphase-bevindingen

Status: **goedgekeurd door de eigenaar, ter beoordeling door QA**. Nog geen code.
Datum: 2026-10-28. De beslissingen van de eigenaar uit de vragenronde van die dag staan in §2 (B1–B16).

## 1. Aanleiding en uitgangssituatie

Small variants, structurele varianten en (sinds #1056) mtDNA-varianten hebben een review per familie:
tags (Review, Exclude, Report, eigen tags), een notitie, een classificatie (ACMG of CNV-scoring), een
bevroren evidence-snapshot met een driftpoort bij de sign-out, en een rij per wijziging in de
hash-gekoppelde klinische audittrail. Repeat expansions (TRGT) en Paraphase hebben dat niet.

Wat er nu is:

| Onderdeel | Repeat expansions | Paraphase |
| --- | --- | --- |
| Opslag | `repeat_expansions`: één rij per sample per locus; een herimport vervangt de rijen (nieuwe `id`) | `sample_paraphase_results`: één rij per sample per gen (`UNIQUE (sample_id, gene_symbol)`) |
| Status | automatisch, live herberekend tegen de catalogus `repeat_loci` (normal, intermediate, premutation, pathogenic, review, not_assessed, unknown) | automatisch uit de catalogus van medische regio's (pathogenic, carrier, normal, review, no_call) |
| Review | geen | geen |
| Live rapport | geen sectie | geen sectie |
| Ondertekend record | sectie `repeat_expansions`: **elke** call met status, drempels, catalogus, near-boundary-waarschuwing en het QA-register van de telwijze | sectie `paraphase`: **elk** resultaat met status en de catalogusregel |
| Eisen | REQ-DIAG-004 (C, geen risico) | REQ-DIAG-005 (C, geen risico) |

Het klinische oordeel over een repeat- of Paraphase-bevinding staat vandaag dus alleen in het
EPD-verslag. In CoGA is niet vastgelegd welke bevinding de analist rapporteert, als artefact beschouwt
of anders inschat dan de automatische status.

## 2. Beslissingen van de eigenaar (2026-10-28)

| # | Vraag | Beslissing |
| --- | --- | --- |
| B1 | Reviewniveau | **Per locus (repeat) of regio (Paraphase) per familie**, zoals bij small variants en SV's. De notitie kan het betrokken sample noemen. |
| B2 | Betekenis van Report | **Markeren; het record blijft volledig.** Het record bevriest zoals nu alle calls met status. Report zet een bevinding bovendien in een lijst "gerapporteerde bevindingen", met de review erbij. |
| B3 | Betekenis van Exclude | **Zoals bij small variants.** De kaart klapt dicht tot de kop en blijft in de lijst; ook *Aberrant only* blijft hem tonen. Status en record veranderen nooit. |
| B4 | Classificatie | **Een eigen schaal per type, met verminderde penetrantie en risicoallel** (§4.3), naast de automatische status. |
| B5 | Call gewijzigd na de review | **Snapshot + sign-outpoort.** De review bevriest waarop het oordeel steunde; wijkt dat af bij de sign-out, dan is een bevestiging met reden nodig, zoals bij de ACMG-driftpoort. |
| B6 | Tags | **De volledige tagcatalogus** (dezelfde als bij small variants, ook de ACMG-klassetags en VUS-tiers), met de beperking van B14 in de dialoog. |
| B7 | Rapport | **De gerapporteerde lijst op het live rapport én in de ondertekende versie**; de ondertekende versie toont daarnaast, zoals nu, alle calls. |
| B8 | Afwijkende call zonder review | **Bevestigen met reden** bij de sign-out; de reden wordt in het record en de audittrail bevroren. |
| B9 | Rechten | **Zoals bij small variants**: een analist of beheerder in het project; een lezer ziet de review maar kan niets wijzigen. |
| B10 | Uitvoering | **Twee PR's**: eerst de backend, dan de UI. |
| B11 | Technisch dossier | **Nieuwe eisen, nieuwe risico's en een gebied in TF-10 §8**, plus `clinical-traceability.md`. Geen CHANGELOG-regel en geen TF-18-rij (pre-RC). |
| B12 | Grijze zone en near-boundary in de poort (was O1) | Een *intermediate*-call telt **niet** als afwijkend voor de poort van §6.2; een telling **near a boundary** bij een niet-gevalideerd locus telt **wel**, ook als de status *normal* of *intermediate* is. |
| B13 | Reviews die de driftpoort controleert (was O2) | Alleen reviews met **een classificatie of de Report-tag**. |
| B14 | ACMG-klassetags bij repeats en Paraphase (was O3) | De volledige catalogus blijft (B6), maar de dialoog van repeats en Paraphase biedt de groep *classification* (ACMG-klassetags en VUS-tiers) **niet** aan. Een review die zo'n tag toch bevat, toont hem gemarkeerd. |
| B15 | Ander locus-ID na een herimport (was O4) | Een review **verhuist niet mee**. Een andere ID is een andere bevinding; de oude review blijft in de audittrail en de nieuwe afwijkende call telt als niet gereviewd. |
| B16 | Gerapporteerde bevinding met status *normal* (was O5) | **Mag**, bijvoorbeeld als relevant negatief resultaat, met de markering "differs from CoGA's status" als het oordeel afwijkt. |

## 3. Principes

1. **De automatische status blijft leidend en onaangeroerd.** Een review voegt een oordeel toe, maar
   wijzigt nooit de status die CoGA uit de catalogus afleidt, niet op de pagina, niet in het record en
   niet in de genoomtracks. Status en oordeel staan altijd naast elkaar.
2. **Niets verdwijnt uit het record.** Het record blijft elke call bevriezen (B2). Report en Exclude zijn
   markeringen; ze filteren niets weg (B3).
3. **Hetzelfde patroon als de bestaande reviews.** Dezelfde concurrency (409 bij een gewijzigde review),
   dezelfde audittrail-acties, dezelfde poortmechaniek (reden en vingerafdruk van de concerns, TRACE-19)
   en dezelfde UI-onderdelen (`FindingCard`, `ExcludedCardBar`, de reviewdialoog). Dat beperkt nieuwe
   risico's en houdt de gebruikerservaring gelijk.
4. **Een stabiele sleutel.** Een herimport mag een review niet loskoppelen of aan een ander locus hangen
   (§4.1).

## 4. Datamodel

### 4.1 Sleutel

| Type | Sleutel | Waarom |
| --- | --- | --- |
| Repeat | `(family_id, assembly_id, locus_id)` | `locus_id` is de TRID uit de catalogus (`FXS_FMR1`, of de gennaam bij een ingebouwd locus) en overleeft een herimport. De assembly zit in de sleutel, zodat een review op GRCh38 niet stil op GRCh37-data gaat gelden (H12). |
| Paraphase | `(family_id, assembly_id, gene_symbol)` | `gene_symbol` is Paraphase's eigen regiosleutel (`smn1`, `RCCX`), dezelfde als de UNIQUE-sleutel van de resultaten. |

Een locus buiten de catalogus (coördinaat-ID, `chr2_1000000_1000030_CAG`) krijgt ook een review op die
ID. Krijgt een bevinding na een herimport een andere ID (een andere TRGT-catalogus, een andere regio),
dan verhuist de review niet mee: het is een nieuwe bevinding, de oude review blijft in de audittrail en de
nieuwe afwijkende call telt als niet gereviewd (B15).

### 4.2 Tabellen

Twee tabellen in de baseline `03_assay.sql`, met een Alembic-revisie die eindigt op die definitie
(AGENTS.md). Ze zijn bewerkbaar, zoals `small_variant_reviews` en `structural_variant_reviews`: de
geschiedenis zit in de append-only `clinical_audit_events`. Ze zijn dus geen append-only tabellen en
krijgen geen `*_block_mutation`-trigger. Wel krijgen ze dezelfde `GRANT`s als de andere reviewtabellen in
`05_grants.sql`, en een rij in de privilegetest.

```
repeat_expansion_reviews
  id uuid PK
  family_id uuid NOT NULL → families ON DELETE CASCADE
  assembly_id uuid NOT NULL → assemblies
  locus_id text NOT NULL
  classification text NULL  CHECK (classification IN (… §4.3 repeat …))
  tags jsonb NOT NULL DEFAULT '[]'
  tag_metadata jsonb NOT NULL DEFAULT '{}'
  note text NULL
  evidence_snapshot jsonb NULL          -- §5
  updated_by text NOT NULL
  created_at, updated_at timestamptz
  UNIQUE (family_id, assembly_id, locus_id)

paraphase_reviews
  … idem, met gene_symbol text NOT NULL i.p.v. locus_id
  CHECK (classification IN (… §4.3 Paraphase …))
  UNIQUE (family_id, assembly_id, gene_symbol)
```

`docs/database.md` krijgt beide tabellen.

### 4.3 Classificatieschalen (B4)

Opgeslagen als vaste sleutel, getoond met een vast label. De backend weigert een andere waarde (422).
Leeg laten mag: dan is er geen oordeel.

| Repeat (sleutel) | Label | Paraphase (sleutel) | Label |
| --- | --- | --- | --- |
| `pathogenic_expansion` | Pathogenic expansion | `affected` | Affected |
| `reduced_penetrance` | Reduced penetrance | `carrier` | Carrier |
| `premutation` | Premutation | `risk_allele` | Risk allele |
| `intermediate` | Intermediate (grey zone) | `normal` | Normal |
| `normal` | Normal | `uncertain` | Uncertain significance |
| `uncertain` | Uncertain significance | | |

Een oordeel dat duidelijk afwijkt van de automatische status (bijvoorbeeld *Normal* bij status
*pathogenic*) mag, ook op een gerapporteerde bevinding (B16). De kaart en het rapport tonen dan beide, met een markering "differs from CoGA's
status". Er gebeurt geen automatische verzoening (§8, H18).

### 4.4 Tags (B6)

Dezelfde tagdefinities als small variants (`small_variant_tag_definitions`, globaal en per project), via
het bestaande endpoint `/families/{id}/small-variant-tags`. De snelle tags *review*, *excluded* en
*report* hebben dezelfde betekenis als elders. De dialoog biedt de groep *classification* (ACMG-klassetags
en VUS-tiers) niet aan, omdat die bij small variants van de ACMG-classificatie afgeleid zijn en hier
tegenstrijdige labels zouden geven (B14, H10/H18). Een review die zo'n tag toch bevat, toont hem
gemarkeerd.

## 5. Evidence-snapshot en driftcontrole (B5)

Bij elke opslag van een review bevriest de server, nooit de browser, wat het oordeel steunde. De inhoud
wordt canoniek opgebouwd (gesorteerd, zonder klok), zoals de recordsecties.

**Repeat**, per locus:
- de locusidentiteit (gen, chr, start, end, motief);
- de drempels die de classificatie toen gebruikte (benign-bereik, intermediate, premutation, pathogenic);
- de catalogusidentiteit (`repeat_catalogue_identity`: aantal rijen en digest);
- het QA-register van de telwijze (gevalideerd of niet);
- de locusstatus van CoGA;
- per sample: de genotype, de status van de call, en per allel de telling, de status en de
  near-boundary-waarschuwing.

**Paraphase**, per regio:
- de regio-identiteit en de catalogusregel (`paraphase_catalogue_identity`, de statusregel, het verwachte
  kopieaantal);
- per sample: `total_cn`, `gene_cn`, `highest_total_cn`, de klinisch gebruikte metrieken
  (`key_copy_number_fields`) en de `clinical_status`.

Een hash van de snapshot gaat mee in de audit-event van de opslag.

**Drift** is een verschil tussen de bevroren snapshot en dezelfde opbouw op het huidige moment. Oorzaken:
een herimport met andere tellingen of kopieaantallen, een andere drempel in de catalogus, of een
gewijzigde validatie van de telwijze. De drift telt mee in de **bestaande driftpoort** van de sign-out
(één poort, één bevestiging, zoals small-variant- en SV/CNV-drift nu samen gaan). Ze wordt per bevinding
opgesomd met wat veranderde ("PROBAND allel 2: 38 → 41; status intermediate → pathogenic"). Op de kaart
staat een chip "Changed since review" met hetzelfde detail in de tooltip.

Een review zonder snapshot (kan niet, want elke opslag maakt er een) of met een snapshot die niet te
lezen is, telt als "cannot be verified", net als bij ACMG.

Welke reviews de poort controleert: de reviews met **een classificatie of de Report-tag** (B13). Een
review met alleen een notitie of een Review-tag steunt geen oordeel dat het rapport draagt.

## 6. Sign-out en het ondertekende record

### 6.1 Nieuwe secties

Twee recordsecties, opgebouwd volgens de vier stappen in `signed_record_sections.py`:

- `repeat_expansion_reviews` — elke repeat-review met een classificatie of de Report-tag: sleutel,
  classificatie, tags, notitie, wie en wanneer, de bevroren snapshot, en de vergelijking met nu bij de
  sign-out;
- `paraphase_reviews` — idem voor Paraphase.

De bestaande secties `repeat_expansions` en `paraphase` blijven ongewijzigd: alle calls (B2). Beide
nieuwe secties gaan mee in de content-hash en in de wijzigingscontrole na de sign-out
(`REPORT_CONTENT_SECTIONS`). Een record dat ondertekend werd voor deze secties bestonden, zegt dat het
ze niet bevat; het vult ze nooit in uit live data (REQ-TRACE-007).

### 6.2 Poort voor afwijkende calls zonder review (B8)

Een nieuwe poort `unreviewed_findings`, na de importpoort, met dezelfde mechaniek als de QC-poort: de
weigering somt de concerns op, de analist bevestigt met een reden, en de vingerafdruk van de concerns
reist mee. Zijn de concerns intussen anders, dan volgt een nieuwe weigering met de huidige concerns
(TRACE-19). De reden en de concerns worden in het record en in de audit-event van de sign-out bevroren.

Een concern is een call zonder review. Een review telt als review zodra hij een classificatie, een tag of
een notitie heeft. Welke calls als afwijkend gelden (B12):

| Type | Afwijkend | Niet afwijkend |
| --- | --- | --- |
| Repeat | `pathogenic`, `premutation`, `review`, `not_assessed`, en elke call met een near-boundary-telling bij een niet-gevalideerd locus | `normal` en `intermediate` zonder near-boundary-telling, `unknown` |
| Paraphase | `pathogenic`, `carrier`, `review` | `normal`, `no_call`, `none` |

Een familie zonder zulke calls ziet de poort niet.

### 6.3 Live rapport en ondertekende weergave (B7)

- **Live rapport**: een sectie "Reported repeat expansions and Paraphase findings", met per bevinding:
  - het gen en de ziekte;
  - de calls per sample met de automatische status;
  - de classificatie van de analist en de notitie;
  - een markering als het oordeel afwijkt van de status, en de chip "Changed since review".

  Zonder gerapporteerde bevindingen zegt de sectie dat.
- **Ondertekende weergave** (`signedReportRecord.ts` + `SignedFamilyReport.tsx`): dezelfde lijst uit het
  record, op scherm en in print, en daaronder, zoals nu, alle calls.

## 7. API, rechten, concurrency en audit

**Endpoints** (in `routers/repeat_expansions.py` en de Paraphase-router):
- `GET /families/{id}/repeat-expansions` en `GET /families/{id}/paraphase` geven per locus/regio ook
  `review` en `review_drift`.
- `PUT /families/{id}/repeat-expansions/{locus_id}/review` met `classification`, `tags`, `note`,
  `expected_updated_at` en optioneel `project_id`.
- `PUT /families/{id}/paraphase/{gene_symbol}/review`, idem.
- De reviewsamenvatting voor de family workspace krijgt tellingen voor beide types.

Paden worden gebouwd met `apiPath`, ID's gecanoniseerd volgens de bestaande regels voor request-ID's.

**Rechten (B9):**
- projectgebonden toegangscontrole (`access_control.py`, `family_metadata_context.py`);
- schrijven mag een analist of beheerder in een project van de familie;
- een lezer krijgt 403 met de bestaande read-only-reden;
- een familie buiten je projecten geeft 404, alsof ze onbekend is;
- de controle-op-stuurtekens van request-parameters geldt ook voor `locus_id` en `gene_symbol`.

**Concurrency (REQ-TRACE-010):**
- `expected_updated_at` verplicht;
- een review die intussen gewijzigd is, geeft 409 met de huidige review;
- opslagen van één sleutel worden geserialiseerd (rijlock in dezelfde transactie).

**Audit (REQ-TRACE-004), in dezelfde transactie als de opslag:**
- acties `classification`, `tags` en `note`, met before/after per veld, op de bestaande keten per familie;
- `variant_id` is `repeat:<locus_id>` of `paraphase:<gene_symbol>`;
- `metadata` bevat `kind`, `assembly_id` en de snapshot-hash.

De audittijdlijn op het rapport toont deze events met een leesbaar label ("FMR1 repeat review").

## 8. Risicoanalyse (TF-06)

Nieuwe gevaren:

| ID | Gevaar | Ernst | Beheersmaatregelen |
| --- | --- | --- | --- |
| H17 | **Review hoort bij een andere of verouderde bevinding**: na een herimport, een catalogusupdate of een andere assembly steunt een classificatie of Report-tag op calls die niet meer gelden | S4 | Stabiele sleutel incl. assembly (§4.1); evidence-snapshot bij elke opslag; drift in de sign-outpoort met reden (§5); chip "Changed since review"; tests op herimport en drempelwijziging |
| H18 | **Oordeel van de analist strijdig met de automatische status** wordt verkeerd gelezen of verkeerd gerapporteerd | S4 | Status en oordeel altijd naast elkaar, nooit vervangen; markering "differs from CoGA's status" op kaart, rapport en record; IFU-tekst; usability-evaluatie (TF-12) |

Bestaande gevaren die mee veranderen:
- **H1** (gemiste bevinding): de poort voor afwijkende calls zonder review (§6.2) wordt een extra maatregel.
- **H9** (reproduceerbaarheid): twee nieuwe recordsecties onder de content-hash.
- **H10** (use error): de folding van Exclude houdt een uitgesloten bevinding zichtbaar.

## 9. Technisch dossier en documentatie (B11)

- **TF-09a**:
  - REQ-DIAG-009 (C, H17/H18): review per repeat-locus en Paraphase-regio per familie, met tags, notitie
    en een classificatie op de schaal van het type; de automatische status wijzigt nooit.
  - REQ-TRACE-015 (C, H17): de evidence-snapshot bij elke opslag, en de drift in de sign-outpoort.
  - REQ-TRACE-016 (C, H1): de poort voor afwijkende calls zonder review.
  - REQ-TRACE-017 (C, H9): de recordsecties voor de gerapporteerde en geclassificeerde reviews, en de
    lijst op het live rapport.
  - REQ-TRACE-004 en -010 worden uitgebreid tot deze reviews.
- **TF-09b**: een RTM-rij per nieuwe eis, verwijzend naar de unit-, integratie- en e2e-tests (§11).
- **TF-06**: H17 en H18, en de aanvullingen bij H1, H9 en H10.
- **TF-10 §8**: een gebied "Review van repeat expansions en Paraphase" (nieuw klinisch gedrag dat geen
  bestaand gebied dekt).
- **TF-15 (IFU)** en **TF-12**: de nieuwe bediening en het naast elkaar tonen van status en oordeel.
- `docs/clinical-traceability.md` (sign-out, de nieuwe secties en de poort), `docs/database.md`,
  `docs/testing.md`, de user guide (*Specialised analyses*, *Interpretation and review*) en de handleiding
  (hoofdstukken 10 en 11, opnieuw gebouwd met `build_site.py`).
- Geen CHANGELOG-regel en geen TF-18-rij (pre-RC, TF-18 §3a).

## 10. Openstaande punten

Geen. De vijf punten van de eerste versie zijn beslist als B12–B16 (§2). QA beoordeelt het voorstel
voor de uitvoering begint; wat QA wijzigt, komt hier als beslissing bij.

## 11. Verificatie

- **Unit**:
  - validatie van de schalen;
  - de snapshotopbouw is deterministisch;
  - de driftvergelijking per veld;
  - de concerns en de vingerafdruk van de nieuwe poort;
  - de audit-payload, die herberekend moet kunnen worden voor de hashketen.
- **Integratie (Postgres)**:
  - baseline en revisie zijn idempotent en eindigen op dezelfde definitie;
  - de grants van de app-rol (privilegetest);
  - de unieke sleutels;
  - 409 bij een gelijktijdige opslag.
- **E2E-API** (TF-09c):
  - opslaan, conflict, 403 voor een lezer, 404 buiten het project;
  - de audittrail en de verificatie van de keten;
  - herimport met andere tellingen → drift;
  - de sign-out weigert en aanvaardt (drift en ongereviewd), en het record bevat de secties met een
    stabiele hash.
- **Browser (TF-09d)**: een journey die een FMR1-call reviewt, rapporteert en ondertekent.
- De nieuwe services komen in de mypy-lijst van `pyproject.toml` en in `check-coverage-floor.py`.

## 12. Uitvoering (B10)

**PR 1 — backend** (na de beoordeling door QA):
- schema, revisie en grants;
- de services voor reviews, snapshot en drift;
- de endpoints;
- audit;
- de sign-outpoort en de recordsecties;
- de wijzigingscontrole;
- de gegenereerde API-types;
- de tests uit §11 (zonder de browserjourney);
- `database.md` en `clinical-traceability.md`;
- de TF-delen uit §9.

**PR 2 — UI**:
- kaarten met Review, Exclude, Report, Tags & notes, de classificatiechip, de tags, de notitie en de
  drift-chip;
- Exclude klapt de kaart dicht (`ExcludedCardBar`);
- de reviewdialoog met de schaal van het type;
- de poortdialoog op het rapport;
- de sectie op het live rapport en de ondertekende weergave;
- de tellingen in de family workspace;
- de user guide en de handleiding;
- de browserjourney.

Elke PR haalt de gates uit CONTRIBUTING.md ("Before you open a PR").
