# 11. Rapport & volledige traceerbaarheid

In dit hoofdstuk komt alles samen. Het beschrijft hoe een casus wordt *ondertekend* (sign-out) tot een **bevroren, geversioneerd en gehasht snapshot** van het rapport, gebonden aan de software-, annotatie- en referentieversies die het maakten. Aan bod komen de **poorten** die eerst vervuld moeten zijn (de referentie binnen de gevalideerde scope, geen onverklaarde classificatiedrift, geen onverklaarde sample-QC-fout, geen onverklaarde onvolledige import), het **append-only, hash-geketende** auditspoor en de externe **integriteitsankers**. De rode draad is de volledige keten: van het ruwe bestand met zijn hash, via het annotatiemanifest en de variant, tot het ondertekende rapport.

Wat de gebruiker op de rapportpagina ziet (de kleuren van het ondertekeningsrecord, de meldingen bij een mislukte vraag), staat in de app: *Docs → Report traceability & sign-out* (`frontend/src/content/docs/clinical-traceability.md`). Het ontwerp staat in `docs/clinical-traceability.md`; de risico's en eisen in de technical file (`TF-06`, `TF-09b`).

Enkele begrippen:

- **Snapshot:** een bevroren JSON-object dat vastlegt wat op dat moment gold.
- **Hash:** een korte, vaste vingerafdruk (SHA-256) van data. Verandert er één teken, dan verandert de hash volledig; zo wordt manipulatie *zichtbaar*.
- **Append-only:** alleen toevoegen, nooit wijzigen of wissen. In de databank afgedwongen met een *trigger* (databankcode die vóór elke wijziging draait en ze kan weigeren).

## Het rapport

Er zijn twee rapportpagina's:

- **`FamilyReportPage.tsx`** — het familierapport. Het toont de varianten met de reviewtag **`report`** (small variants en structurele varianten), met per gerapporteerd gen het genprofiel en de HPO-termen van de familie. De zinnen van het rapport bouwen hulpfuncties die apart getest worden (`reportNarrative.ts`).
- **`FamilyNiptReportPage.tsx`** — het rapport voor monogene NIPT, met de foetale fractie, de dekking en de kandidaatvarianten (hoofdstuk 8).

Wat elke variantsectie bevat, beschrijft `docs/report-template.md`. Exporteren gebeurt via de printfunctie van de browser. **Beide pagina's dragen een disclaimer** dat het rapport beslissingsondersteuning is die een gekwalificeerd klinisch wetenschapper moet bevestigen; het NIPT-rapport vraagt ook bevestiging met een invasieve diagnostische test.

Naast de varianten toont het familierapport drie herkomstelementen, elk met een eigen endpoint in `backend/app/routers/families_reports.py`:

| Element | Toont | Endpoint |
| --- | --- | --- |
| Herkomstvoettekst | De versies van annotatie en referentie (assembly, VEP, ClinVar, gnomAD, GENCODE, Monarch, …), met afwijkingen per modaliteit | `GET /families/{id}/annotation-manifest` |
| Driftmelding | Classificaties waarvan de annotatie veranderde sinds ze gemaakt werden | `GET /families/{id}/classification-drift` |
| Klinisch auditspoor | Wie wat classificeerde of tagde, wanneer, met de waarde ervoor en erna | `GET /families/{id}/clinical-audit` |

Dezelfde drie worden bij het ondertekenen bevroren.

## Ondertekenen

Ondertekenen gaat via **`POST /api/families/{id}/report/sign-out`**. De service `sign_out_report` doet, in volgorde:

0. **Poort 0 — de referentie is gevalideerd.** Staat de assembly van de familie niet in `VALIDATED_ASSEMBLIES` (standaard alleen `GRCh38`), of is er geen, dan weigert de backend (`409`, `gate = "assembly_scope"`). Die weigering kan niet worden erkend of omzeild. De pagina's tonen zo'n familie als *Not validated for clinical use*, en het rapport biedt dan geen ondertekenknop aan (`TF-06`, gevaar H12).
1. **Het snapshot samenstellen:** de familiecontext, het annotatiemanifest, de driftcontrole, de sample-QC, de sequencing-QC met de gebruikte grenzen, de importstatus, en alle gerapporteerde reviews: de small variants met klasse, criteria, notitie en bevroren bewijs, en de structurele varianten en CNV's met classificatie, CNV-criteria, tags en notitie.
2. **Poort 1 — drift** (hieronder).
3. **Poort 2 — sample-QC** (hieronder).
4. **Poort 3 — onvolledige import** (hieronder).
5. **Versie en keten vastleggen** onder een slot per familie, zodat twee gelijktijdige ondertekeningen elkaar niet kunnen storen.
6. **De hashes berekenen** en de nieuwe rij **toevoegen** aan `report_signouts` als volgende versie.
7. **Een klinisch auditevent schrijven** (`sign_out`), in dezelfde transactie.

**Wie mag ondertekenen.** Alleen wie het labo als ondertekenaar machtigt. Dat is een procedurele maatregel, geen controle in de software: CoGA laat elk lid van het project (ook een `viewer`) ondertekenen (`TF-06`, gevaar H15). Ondertekenen vraagt wel een login en projecttoegang. De ondertekenaar wordt vastgelegd als verwijzing naar het account én als tekst, zodat hij herkenbaar blijft als het account later verdwijnt.

**Wijzigingen.** Elke ondertekening is een nieuwe versie; een ondertekende versie wordt nooit gewijzigd. In de UI heet de knop dan *Amend sign-out*.

**Waar in de code:** `backend/app/services/report_signout_service.py` (`sign_out_report`, `build_report_snapshot`).

### Poort 1 — geen onverklaarde drift

Drift betekent dat het bewijs achter een classificatie veranderde sinds ze gemaakt werd (hoofdstuk 10). Voor elke ACMG-classificatie van een small variant in de familie met bevroren bewijs (gerapporteerd of niet) vergelijkt de controle de bevroren hash van de annotatieset met de huidige. Een ontbrekende hash telt als `unknown`, en een gerapporteerde classificatie zonder bevroren bewijs als `no_snapshot`; beide tellen als drift. Is er drift en heeft de ondertekenaar die niet erkend, dan volgt `409`. Erkennen vraagt een reden (anders `422`); de erkenning en de reden worden in het snapshot, en dus in de hash, bevroren en in het auditevent opgenomen.

**Beperking:** de driftcontrole dekt alleen small variants. Gerapporteerde CNV's en SV's worden wel bevroren, maar nooit op drift gecontroleerd.

**Waar in de code:** `sign_out_report` en `backend/app/services/classification_drift_service.py`.

### Poort 2 — geen onverklaarde sample-QC-fout

De sample-QC spoort verwisselde samples of een foute stamboom op (`TF-06`, gevaar H4, ernst S5). De poort blokkeert in twee gevallen:

1. een **gevonden fout** (de totaalstatus is `fail`);
2. een **controle die niet kon draaien** voor een relatie die de stamboom beweert (ouder-kind, broer-zus, de NIPT-lijn, of het geslacht van een sample zonder verwantschapsanker). Een verwisseling kan zich immers voordoen als *ontbrekende* data, en die mag niet stil worden ondertekend.

Blokkeert de poort en is ze niet erkend, dan volgt `409` met de bevindingen. Erkennen vraagt een reden (anders `422`); de reden wordt, net als het QC-oordeel, in de hash bevroren en in het auditevent opgenomen. De rapportpagina opent daarvoor een aparte dialoog waarin de reden verplicht is.

**Waar in de code:** `sign_out_report` en `_unverifiable_swap_checks` in `report_signout_service.py`.

### Poort 3 — geen onverklaarde onvolledige import

Een pakketimport die voor een deel van de datasets faalt en de familie deels geladen achterlaat, zet de vlag `import_incomplete` in de familiemetadata (hoofdstuk 6). De vlag noemt de mislukte en de gelukte datasets, het tijdstip en de importjob waarin de fout per dataset staat. Zo'n familie kan hele datasets missen, bv. alle structurele varianten (`TF-06`, gevaar H16).

Staat de vlag en heeft de ondertekenaar ze niet erkend, dan volgt `409` (`gate = "import_incomplete"`) met de mislukte datasets en de importjob. Erkennen vraagt een reden (anders `422`). De vlag en de reden worden in het snapshot, en dus in de hash, bevroren en in het auditevent opgenomen. Elke gezette vlag telt, ook een in een onverwachte vorm. Zolang de vlag staat, toont elke familiepagina *Import incomplete*; een latere volledige import wist ze. De rapportpagina opent een aparte dialoog waarin de reden verplicht is.

**Waar in de code:** `sign_out_report` en `_import_incomplete_state` in `report_signout_service.py`; de vlag in `_flag_family_import_incomplete` (`family_package_registration.py`); de melding in `frontend/src/components/ImportIncompleteBanner.tsx`.

## Het bevroren snapshot

| Veld | Inhoud |
| --- | --- |
| `family_id`, `assembly` | De familie en de referentie-assembly |
| `modules` | Het volledige annotatie- en referentiemanifest, per modaliteit |
| `software` | De build die het snapshot maakte (`app_version` en `git_sha`) |
| `drift` | Het aantal gecontroleerde classificaties en de lijst met drift (ook `no_snapshot`) |
| `sample_qc` | De volledige sample-QC |
| `sequencing_qc` | De sequencing-QC per sample en de grenzen waartegen ze beoordeeld werd |
| `import_incomplete` | Leeg (`null`) als de data volledig geïmporteerd is; anders de mislukte en de gelukte datasets, het tijdstip en de importjob |
| `reported_variants` | Elke gerapporteerde small variant met klasse, criteria, tags, notitie en bevroren bewijs |
| `reported_structural_variants` | Elke gerapporteerde SV of CNV met classificatie, CNV-criteria, tags en notitie |

Bij het ondertekenen komen er de versie, het tijdstip, de ondertekenaar en de erkenningen met hun redenen bij. Het snapshot is zo aan **drie versie-assen** gebonden:

- **Software:** `app_version` en `git_sha`, bij het bouwen van de image vastgelegd.
- **Annotatie en pipeline:** het bevroren manifest en, per classificatie, de hash van de annotatieset.
- **Referentie:** de assembly, de bron van de genloci die CoGA zelf laadde (GENCODE, of de UCSC-tabel als GENCODE niet lukte) en de Monarch-release.

**De hash.** De inhoudshash is een SHA-256 over een vaste, op sleutel gesorteerde JSON-codering; lijsten worden vooraf op een stabiele sleutel gesorteerd. De klinische secties zijn deterministisch: dezelfde inhoud geeft dezelfde vingerafdruk, en daarop steunt de controle hieronder. De inhoudshash zelf omvat ook de versie, het tijdstip en de ondertekenaar, en is dus per ondertekening uniek. Een opgeslagen hash wordt altijd herberekend over het snapshot zoals het opgeslagen werd; een versie van vóór een nieuw veld blijft dus geverifieerd.

**Los van ClickHouse.** Het snapshot bevat zelf de waarden die het nodig heeft. Een latere herbouw van ClickHouse of een nieuwe annotatieversie verandert het ondertekende record niet.

**Waar in de code:** `build_report_snapshot`; de codering in `backend/app/services/hash_chain.py`; de tabel `report_signouts` in `04_traceability.sql`.

### Toont de rapportpagina het ondertekende rapport?

Nee, niet vanzelf. In het kort:

- De rapportpagina toont altijd de **huidige** data.
- Een controle (`GET /families/{id}/report/sign-out-check`) bouwt het snapshot zoals het nu zou worden bevroren en vergelijkt het, sectie per sectie, met de laatste ondertekende versie. Alleen bij een bevestigde overeenkomst toont het record "This page matches signed version N"; anders noemt het de gewijzigde delen, of zegt het dat de controle niet kon draaien.
- Een afdruk die niet het bevestigde ondertekende record is, krijgt bovenaan een melding. Het bevroren record zelf is als JSON te downloaden.
- Mislukt tijdens het ondertekenen een opzoeking (bv. de QC-grenzen of de versie van de assembly of van Monarch), dan gaat de ondertekening door, maar bevriest het snapshot dat deel expliciet als *niet beschikbaar*, met de reden. Het auditevent en het ondertekeningsrecord noemen die delen.
- Het rapport volledig **uit het snapshot** opbouwen, is nog niet gerealiseerd: het snapshot bevat de verhalende invoer (genprofielen, HGVS, frequenties) nog niet.

## Append-only, hash-geketend auditspoor

CoGA houdt twee auditlogs bij:

- **De HTTP-toegangslog** `audit_log_events`: elk verzoek, met methode, pad, status en gebruiker (hoofdstuk 7).
- **Het klinische auditspoor** `clinical_audit_events`: betekenisvolle klinische handelingen (classificeren, een tag zetten of weghalen, een notitie wijzigen, ondertekenen), met per veld de waarde ervoor en erna. Het wordt in dezelfde transactie geschreven als de wijziging zelf, zodat het nooit uit de pas loopt met de data.

Beide zijn in de databank **append-only**: een trigger blokkeert wissen en wijzigen, met één uitzondering: het op `NULL` zetten van een verwijzing naar een account of familie die verwijderd wordt. De gedenormaliseerde velden bewaren dan wie het was. Dezelfde bescherming geldt voor `report_signouts`, `integrity_anchors` en `qc_threshold_changes`.

**Waar in de code:** de triggers in `backend/db/schema/postgres/04_traceability.sql`; het schrijven in `backend/app/services/clinical_audit_service.py`.

### De hash-keten

Een trigger houdt de normale applicatie tegen, maar een databankgebruiker met genoeg rechten kan een trigger uitschakelen. Daarom ligt over `clinical_audit_events` en `report_signouts` een **hash-keten**. Elke rij krijgt een `row_hash`: een SHA-256 over haar eigen onveranderlijke inhoud **plus** de `row_hash` van de vorige rij (de eerste rij gebruikt de tekst `GENESIS`). Wijzig je een rij, dan klopt haar hash niet meer; wis of verplaats je er een, dan wijst de volgende rij niet meer naar de juiste voorganger.

- De keten loopt **per familie**, op de onveranderlijke familienaam (`family_identifier`), niet op de UUID die bij verwijderen op `NULL` gaat. De ondertekende geschiedenis van een verwijderde familie blijft zo controleerbaar.
- De gehashte inhoud **laat de verwijzingen weg** die op `NULL` mogen gaan, zodat een toegelaten verwijdering de keten niet breekt.
- Bij elke lezing van een ondertekening wordt de inhoudshash opnieuw berekend; een verschil wordt als fout gelogd ("possible tampering"). Een beheerder kan de keten van een familie ook volledig laten nalopen (`GET /api/admin/integrity/verify`).

**Eerlijke reikwijdte.** De keten is **tamper-evident, niet tamper-proof**: ze maakt manipulatie zichtbaar, niet onmogelijk. Ze betrapt iedereen die de keten niet kan herberekenen. De eigenaar van de tabellen kan echter een trigger uitschakelen, een rij wijzigen en de hashes van die rij en alle volgende opnieuw berekenen. Zolang de app als eigenaar draait (hoofdstuk 2), is dat de app zelf. Daarvoor dienen de ankers hieronder. Formuleer het tegenover een regulator dus als "tamper-evident tegen een tegenstander die alleen de databank heeft, tussen bewaarde ankers", nooit als "tamper-proof" of "onveranderlijk".

**Waar in de code:** `backend/app/services/hash_chain.py`; de kolommen `row_hash` en `prev_hash` in `04_traceability.sql`.

## Integriteitsankers

Een anker maakt ook een herberekende keten zichtbaar. Bij het maken van een anker doet `create_integrity_anchor` drie dingen:

1. de **kop van elke keten** vastleggen (per familie, voor `report_signouts` en `clinical_audit_events`: de lengte en de laatste `row_hash`);
2. die koppen samenvatten in één hash, die aan het vorige anker ketenen, en het geheel **ondertekenen** met een Ed25519-sleutel die in de configuratie staat, **nooit in de databank**;
3. het anker append-only opslaan in `integrity_anchors`.

Wie de sleutel niet heeft, kan een keten wel herberekenen, maar geen geldig ondertekend anker vervalsen. Een controle vergelijkt de huidige ketens met het laatste anker, of loopt de hele ankerketen en alle handtekeningen na, en meldt `ok`, `diverged`, `chain_broken`, `signature_invalid`, `unknown_key` of `unverifiable_unsigned`.

**Wanneer ontstaat een anker?** Een beheerder of een externe planner roept `POST /api/admin/integrity/anchor` aan; de controles zijn `GET /api/admin/integrity/anchor/verify` en `…/verify-chain`. De code plant dit niet in en er is geen scherm voor: hoe vaak een anker wordt gemaakt, is een procedureafspraak.

**Grenzen.** Een ankersleutel die in handen valt (bv. bij een gecompromitteerde server) doorbreekt de bescherming; daartegen zou een hardwaresleutel (HSM) nodig zijn. Het wissen van de *laatste* ankers is alleen te zien met een kopie buiten de databank. Die export naar een externe opslag is voorzien maar nog niet gebouwd (`export_anchor` doet nog niets).

**Waar in de code:** `backend/app/services/integrity_anchor_service.py`; de tabel en trigger in `04_traceability.sql`; de rechten in `05_grants.sql`.

### De variantopslag in ClickHouse

De ankers dekken de Postgres-ketens. De variantopslag in ClickHouse bewaakt een aparte monitor: kort na het opstarten en daarna op een vast interval controleert hij de varianttabellen van elke assembly, en bij een beschadigde of ontbrekende tabel schrijft hij een fout naar de log, bedoeld om een waarschuwing te laten afgaan vóór gebruikers er last van hebben. Het laatste resultaat wordt in het geheugen bewaard, maar door geen endpoint getoond; een beheerder start de controle zelf via `GET /api/admin/clickhouse/variants/{assembly}/integrity`.

**Waar in de code:** `backend/app/services/clickhouse_integrity_monitor.py`.

## De volledige traceerbaarheidsketen

1. **Ruw bestand → hash.** Elk bronbestand staat in `raw_import_files`, met zijn SHA-256 (hoofdstuk 6).
2. **Annotatiemanifest.** De tool- en databankversies uit de VCF-headers staan per familie in `family_annotation_manifest` (hoofdstuk 6).
3. **Variant in ClickHouse.** Elke variant draagt de hash van de annotatieset waarmee hij werd geannoteerd (hoofdstuk 3).
4. **Classificatie en bewijs.** Bij elke ACMG-classificatie van een small variant worden de annotatieversie, de hash van de annotatieset, de ClinVar-waarde en het tijdstip bevroren (hoofdstuk 10).
5. **Poorten.** Vóór het ondertekenen worden de assembly, de drift en de sample-QC gecontroleerd; wat niet in orde is, moet worden erkend, met een reden.
6. **Bevroren rapport.** Het snapshot met manifest, softwareversie, drift, QC en gerapporteerde varianten wordt gehasht en append-only opgeslagen als nieuwe versie.
7. **Keten en anker.** De ondertekening komt in de hash-keten van de familie, er komt een `sign_out`-regel in het klinische auditspoor, en een ondertekend anker kan de ketenkoppen extern vastleggen.

Zo loopt een auditor van de hash van het ruwe bestand, via de versies en het bewijs achter elke classificatie, tot een ondertekend rapport waarvan de integriteit extern te controleren is.

## IVDR-koppeling

Deze keten is de technische invulling van de eis tot traceerbaarheid onder de IVDR (in-house IVD, Artikel 5(5); device boundary "geannoteerde VCF → ondertekend klinisch rapport"). Het ontwerp staat in `docs/clinical-traceability.md`, de herkomst van annotaties in `docs/annotation-provenance.md`. Voor het regelgevende dossier: `docs/regulatory/`, met name TF-06 (risicobeheer, o.a. de gevaren H4, H12 en H15) en TF-09 (verificatie en validatie).

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/families_reports.py` | Manifest, drift, klinische audit, sample-QC, ondertekenen |
| `backend/app/services/report_signout_service.py` | Ondertekenen, snapshot, poorten, hashes en keten |
| `backend/app/services/hash_chain.py` | Vaste JSON-codering, SHA-256, de keten en haar controle |
| `backend/app/services/clinical_audit_service.py` | Het klinische auditspoor |
| `backend/app/services/integrity_anchor_service.py` | Ondertekende ankers en hun controle |
| `backend/app/services/classification_drift_service.py` | Drift |
| `backend/app/services/annotation_manifest_service.py` | Het annotatie- en referentiemanifest |
| `backend/app/services/clickhouse_integrity_monitor.py` | Bewaking van de variantopslag |
| `backend/db/schema/postgres/04_traceability.sql` · `05_grants.sql` | De tabellen, triggers en rechten |
| `frontend/src/pages/families/FamilyReportPage.tsx` · `FamilyNiptReportPage.tsx` | De rapportpagina's |
