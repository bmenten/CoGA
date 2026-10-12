# 11. Rapport & volledige traceerbaarheid

In dit hoofdstuk komt alles samen. Het beschrijft hoe een casus wordt *ondertekend* (sign-out) tot een **bevroren, geversioneerd en gehasht snapshot** van het rapport, gebonden aan de software-, annotatie- en referentieversies die het maakten. Aan bod komen de **poorten** die eerst vervuld moeten zijn (de referentie binnen de gevalideerde scope, geen import of andere schrijfactie op de familie bezig, geen onverklaarde classificatiedrift, geen onverklaarde sample-QC-fout, geen onverklaarde onvolledige import), het **append-only, hash-geketende** auditspoor en de externe **integriteitsankers**. De rode draad is de volledige keten: van het ruwe bestand met zijn hash, via het annotatiemanifest en de variant, tot het ondertekende rapport.

Wat de gebruiker op de rapportpagina ziet (de kleuren van het ondertekeningsrecord, de meldingen bij een mislukte vraag), staat in de app: *Docs → Report traceability & sign-out* (`frontend/src/content/docs/clinical-traceability.md`). Het ontwerp staat in `docs/clinical-traceability.md`; de risico's en eisen in de technical file (`TF-06`, `TF-09b`).

Enkele begrippen:

- **Snapshot:** een bevroren JSON-object dat vastlegt wat op dat moment gold.
- **Hash:** een korte, vaste vingerafdruk (SHA-256) van data. Verandert er één teken, dan verandert de hash volledig; zo wordt manipulatie *zichtbaar*.
- **Append-only:** alleen toevoegen, nooit wijzigen of wissen. In de databank afgedwongen met een *trigger* (databankcode die vóór elke wijziging draait en ze kan weigeren).

## Het rapport

Er zijn twee rapportpagina's:

- **`FamilyReportPage.tsx`** — het familierapport. Het live rapport toont de varianten met de reviewtag **`report`** (small variants en structurele varianten), met per gerapporteerd gen het genprofiel en de HPO-termen van de familie. Het vraagt per soort tot 10.000 gerapporteerde varianten, het maximum van de API, en zegt op het scherm en op de afdruk wanneer een lijst niet volledig is: meer gerapporteerde varianten dan die pagina, of een begrensde zoekopdracht (`reportedListCompleteness.ts`). Het ondertekende snapshot leest de gerapporteerde reviews rechtstreeks uit Postgres en heeft die grens niet. De zinnen van het rapport bouwen hulpfuncties die apart getest worden (`reportNarrative.ts`). Een ondertekende versie tekent `SignedFamilyReport.tsx` uit het bevroren snapshot (zie verder).
- **`FamilyNiptReportPage.tsx`** — het rapport voor monogene NIPT, met de foetale fractie, de kwaliteitscontroles (foetaal geslacht, vaderschap, het plasmasample), de dekking en de kandidaatvarianten (hoofdstuk 8).

**Alleen het familierapport wordt ondertekend.** Het NIPT-rapport heeft geen ondertekening. De embryo-indeling van PGT, die de frontend afleidt en op de familiepagina toont (hoofdstuk 8), en de resultaten van de NIPT-analyse (de foetale fractie, per variant de categorie en de kans dat de foetus het allel erfde, de de-novokandidaten en het foetale risico per gen in de recessieve weergave) staan dus in geen enkel ondertekend record. Van de NIPT-analyse bevriest een ondertekening van het familierapport alleen de controles van de sample-QC (het vaderschap, het foetale geslacht uit de X-allelen van de vader en het categoriepatroon van het cfDNA). Wat het ondertekende record per toepassing moet bevatten, beslist de eigenaar nog (`docs/open-issues.md`, REG-2; `TF-02` §6 en `TF-06`, gevaren H5, H6 en H9).

Wat elke variantsectie bevat, beschrijft `docs/report-template.md`. Exporteren gebeurt via de printfunctie van de browser. **Beide pagina's dragen een disclaimer** dat het rapport beslissingsondersteuning is die een gekwalificeerd klinisch wetenschapper moet bevestigen; het NIPT-rapport vraagt ook bevestiging met een invasieve diagnostische test.

**Beide pagina's dragen in hun kop de waarschuwingen van elke familiepagina**, en drukken ze mee af: *Not validated for clinical use* voor een familie buiten de gevalideerde scope (poort 0 hieronder) en *Import incomplete* zolang een import de familie deels geladen achterliet of niet afrondde (poort 3). Het NIPT-rapport kent geen ondertekening en dus geen poort: daar zegt de afdruk zelf dat de data onvolledig zijn.

**Beide pagina's eindigen met een voettekst** die noemt wanneer het rapport gemaakt werd en met welke build (*Software: CoGA x.y.z (commit)*, uit `GET /api/version`), gevolgd door het label: in-house IVD volgens IVDR Artikel 5(5), niet CE-gemarkeerd, alleen voor intern gebruik bij CMGG, en de fabrikant. Het rapport vraagt de build bij elke opening opnieuw op en toont tot dan geen eerder bewaarde waarde, zodat de voettekst na een update nooit de vorige build noemt. Lukt het opvragen niet, dan zegt de voettekst dat en begint een afdruk met de melding dat ze onvolledig is. Een ondertekende versie noemt in haar voettekst twee builds: *Signed with*, de build die ze bevroor, en *Rendered by*, de build die de pagina uit het record tekende. Dezelfde versie en hetzelfde label staan in de voettekst van elke pagina van de app.

**Waar in de code:** `useReportBuild` in `frontend/src/lib/appVersion.ts`, `frontend/src/pages/families/ReportSoftwareIdentity.tsx`, en de tekst van het label in `frontend/src/lib/deviceLabel.ts`.

Naast de varianten toont het familierapport drie herkomstelementen, elk met een eigen endpoint in `backend/app/routers/families_reports.py`:

| Element | Toont | Endpoint |
| --- | --- | --- |
| Herkomstvoettekst | De versies van annotatie en referentie (assembly, VEP, ClinVar, gnomAD, GENCODE, Monarch, HPO, …), met afwijkingen per modaliteit | `GET /families/{id}/annotation-manifest` |
| Driftmelding | Classificaties (van small variants, SV's en CNV's) waarvan het bewijs veranderde sinds ze gemaakt werden | `GET /families/{id}/classification-drift` |
| Klinisch auditspoor | Wie wat classificeerde of tagde of het annotatiemanifest verving, wanneer, met de waarde ervoor en erna | `GET /families/{id}/clinical-audit` |

De versies en de driftstatus worden bij het ondertekenen bevroren. Het auditspoor zit niet in het snapshot: het is zelf append-only.

## Ondertekenen

Ondertekenen gaat via **`POST /api/families/{id}/report/sign-out`**. De service `sign_out_report` doet, in volgorde:

0. **Poort 0 — de referentie is gevalideerd.** Staat de assembly van de familie niet in `VALIDATED_ASSEMBLIES` (standaard alleen `GRCh38`), of is er geen, dan weigert de backend (`409`, `gate = "assembly_scope"`). Die weigering kan niet worden erkend of omzeild. De pagina's tonen zo'n familie als *Not validated for clinical use*, en het rapport biedt dan geen ondertekenknop aan (`TF-06`, gevaar H12).
1. **Poort 0b — er wordt niet naar de familie geschreven** (hieronder). Ook deze weigering kan niet worden erkend.
2. **Het snapshot samenstellen:** de familiecontext, het annotatiemanifest, de driftcontrole, de sample-QC, de sequencing-QC met de gebruikte grenzen, de importstatus, en alle gerapporteerde reviews: de small variants met klasse, criteria, notitie en bevroren bewijs, en de structurele varianten en CNV's met classificatie, CNV-criteria, tags, notitie en bevroren bewijs. Daarna worden de importjobs nog eens nagekeken (poort 0b).
3. **Poort 1 — drift** (hieronder).
4. **Poort 2 — sample-QC** (hieronder).
5. **Poort 3 — onvolledige import** (hieronder).
6. **Versie en keten vastleggen** onder een slot per familie, zodat twee gelijktijdige ondertekeningen elkaar niet kunnen storen.
7. **De hashes berekenen** en de nieuwe rij **toevoegen** aan `report_signouts` als volgende versie.
8. **Een klinisch auditevent schrijven** (`sign_out`), in dezelfde transactie.

**Wie mag ondertekenen.** Alleen wie het labo als ondertekenaar machtigt. Dat is een procedurele maatregel, geen controle in de software: CoGA laat elk lid van het project (ook een `viewer`) ondertekenen (`TF-06`, gevaar H15). Ondertekenen vraagt wel een login en projecttoegang. De ondertekenaar wordt vastgelegd als verwijzing naar het account én als tekst, zodat hij herkenbaar blijft als het account later verdwijnt.

**Wijzigingen.** Elke ondertekening is een nieuwe versie; een ondertekende versie wordt nooit gewijzigd. In het live rapport heet de knop dan *Amend sign-out*.

**Waar in de code:** `backend/app/services/report_signout_service.py` (`sign_out_report`, `build_report_snapshot`).

### Poort 0b — er wordt niet naar de familie geschreven

Een pakketimport schrijft een familie over enkele minuten: eerst de stamboom en de samples, dan dataset na dataset, met een commit na elke stap (hoofdstuk 6). De vlag `import_incomplete` komt pas als de import mislukt is. Een snapshot dat tijdens de import gelezen wordt, kan dus een half geïmporteerde familie als volledig bevriezen (`TF-06`, gevaar H16). Daarom weigert de backend (`409`, niet te erkennen, zonder te wachten):

- zolang voor de familie een importjob (geen dry run) `queued` (met de familie in de aanvraag), `validating` of `running` is: `gate = "import_in_progress"`, met het id en de status van de job. Een job waarvan de worker stopte, blijft weigeren tot een worker hem overneemt; was de import al begonnen te schrijven, dan beëindigt die worker de job als onderbroken en blijft de familie gemarkeerd (poort 3). Leefde de import nog en kon alleen zijn heartbeat niet geschreven worden, dan stopt die heartbeat de import zodra hij de databank weer bereikt (hoofdstuk 6, #746); tot dan weigeren de schrijfsloten van de import de ondertekening (volgend punt), zolang hun verbinding ze houdt;
- zolang een schrijver de schrijfsloten van de varianten van de familie houdt (een import, een upload, een verwijdering door een admin; `family_variant_write_lock.py`, hoofdstuk 6): `gate = "variant_writes_in_progress"`.

Wie de poort passeert, deelt die sloten tot het einde van de transactie (`pg_try_advisory_xact_lock_shared`): ondertekeningen houden elkaar niet tegen, maar een schrijver van de varianten wacht tot de ondertekening klaar is. De sloten worden genomen vóór het slot van de sign-outketen, zoals elke schrijver ze als eerste neemt. Na het snapshot kijkt de ondertekening de importjobs opnieuw na: een import die intussen werd opgepakt, zet zijn job op `running`, met de familie, vóór hij iets van die familie schrijft, en weigert dan de ondertekening. Dat vastleggen is geen formaliteit: lukt het niet (een databankfout, of een andere worker nam de job over), dan stopt de import zonder iets van de familie te schrijven, en eindigt de job op `failed` met de reden, zolang hij nog van deze worker is en bij te werken valt (#736). De rapportpagina toont beide weigeringen als *Not signed out.*, zonder dialoog.

**Waar in de code:** `_refuse_while_family_is_written` en `_refuse_if_import_started` in `report_signout_service.py`; `try_share_family_variant_writes` in `family_variant_write_lock.py`.

### Poort 1 — geen onverklaarde drift

Drift betekent dat het bewijs achter een classificatie veranderde sinds ze gemaakt werd (hoofdstuk 10). Voor elke ACMG-classificatie van een small variant in de familie met bevroren bewijs (gerapporteerd of niet) vergelijkt de controle de bevroren hash van de annotatieset met de huidige. Voor elke CNV-classificatie van een SV of CNV vergelijkt ze het bevroren bewijs (de genen, de pLI, de overerving, het type en de positie) met de SV zoals die nu in de data staat (hoofdstuk 10). Een ontbrekende hash of een onleesbaar bewijs telt als `unknown`, en een gerapporteerde classificatie zonder bevroren bewijs, van een small variant of van een SV, als `no_snapshot`; beide tellen als drift. Is er drift en heeft de ondertekenaar die niet erkend, dan volgt `409`; de melding zegt hoeveel ervan SV's of CNV's zijn. Erkennen vraagt een reden (anders `422`) en geldt voor beide; de erkenning en de reden worden in het snapshot, en dus in de hash, bevroren en in het auditevent opgenomen.

**Waar in de code:** `sign_out_report` en `backend/app/services/classification_drift_service.py`.

### Poort 2 — geen onverklaarde sample-QC-fout

De sample-QC spoort verwisselde samples of een foute stamboom op (`TF-06`, gevaar H4, ernst S5). De poort blokkeert in twee gevallen:

1. een **gevonden fout** (de totaalstatus is `fail`);
2. een **controle die niet kon draaien** voor een relatie die de stamboom beweert (ouder-kind, broer-zus, de NIPT-lijn, of het geslacht van een sample zonder verwantschapsanker). Een verwisseling kan zich immers voordoen als *ontbrekende* data, en die mag niet stil worden ondertekend.

Blokkeert de poort en is ze niet erkend, dan volgt `409` met de bevindingen. Erkennen vraagt een reden (anders `422`); de reden wordt, net als het QC-oordeel, in de hash bevroren en in het auditevent opgenomen. De rapportpagina opent daarvoor een aparte dialoog waarin de reden verplicht is.

**Waar in de code:** `sign_out_report` en `_unverifiable_swap_checks` in `report_signout_service.py`.

### Poort 3 — geen onverklaarde onvolledige import

Een pakketimport die voor een deel van de datasets faalt en de familie deels geladen achterlaat, zet de vlag `import_incomplete` in de familiemetadata (hoofdstuk 6). De vlag noemt de mislukte en de gelukte datasets, het tijdstip en de importjob waarin de fout per dataset staat. Zo'n familie kan hele datasets missen, bv. alle structurele varianten (`TF-06`, gevaar H16).

Een import waarvan het proces halverwege stopte (een herstart, een crash, geen geheugen meer), zet die vlag niet: daar draait niets meer. Wat hij achterlaat, is zijn item in `import_unfinished` in de familiemetadata, dat hij vóór zijn eerste schrijfactie zette (hoofdstuk 6). Het noemt de importjob, het begin, de datasets die hij zou importeren en die hij afrondde; de andere kunnen half geschreven zijn of ontbreken. Zo'n item telt voor deze poort als de vlag.

Staat de vlag of een item en heeft de ondertekenaar dat niet erkend, dan volgt `409` (`gate = "import_incomplete"`) met de mislukte datasets en de importjob, en elk item (`import_unfinished`). Eén erkenning geldt voor beide en vraagt een reden (anders `422`). De vlag, de items en de reden worden in het snapshot, en dus in de hash, bevroren en in het auditevent opgenomen. Elke gezette vlag of elk item telt, ook in een onverwachte vorm. Zolang er een staat, toont elke familiepagina *Import incomplete*. De vlag verdwijnt pas als een import elke mislukte dataset opnieuw heeft geïmporteerd (een latere mislukking houdt de vorige bij, elk met zijn importjob). Mislukte een `overwrite` en daarna ook het terugzetten van de familie (anders dan door te weigeren zonder volledige back-up), dan kan elke dataset van die import rijen verloren hebben: de vlag noemt ze dan alle als mislukt, ook de gelukte, en de poort blijft dicht tot elk ervan opnieuw geïmporteerd is (hoofdstuk 6). Een item wist alleen een import met `overwrite` die de datasets opnieuw importeert die de gestopte import niet afrondde, voor dezelfde samples en (bij de small variants) dezelfde bron. De rapportpagina opent een aparte dialoog waarin de reden verplicht is.

**Waar in de code:** `sign_out_report`, `_import_incomplete_state` en `_import_unfinished_state` in `report_signout_service.py`; de vlag in `_flag_family_import_incomplete` en het item in `ImportMark` (`family_package_registration.py`); de melding in `frontend/src/components/ImportIncompleteBanner.tsx`, die `FamilyPageBanners.tsx` op elke familiepagina toont: in `FamilyPageHeader.tsx`, of in de eigen kop van de NIPT-pagina's, de viewers en de ROI-markerpagina. `frontend/src/__tests__/familyRouteBanners.test.tsx` loopt elke familieroute af en eist beide waarschuwingen.

## Het bevroren snapshot

| Veld | Inhoud |
| --- | --- |
| `family_id`, `assembly` | De familie en de referentie-assembly |
| `modules` | Het volledige annotatie- en referentiemanifest, per modaliteit |
| `reference_modules` | De referentiemodules die bij het bevriezen werden opgezocht: ontbreekt er een in `modules`, dan was die toen niet geladen |
| `software` | De build die het snapshot maakte (`app_version` en `git_sha`) |
| `drift` | Het aantal gecontroleerde classificaties van small variants en de lijst met drift (ook `no_snapshot`) |
| `structural_drift` | Hetzelfde voor de classificaties van SV's en CNV's, met per drift wat verschoof |
| `sample_qc` | De volledige sample-QC |
| `sequencing_qc` | De sequencing-QC per sample en de grenzen waartegen ze beoordeeld werd |
| `import_incomplete` | Leeg (`null`) als de data volledig geïmporteerd is; anders de mislukte en de gelukte datasets, het tijdstip en de importjob |
| `import_unfinished` | Leeg (`{}`) als geen import halverwege stopte; anders per import de job, het begin, de datasets en welke ervan afgerond waren |
| `reported_variants` | Elke gerapporteerde small variant met klasse, criteria, tags, notitie en bevroren bewijs |
| `reported_structural_variants` | Elke gerapporteerde SV of CNV met classificatie, CNV-criteria, tags, notitie en bevroren bewijs |

Bij het ondertekenen komen er de versie, het tijdstip, de ondertekenaar en de erkenningen met hun redenen bij. Het snapshot is zo aan **drie versie-assen** gebonden:

- **Software:** `app_version` en `git_sha`, bij het bouwen van de image vastgelegd.
- **Annotatie en pipeline:** het bevroren manifest en, per classificatie, de hash van de annotatieset; bij een SV of CNV de hash van het bevroren bewijs, met de versies van de SV-callset erachter.
- **Referentie:** de assembly, de bron van de genloci die CoGA zelf laadde (GENCODE, of de UCSC-tabel als GENCODE niet lukte), de Monarch-release en de HPO-release (de release van de laatste import van de ontologie).

**De hash.** De inhoudshash is een SHA-256 over een vaste, op sleutel gesorteerde JSON-codering; lijsten worden vooraf op een stabiele sleutel gesorteerd. De klinische secties zijn deterministisch: dezelfde inhoud geeft dezelfde vingerafdruk, en daarop steunt de controle hieronder. De inhoudshash zelf omvat ook de versie, het tijdstip en de ondertekenaar, en is dus per ondertekening uniek. Een opgeslagen hash wordt altijd herberekend over het snapshot zoals het opgeslagen werd; een versie van vóór een nieuw veld blijft dus geverifieerd.

**Los van ClickHouse.** Het snapshot bevat zelf de waarden die het nodig heeft. Een latere herbouw van ClickHouse of een nieuwe annotatieversie verandert het ondertekende record niet.

**Waar in de code:** `build_report_snapshot`; de codering in `backend/app/services/hash_chain.py`; de tabel `report_signouts` in `04_traceability.sql`.

### Toont de rapportpagina het ondertekende rapport?

Alleen de ondertekende versie is het ondertekende rapport. De rapportpagina heeft twee weergaven:

- **Een ondertekende versie** (`?version=N`). Een ondertekende casus opent op de laatste. `SignedFamilyReport.tsx` tekent ze uitsluitend uit het bevroren snapshot dat `GET /families/{id}/report/sign-outs/{versie}` teruggeeft; `signedReportRecord.ts` leest het sectie per sectie. De pagina toont de versie, de ondertekenaar, het tijdstip, de inhoudshash en of die nog klopt (`verified`), de build die ondertekende, per gerapporteerde variant de klasse, de aanvaarde criteria, het bevroren bewijs, de tags en de notitie, de SV's met hun ClinGen-CNV-criteria, de drift (ook die van de SV- en CNV-classificaties), de sample-QC, de sequencing-QC en de importstatus met de erkenningen, en de bevroren versies.
- Wat geen snapshot bevat — de variantbeschrijving (gen, HGVS, consequentie, genotypes, frequenties, voorspellingen), de segregatie, de gen- en fenotypecontext, het auditspoor en de pipelinesettings — zegt die pagina, voor het rapport en per variant. Ze vult het nooit aan met huidige data; elke variant heet er bij zijn ID. Een sectie die een record niet bevat, staat er als *Not in the signed record*, nooit als leeg.
- De printknop drukt die weergave af. Een record dat niet meer bij zijn inhoudshash past, een versie die een latere vervangt, of een versie waarvan niet vaststaat dat ze de laatste is, krijgt bovenaan de afdruk een melding.
- **Het live rapport** (`?view=live`) toont de **huidige** data. Hier wordt de casus ondertekend; daarna toont de pagina de nieuwe versie. Het is nooit het ondertekende rapport, ook niet als de inhoud overeenkomt ("This is the live report, not signed version N"), en elke afdruk ervan krijgt bovenaan een melding. Zolang het ondertekeningsrecord laadt of niet geladen kon worden, biedt het geen ondertekening aan.
- Een controle (`GET /families/{id}/report/sign-out-check`) bouwt het snapshot zoals het nu zou worden bevroren en vergelijkt het, sectie per sectie, met de laatste ondertekende versie. Het live rapport zegt zo of het nog overeenkomt, noemt de gewijzigde delen, of zegt dat de controle niet kon draaien. Op de laatste ondertekende versie zegt dezelfde controle, alleen op het scherm, of de data van de familie sindsdien veranderde.
- Het bevroren record zelf is in beide weergaven als JSON te downloaden.
- Mislukt tijdens het ondertekenen een opzoeking (bv. de QC-grenzen of de versie van de assembly, de genloci, Monarch of HPO), dan gaat de ondertekening door, maar bevriest het snapshot dat deel expliciet als *niet beschikbaar*, met de reden. Een HPO-ontologie uit een bestand zonder release krijgt dezelfde markering. Het auditevent en de ondertekende versie noemen die delen (`not_captured`, ook in het antwoord van `GET …/sign-outs/{versie}`).
- Elk snapshot noemt de referentiemodules die zijn build opzocht (`reference_modules`). Een module die CoGA later toevoegt, staat daar niet in: voor een versie van daarvoor vergelijkt de controle er niet op (`not_compared` bevat `modules.<sleutel>`) en noemt de ondertekende versie ze als niet vastgelegd.
- Ook een sectie die CoGA sindsdien anders berekent, leest als gewijzigd. Een controle die niet kon draaien, telt nu als *Warning*, en een notitie noemt de samples waarvan het geslacht niet gecontroleerd kon worden (hoofdstuk 8). Voor een familie met zo'n geslachtscontrole noemt de controle `sample_qc` dus gewijzigd tegenover een versie die daarvóór werd ondertekend: dat record heeft die notitie niet, en een *Pass* waar de andere controles slaagden. De ondertekende versie zelf toont wat er ondertekend werd; wie de nieuwe beoordeling wil vastleggen, ondertekent opnieuw.
- Het snapshotformaat van de release candidate is het eerste dat CoGA leest: versies die door vroegere ontwikkelbuilds zijn ondertekend, krijgen geen eigen lezing (#681). Ze blijven wel verifiëren, want de verificatie hasht het record zoals het bewaard is.
- Of het snapshot ook de variantbeschrijving en de gen- en fenotypecontext moet bevriezen, is een open beslissing (`TF-09b` §3). Dat zou veranderen wat gehasht en wat vergeleken wordt.

## Append-only, hash-geketend auditspoor

CoGA houdt twee auditlogs bij:

- **De HTTP-toegangslog** `audit_log_events`: elk verzoek, met methode, pad, status en gebruiker (hoofdstuk 7).
- **Het klinische auditspoor** `clinical_audit_events`: betekenisvolle klinische handelingen (classificeren, een tag zetten of weghalen, een notitie wijzigen, een review leegmaken of wissen, het annotatiemanifest vervangen, ondertekenen), met per veld de waarde ervoor en erna. Het geldt voor small variants, SV's en CNV's. De regel voor de klasse legt het hele bewaarde record vast: per criterium de sterkte of, bij een CNV, de punten, of het aanvaard is, het bewijs en of het een suggestie was, met het puntentotaal (en bij een CNV de ClinGen-soort). Zo staat ook een gewijzigd criterium in het spoor als de klasse gelijk blijft. Regels van SV's en CNV's dragen `metadata.modality = "sv"`, zodat hun id nooit voor dat van een small variant wordt gehouden. Ook elke wijziging aan de lijst van NIPT-artefacten (een artefact toevoegen, wijzigen of verwijderen, de lijst automatisch aanvullen of een lijst importeren) is een klinisch auditevent, want ze verandert welke varianten elke NIPT-analyse van die assay wegfiltert (hoofdstuk 15). Het spoor wordt in dezelfde transactie geschreven als de wijziging zelf, zodat het nooit uit de pas loopt met de data. Wijzigingen aan de stamboom, de familieleden en hun HPO-termen, en de review van een compound-heterozygoot paar, staan er niet in. Ze staan in de HTTP-toegangslog, en stamboom- en ledenwijzigingen ook als structuurversie in `family_structure_versions` (niet append-only en niet geketend).

Beide zijn in de databank **append-only**: een trigger blokkeert wissen en wijzigen, met één uitzondering: het op `NULL` zetten van een verwijzing naar een account of familie die verwijderd wordt. De gedenormaliseerde velden bewaren dan wie het was. Dezelfde bescherming geldt voor `report_signouts`, `integrity_anchors` en `qc_threshold_changes`.

**Waar in de code:** de triggers in `backend/db/schema/postgres/04_traceability.sql`; het schrijven in `backend/app/services/clinical_audit_service.py`.

### De hash-keten

Een trigger houdt de normale applicatie tegen, maar een databankgebruiker met genoeg rechten kan een trigger uitschakelen. Daarom ligt over `clinical_audit_events` en `report_signouts` een **hash-keten**. Elke rij krijgt een `row_hash`: een SHA-256 over haar eigen onveranderlijke inhoud **plus** de `row_hash` van de vorige rij (de eerste rij gebruikt de tekst `GENESIS`). Wijzig je een rij, dan klopt haar hash niet meer; wis of verplaats je er een, dan wijst de volgende rij niet meer naar de juiste voorganger.

- De keten loopt **per familie**, op de onveranderlijke familienaam (`family_identifier`), niet op de UUID die bij verwijderen op `NULL` gaat. De ondertekende geschiedenis van een verwijderde familie blijft zo controleerbaar. De wijzigingen aan de NIPT-artefactenlijst horen bij geen familie en vormen een eigen keten, onder de naam `system:nipt-artifacts`.
- De gehashte inhoud **laat de verwijzingen weg** die op `NULL` mogen gaan, zodat een toegelaten verwijdering de keten niet breekt.
- Bij elke lezing van een ondertekening wordt de inhoudshash opnieuw berekend; een verschil wordt als fout gelogd ("possible tampering"). Een beheerder kan de keten van een familie ook volledig laten nalopen (`GET /api/admin/integrity/verify`).

**Eerlijke reikwijdte.** De keten is **tamper-evident, niet tamper-proof**: ze maakt manipulatie zichtbaar, niet onmogelijk. Ze betrapt iedereen die de keten niet kan herberekenen. De eigenaar van de tabellen kan echter een trigger uitschakelen, een rij wijzigen en de hashes van die rij en alle volgende opnieuw berekenen. Zolang de app als eigenaar draait (hoofdstuk 2), is dat de app zelf. Daarvoor dienen de ankers hieronder. Formuleer het tegenover een regulator dus als "tamper-evident tegen een tegenstander die alleen de databank heeft, tussen bewaarde ankers", nooit als "tamper-proof" of "onveranderlijk".

**Waar in de code:** `backend/app/services/hash_chain.py`; de kolommen `row_hash` en `prev_hash` in `04_traceability.sql`.

## Integriteitsankers

Een anker maakt ook een herberekende keten zichtbaar. Bij het maken van een anker doet `create_integrity_anchor` drie dingen:

1. de **kop van elke keten** vastleggen (per familie, voor `report_signouts` en `clinical_audit_events`: de lengte en de laatste `row_hash`);
2. die koppen samenvatten in één hash, die aan het vorige anker ketenen, en het geheel **ondertekenen** met een Ed25519-sleutel die in de configuratie staat, **nooit in de databank**;
3. het anker append-only opslaan in `integrity_anchors`.

Wie de sleutel niet heeft, kan een keten wel herberekenen, maar geen geldig ondertekend anker vervalsen. Een controle vergelijkt de huidige ketens met het laatste anker, of loopt de hele ankerketen en alle handtekeningen na, en meldt `ok`, `diverged`, `chain_broken`, `signature_invalid`, `unknown_key` of `unverifiable_unsigned`, en `no_anchor` zolang er nog geen anker is.

**Wanneer ontstaat een anker?** Een beheerder of een externe planner roept `POST /api/admin/integrity/anchor` aan; de controles zijn `GET /api/admin/integrity/anchor/verify` en `…/verify-chain`. De code plant dit niet in en er is geen scherm voor: hoe vaak een anker wordt gemaakt, is een procedureafspraak.

**Grenzen.** Een ankersleutel die in handen valt (bv. bij een gecompromitteerde server) doorbreekt de bescherming; daartegen zou een hardwaresleutel (HSM) nodig zijn. Het wissen van de *laatste* ankers is alleen te zien met een kopie buiten de databank. Die export naar een externe opslag is voorzien maar nog niet gebouwd (`export_anchor` doet nog niets).

**Waar in de code:** `backend/app/services/integrity_anchor_service.py`; de tabel en trigger in `04_traceability.sql`; de rechten in `05_grants.sql`.

### De variantopslag in ClickHouse

De ankers dekken de Postgres-ketens. De variantopslag in ClickHouse bewaakt een aparte monitor: kort na het opstarten en daarna op een vast interval controleert hij de varianttabellen van elke assembly, en bij een beschadigde of ontbrekende tabel schrijft hij een fout naar de log, bedoeld om een waarschuwing te laten afgaan vóór gebruikers er last van hebben. Het laatste resultaat per assembly, met het tijdstip van de controle, bewaart de server in het geheugen. `GET /api/admin/clickhouse/variants/integrity-monitor` toont het aan beheerders, en de pagina voor ClickHouse-onderhoud toont het per assembly (hoofdstuk 15). Kon een controle niet lopen, dan staat dat er, in plaats van het vorige resultaat; de fout zelf staat in de log. Na een herstart is er geen resultaat tot de eerste controle. Een beheerder start een controle zelf via `GET /api/admin/clickhouse/variants/{assembly}/integrity`.

**Wat de controle beoordeelt.** Per varianttabel geeft ClickHouse zelf zijn oordeel over de hele tabel, over elk actief onderdeel. Alleen van een tabel die niet in orde is, leest CoGA de onderdelen één voor één, om de kapotte te noemen. Een tabel zonder rijen is in orde. Een resultaat dat niet te lezen is, geldt nooit als in orde. Dezelfde controle bewaakt het herbouwen van de gen-index van de small variants: de nieuwe index komt pas in gebruik als al zijn onderdelen in orde zijn.

**Waar in de code:** `backend/app/services/clickhouse_integrity_monitor.py`; de controle zelf in `check_clickhouse_variant_integrity` en `_check_table` (`backend/app/services/clickhouse_variant_storage.py`).

## De volledige traceerbaarheidsketen

1. **Ruw bestand → hash.** Elk bronbestand staat in `raw_import_files`, met zijn SHA-256 of, voor een alignment in een bucket, het record van de opslag (hoofdstuk 6).
2. **Annotatiemanifest.** De tool- en databankversies uit de VCF-headers staan per familie in `family_annotation_manifest` (hoofdstuk 6); een vervanging door een beheerder komt in het klinische auditspoor.
3. **Variant in ClickHouse.** Elke variant draagt de hash van de annotatieset waarmee hij werd geannoteerd (hoofdstuk 3).
4. **Classificatie en bewijs.** Bij elke ACMG-classificatie van een small variant worden de annotatieversie, de hash van de annotatieset, de ClinVar-waarde en het tijdstip bevroren; bij elke CNV-classificatie de genen, de pLI, de overerving, het type en de positie van de SV, een hash van zijn annotatie en de versies erachter (hoofdstuk 10).
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
| `backend/app/services/structural_variant_evidence.py` | Het bewijs van een CNV-classificatie en zijn drift |
| `backend/app/services/annotation_manifest_service.py` | Het annotatie- en referentiemanifest |
| `backend/app/services/clickhouse_integrity_monitor.py` | Bewaking van de variantopslag |
| `backend/db/schema/postgres/04_traceability.sql` · `05_grants.sql` | De tabellen, triggers en rechten |
| `frontend/src/pages/families/FamilyReportPage.tsx` · `FamilyNiptReportPage.tsx` | De rapportpagina's |
| `frontend/src/pages/families/SignedFamilyReport.tsx` · `signedReportRecord.ts` | Een ondertekende versie, getekend uit haar bevroren snapshot |
| `frontend/src/lib/appVersion.ts` · `frontend/src/lib/deviceLabel.ts` | De draaiende build en het label in de voetteksten |
