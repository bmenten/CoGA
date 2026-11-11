# 11. Rapport & volledige traceerbaarheid

In dit hoofdstuk komt alles samen. Een casus wordt *ondertekend* (sign-out) tot een **bevroren, geversioneerd en gehasht snapshot** van het rapport, gebonden aan de software-, annotatie- en referentieversies die het maakten. Die ondertekening is de **analytische ondertekening**, door een gebruiker met de rol `supervisor` of hoger: ze sluit de analyse af en bevriest ze. De medische ondertekening gebeurt niet in CoGA: het klinische verslag wordt opgesteld en medisch ondertekend in het EPD (het elektronisch patiëntendossier van het ziekenhuis) en verwijst 1-op-1 naar de bevroren analyse.

Aan bod komen de **poorten** die eerst vervuld moeten zijn (de referentie binnen de gevalideerde scope, geen import of andere schrijfactie op de familie bezig, geen onverklaarde classificatiedrift, sample-QC-fout, onvolledige import of pakketmap die na de import wijzigde, geen afwijkende repeat- of Paraphase-call zonder review), het **append-only, hash-geketende** auditspoor en de externe **integriteitsankers**. De rode draad is de volledige keten: van het ruwe bestand met zijn hash, via het annotatiemanifest en de variant, tot de ondertekende analyse waarnaar het klinische verslag in het EPD verwijst.

Wat de gebruiker op de rapportpagina ziet (de kleuren van het ondertekeningsrecord, de meldingen bij een mislukte vraag), staat in de app: *Docs → Report traceability & sign-out* (`frontend/src/content/docs/clinical-traceability.md`). Het ontwerp staat in `docs/clinical-traceability.md`, de risico's en eisen in de technical file (`TF-06`, `TF-09b`).

Enkele begrippen:

- **Snapshot:** een bevroren JSON-object dat vastlegt wat op dat moment gold.
- **Hash:** een korte, vaste vingerafdruk (SHA-256) van data. Verandert er één teken, dan verandert de hash volledig; zo wordt manipulatie *zichtbaar*.
- **Append-only:** alleen toevoegen, nooit wijzigen of wissen. De databank dwingt dat af met een *trigger* (databankcode die vóór elke wijziging draait en ze kan weigeren).

## Het rapport

Er zijn twee rapportpagina's:

- **`FamilyReportPage.tsx`** — het familierapport. Het live rapport toont de varianten met de reviewtag **`report`** (small variants en structurele varianten), met per gerapporteerd gen het genprofiel en de HPO-termen van de familie. Repeatloci en Paraphase-regio's met **`report`** staan in de sectie *Reported repeat expansions and Paraphase findings* (`ReportedFindings.tsx`, uit de tabellen van de repeat- en Paraphase-pagina). Het rapport vraagt per soort tot 10.000 gerapporteerde varianten, het maximum van de API. Is een lijst niet volledig (meer gerapporteerde varianten dan die pagina, of een begrensde zoekopdracht), dan zegt het dat op het scherm en op de afdruk (`reportedListCompleteness.ts`). Het ondertekende snapshot leest de gerapporteerde reviews rechtstreeks uit Postgres en heeft die grens niet. Apart geteste hulpfuncties bouwen de zinnen van het rapport (`reportNarrative.ts`). Een ondertekende versie tekent `SignedFamilyReport.tsx` uit het bevroren snapshot (zie verder).
- **`FamilyNiptReportPage.tsx`** — het rapport voor monogene NIPT: de foetale fractie, de kwaliteitscontroles (foetaal geslacht, vaderschap, het plasmasample), de dekking en de kandidaatvarianten (hoofdstuk 8).

**De familie wordt ondertekend, een PGT-familie met haar embryo-indeling en een NIPT-familie met haar NIPT-resultaten.** Bij een PGT-familie bevriest de ondertekening van het familierapport de embryo-indeling (`pgt_analysis`, beslissing van de eigenaar van 2026-10-14): hetzelfde object dat de backend uit de opgeslagen haplotypeblokken afleidt en de familiepagina toont (hoofdstuk 8; zie *De embryo-indeling* verder).

Een monogene NIPT-familie gebruikt dezelfde ondertekening (beslissing van de eigenaar van 2026-10-14):

- Ze wordt ondertekend vanaf het NIPT-rapport (**Sign out NIPT analysis**) of vanaf het familierapport, niet vanaf de NIPT-pagina zelf. Er is één ondertekend record per analyse, met dezelfde poorten, versie en rapportcode.
- Haar record bevat ook de sectie `nipt` (`signed_record_nipt.py`, zie verder): de NIPT-analyse zoals de NIPT-pagina ze toonde, door de backend berekend.
- De controles van de sample-QC blijven in `sample_qc`: het vaderschap, het foetale geslacht uit de X-allelen van de vader, het categoriepatroon van het cfDNA en het geslacht van de ouders.
- Een gerapporteerde NIPT-variant staat in `reported_variants` en `variant_descriptions`, zoals elke gerapporteerde small variant, en in `nipt` met zijn categorie, reads en kansen.
- Het NIPT-rapport noemt de laatste ondertekende versie met haar rapportcode, en zegt of de data er nog mee overeenkomen.
- De knop en de poortdialogen deelt het met het familierapport (`reportSignOut.tsx`), net als de regel die zegt wanneer de knop niet aangeboden wordt (`signOutRefusal`). Een viewer, een lezer in het project van de familie en een analyst ondertekenen op geen van beide pagina's: de knop is uitgeschakeld met de reden, en de server weigert hen met 403 (hoofdstuk 2).

Wat elke variantsectie bevat, beschrijft `docs/report-template.md`. Exporteren gebeurt via de printfunctie van de browser.

**Beide pagina's dragen een disclaimer:** het rapport is beslissingsondersteuning die een gekwalificeerd klinisch wetenschapper moet bevestigen. Het NIPT-rapport vraagt ook bevestiging met een invasieve diagnostische test.

**Beide pagina's dragen in hun kop de waarschuwingen van elke familiepagina**, en drukken ze mee af:

- *Not validated for clinical use* voor een familie buiten de gevalideerde scope (poort 0 hieronder);
- *Import incomplete* zolang een import de familie deels geladen achterliet of niet afrondde, of een losse upload niet afrondde (poort 3);
- *Package folder changed after import* (of *Package folder gone*) zolang de pakketmap waaruit de familie geïmporteerd werd, sinds de import wijzigde of weg is (poort 3b); *Source files archived* als een superuser die verdwenen map bevestigde als bewust gearchiveerd.

Het NIPT-rapport is een live rapport dat zonder poort afgedrukt wordt; daar zegt de afdruk zelf dat de data onvolledig zijn. Net als het live familierapport zegt het dat het niet de ondertekende versie is: op het scherm naast de laatste ondertekende versie, en bovenaan elke afdruk (*Draft …*, *Not the signed report …*, *Not verified …*; `liveReportSignedState` en `liveReportSignedNotice` in `signedReportRecord.ts`, die beide pagina's gebruiken).

**Beide pagina's eindigen met een voettekst.** Die noemt wanneer het rapport gemaakt werd en met welke build (*Software: CoGA x.y.z (commit)*, uit `GET /api/version`). Een build die geen release is, noemt haar versie met `dev` erbij, bv. *CoGA 0.1.0-dev (commit)*. Daarna volgt het label: in-house IVD volgens IVDR Artikel 5(5), niet CE-gemarkeerd, alleen voor intern gebruik bij CMGG, en de fabrikant.

- Het rapport vraagt de build bij elke opening opnieuw op en toont tot dan geen bewaarde waarde, zodat de voettekst na een update nooit de vorige build noemt.
- Lukt het opvragen niet, dan zegt de voettekst dat, en begint een afdruk met de melding dat ze onvolledig is.
- Een ondertekende versie noemt twee builds: *Signed with*, de build die ze bevroor, en *Rendered by*, de build die de pagina uit het record tekende.
- Dezelfde versie en hetzelfde label staan in de voettekst van elke pagina van de app.

**Waar in de code:** `useReportBuild` in `frontend/src/lib/appVersion.ts`, `frontend/src/pages/families/ReportSoftwareIdentity.tsx`, en de tekst van het label in `frontend/src/lib/deviceLabel.ts`.

Naast de varianten toont het familierapport drie herkomstelementen, elk met een eigen endpoint in `backend/app/routers/families_reports.py`:

| Element | Toont | Endpoint |
| --- | --- | --- |
| Herkomstvoettekst | De versies van annotatie en referentie (assembly, VEP, ClinVar, gnomAD, GENCODE, Monarch, HPO, …), met afwijkingen per modaliteit | `GET /families/{id}/annotation-manifest` |
| Driftmelding | Classificaties (small variants, SV's en CNV's) waarvan het bewijs veranderde sinds ze gemaakt werden | `GET /families/{id}/classification-drift` |
| Klinisch auditspoor | Wie wanneer wat deed, met de waarde ervoor en erna: classificeren of taggen, een compound-heterozygoot paar beoordelen, de stamboom, de familieleden of een fenotype wijzigen, het annotatiemanifest vervangen | `GET /families/{id}/clinical-audit` (niet voor een viewer of een lezer, hoofdstuk 2) |

De versies en de driftstatus worden bij het ondertekenen bevroren. Het auditspoor zit niet in het snapshot: het is zelf append-only.

**Het auditspoor komt per pagina.** `GET /families/{id}/clinical-audit` geeft de recentste regels eerst, in één vaste volgorde: `created_at`, dan `id` (de volgorde van de hash-keten, omgekeerd).

- Standaard 200 regels per pagina, ten hoogste 500 (`limit`).
- `before` is het id van de laatste regel van de vorige pagina; dan komen de regels die daarvoor geschreven werden. Een regel die intussen bijkomt, verschuift dus niets: geen regel komt twee keer of valt weg.
- Een `before` dat geen regel van het spoor van die familie noemt, krijgt `404`.
- Het antwoord zegt hoeveel regels het spoor telt (`total`, in dezelfde query gelezen als de pagina) en of er oudere zijn (`has_more`).

Het rapport toont het spoor onder de kop *Clinical audit trail*. Toont het maar een deel, dan zegt het dat bij het spoor (*Showing the 200 most recent of 450 events …*) en bovenaan elke afgedrukte pagina (*Incomplete — the audit trail lists …*). **Show older events** leest de volgende pagina.

**Waar in de code:** `list_clinical_audit` in `backend/app/services/clinical_audit_service.py`; de pagina's van het spoor in `frontend/src/pages/families/clinicalAuditTrail.ts` en `FamilyReportPage.tsx`.

## Ondertekenen

**Wat ondertekenen betekent.** *Sign out* in CoGA is de analytische ondertekening, door een supervisor of hoger (zie *Wie mag ondertekenen*): ze sluit de analyse af en bevriest ze tot een ondertekende versie. Het is niet de medische ondertekening: die gebeurt in het EPD, buiten CoGA, op het klinische verslag. Dat verslag verwijst 1-op-1 naar één ondertekende versie, met haar **rapportcode** (zie *De rapportcode* hieronder). CoGA ziet het verslag in het EPD niet en controleert die verwijzing niet (`TF-02` §3); wie het verslag leest, vindt met de rapportcode de versie terug. De namen in de app blijven *Sign out report*, *signed version* en *signed report*. Het record zegt zelf wat het is, in één zin die mee bevroren wordt (`record_statement`); het live rapport zegt het ook.

Ondertekenen gaat via **`POST /api/families/{id}/report/sign-out`**. De service `sign_out_report` laadt eerst de familie via haar checkpoint: een familie buiten de projecten van de gebruiker antwoordt `404`, en een viewer of lezer krijgt `403` omdat hij alleen leest (hoofdstuk 2). Dan eist ze de rol `supervisor` of hoger (`require_sign_out_authority`): een analyst krijgt `403`, vóór elke poort, en er wordt niets geschreven. Daarna doet ze, in volgorde:

0. **Poort 0 — de referentie is gevalideerd.** Staat de assembly van de familie niet in `VALIDATED_ASSEMBLIES` (standaard alleen `GRCh38`), of is er geen, dan weigert de backend (`409`, `gate = "assembly_scope"`). Die weigering is niet te erkennen of te omzeilen. De pagina's tonen zo'n familie als *Not validated for clinical use*, en het rapport biedt dan geen ondertekenknop aan (`TF-06`, gevaar H12).
1. **Poort 0b — er wordt niet naar de familie geschreven** (hieronder). Ook deze weigering is niet te erkennen.
2. **Het snapshot samenstellen:** de familiecontext, het annotatiemanifest, de driftcontrole, de sample-QC, de sequencing-QC met de gebruikte grenzen, de importstatus, alle gerapporteerde reviews met hun bevroren bewijs, en wat de analyse bekeek (`signed_record_sections.py`). De tabel in *Het bevroren snapshot* geeft elke sectie. Daarna worden de importjobs nog eens nagekeken (poort 0b).
3. **Poort 1 — drift** (hieronder).
4. **Poort 2 — sample-QC** (hieronder).
5. **Poort 3 — onvolledige import** en **poort 3b — een pakketmap die na de import wijzigde** (hieronder).
6. **Poort 4 — afwijkende repeat- of Paraphase-calls zonder review** (hieronder).
7. **Versie en keten vastleggen** onder een slot per familie, zodat twee gelijktijdige ondertekeningen elkaar niet kunnen storen.
8. **De hashes en de rapportcode berekenen** onder één slot voor alle ondertekeningen (`report-code`). Heeft een ander record die code al, dan wordt opnieuw ondertekend, een microseconde later; dat verandert de inhoudshash en dus de code. Dan wordt de nieuwe rij **toegevoegd** aan `report_signouts` als volgende versie.
9. **Een klinisch auditevent schrijven** (`sign_out`), met de rapportcode, in dezelfde transactie.

**Wie mag ondertekenen.** Een gebruiker met de rol `supervisor`, `superuser` of `admin` (beslissing van de eigenaar van 2026-11-04). Een supervisor ondertekent in de projecten waarin hij analist is, een superuser en een beheerder in elk project. Een viewer, een lezer in het project en een analyst kunnen het niet: de server weigert hen met `403`, een analyst met *Signing out needs the supervisor role: ask a supervisor of this family's project to sign out the analysis.* (`SIGN_OUT_ROLE_DETAIL`, hoofdstuk 2). De familie die de API teruggeeft, zegt het vooraf (`can_sign_out`), en beide rapportpagina's schakelen *Sign out* dan uit met die reden. Zo is gevaar H15 (`TF-06`: een analyse ondertekend door iemand zonder ondertekeningsbevoegdheid) een controle in de software. Wie de rol `supervisor` krijgt, blijft een beslissing van het labo: een beheerder geeft ze alleen wie het labo machtigt om een analyse af te sluiten. De medische ondertekening in het EPD valt onder de ondertekeningsrechten van het EPD. De ondertekenaar wordt vastgelegd als verwijzing naar het account én als tekst, zodat hij herkenbaar blijft als het account later verdwijnt.

**Wijzigingen.** Elke ondertekening is een nieuwe versie; een ondertekende versie wordt nooit gewijzigd. In het live rapport heet de knop dan *Amend sign-out*. Een klinisch verslag dat op de gewijzigde analyse steunt, verwijst naar de nieuwe versie.

**Waar in de code:** `backend/app/services/report_signout_service.py` (`sign_out_report`, `build_report_snapshot`); de rol in `require_sign_out_authority` en `user_can_sign_out_metadata_projects` (`backend/app/services/access_control.py`).

### Poort 0b — er wordt niet naar de familie geschreven

Een pakketimport schrijft een familie over enkele minuten: eerst de stamboom en de samples, dan dataset na dataset, met een commit na elke stap (hoofdstuk 6). De vlag `import_incomplete` komt pas als de import mislukt. Een snapshot dat tijdens de import gelezen wordt, kan dus een half geïmporteerde familie als volledig bevriezen (`TF-06`, gevaar H16). Daarom weigert de backend meteen (`409`, niet te erkennen):

- zolang voor de familie een importjob (geen dry run) `queued` (met de familie in de aanvraag), `validating` of `running` is: `gate = "import_in_progress"`, met het id en de status van de job.
    - Een job waarvan de worker stopte, blijft weigeren tot een worker hem overneemt. Was de import al begonnen te schrijven, dan beëindigt die worker de job als onderbroken, en blijft de familie gemarkeerd (poort 3).
    - Leefde de import nog en kon alleen zijn heartbeat (zijn periodieke levensteken) niet geschreven worden, dan stopt die heartbeat de import zodra hij de databank weer bereikt (hoofdstuk 6). Tot dan weigeren de schrijfsloten van de import de ondertekening (volgend punt), zolang hun verbinding ze houdt;
- zolang een schrijver de schrijfsloten van de varianten van de familie houdt (een import, een upload, een verwijdering door een admin; `family_variant_write_lock.py`, hoofdstuk 6): `gate = "variant_writes_in_progress"`.

Wie de poort passeert, deelt die sloten tot het einde van de transactie (`pg_try_advisory_xact_lock_shared`): ondertekeningen houden elkaar niet tegen, maar een schrijver van de varianten wacht tot de ondertekening klaar is. De sloten worden genomen vóór het slot van de sign-outketen, zoals elke schrijver ze als eerste neemt.

Na het snapshot kijkt de ondertekening de importjobs opnieuw na. Een import die intussen werd opgepakt, zet zijn job op `running`, met de familie, vóór hij iets van die familie schrijft; die job weigert dan de ondertekening. Lukt dat vastleggen niet (een databankfout, of een andere worker nam de job over), dan stopt de import zonder iets van de familie te schrijven. De job eindigt dan op `failed` met de reden, zolang hij nog van deze worker is en bij te werken valt.

De rapportpagina toont beide weigeringen als *Not signed out.*, zonder dialoog.

**Waar in de code:** `_refuse_while_family_is_written` en `_refuse_if_import_started` in `report_signout_service.py`; `try_share_family_variant_writes` in `family_variant_write_lock.py`.

### Poort 1 — geen onverklaarde drift

Drift betekent dat het bewijs achter een classificatie veranderde sinds ze gemaakt werd. De controle vergelijkt met de huidige data en noemt telkens wat verschoof (hoofdstuk 10):

- voor elke ACMG-classificatie van een small variant in de familie met bevroren bewijs (gerapporteerd of niet): de bevroren hash van de annotatieset, de ClinVar-waarde, de consequentie en LoF-oproep, de frequenties en in-silicoscores en, bij een missense-variant, de missense-Z van het gen en zijn bron (het genprofiel of de VCF);
- voor elke CNV-classificatie van een SV of CNV: het bevroren bewijs (de genen, de pLI, de overerving, het type en de positie), tegenover de rij van dezelfde caller zoals die nu in de data staat.

Een ontbrekende hash of onleesbaar bewijs telt als `unknown`. Een gerapporteerde classificatie zonder bevroren bewijs, van een small variant of van een SV, telt als `no_snapshot`. Beide tellen als drift.

Is er drift en heeft de ondertekenaar die niet erkend, dan volgt `409` (`gate = "evidence_drift"`); de melding zegt hoeveel ervan SV's of CNV's zijn. De `409` somt elke getelde classificatie op zoals het snapshot ze bevriest: die van de small variants in `drifted`, die van de SV's en CNV's in `structural_drifted`. `classified_by` en `classified_at` zitten er niet in, want een notitie of tag op de review verandert ze. De dialoog toont ze zoals de ondertekende versie. Erkennen vraagt een reden (anders `422`) en geldt voor beide soorten. De erkenning en de reden worden in het snapshot, en dus in de hash, bevroren en in het auditevent opgenomen.

**Repeat- en Paraphase-reviews.** Een review van een repeatlocus of een Paraphase-regio die een classificatie of de tag *Report* draagt, bevriest de calls waarop ze steunt (`finding_review_pg.py`):

- bij een repeatlocus: de vermelding van het locus zoals het record ze bevriest (de call van elk lid, de allelen, de waarschuwingen dicht bij een grens, de drempels en het QA-register van de telwijze);
- bij een Paraphase-regio: de resultaten, zonder hun laadtijd.

Een opslag die de classificatie zet of wijzigt, of *Report* toevoegt, bevriest de calls opnieuw. Een opslag van alleen tags of een notitie laat ze staan, zodat zo'n opslag een wijziging niet ongemerkt wegneemt. Wijken de bevroren calls bij de ondertekening af van de calls van dat moment (een nieuwe import, een andere drempel in de catalogus, een validatie van de telwijze), of zijn ze er niet meer, dan telt de review mee in deze poort, met wat er veranderde (`finding_drifted`, REQ-TRACE-015; `TF-06`, gevaar H17). De repeat- en Paraphase-tabellen tonen die drift ook bij elke review.

**Waar in de code:** `sign_out_report` en `backend/app/services/classification_drift_service.py`; voor de repeat- en Paraphase-reviews `backend/app/services/finding_review_pg.py`.

### Poort 2 — geen onverklaarde sample-QC-fout

De sample-QC spoort verwisselde samples of een foute stamboom op (`TF-06`, gevaar H4, ernst S5). De poort blokkeert in twee gevallen:

1. een **gevonden fout** (de totaalstatus is `fail`);
2. een **controle die niet kon draaien** voor een relatie die de stamboom beweert (ouder-kind, broer-zus, de NIPT-lijn, of het geslacht van een sample zonder verwantschapsanker). Een verwisseling kan zich immers voordoen als *ontbrekende* data, en die mag niet stil worden ondertekend.

Blokkeert de poort en is ze niet erkend, dan volgt `409` met de bevindingen. Erkennen vraagt een reden (anders `422`); de reden wordt, net als het QC-oordeel, in de hash bevroren en in het auditevent opgenomen. De rapportpagina opent daarvoor een aparte dialoog waarin de reden verplicht is.

Bij een monogene NIPT-familie telt ook een gefaalde NIPT-kwaliteitscontrole uit de sectie `nipt`, onder dezelfde erkenning:

- geen schatting van de foetale fractie;
- een foetaal geslacht waarover de X-allelen van de vader en de chrY-dekking elkaar tegenspreken;
- mannelijk DNA als plasma van de moeder (REQ-NIPT-015).

De `409` noemt ze in `nipt_failed_checks` en in `qc_summary.messages` (dat van het plasma met zijn sample); het auditevent legt ze vast (`nipt_failed_checks`). Het vaderschap hoort er niet bij: de sample-QC leest het uit dezelfde analyse en blokkeert er al op. NIPT-resultaten die niet gelezen konden worden, staan in `not_captured`; ze blokkeren niet.

Elke bevinding noemt vooraan het sample, het paar, het kind met zijn ouders of de NIPT-vader waarover haar controle gaat, bv. *Sample X: no chrX genotypes available for sex inference.* Zonder die naam zou de melding voor twee samples zonder chrX-calls twee keer dezelfde zin lezen. Dat geldt voor de bevindingen in de `409` (`qc_summary.messages` en elke groep hieronder) en voor de groepen in het auditevent. Het foetale geslacht en de NIPT-moederlijn worden uit het cfDNA als geheel gelezen en noemen geen sample. De controles in het snapshot (`sample_qc`) houden hun eigen melding, naast hun samplevelden.

**Elke groep bevindingen apart.** De `409` zet elke bevinding in haar groep:

- `failed_checks`: de controles die faalden;
- `unverifiable_checks`: de controles die niet konden draaien;
- `nipt_failed_checks`: de NIPT-kwaliteitscontroles die faalden;
- `warning_checks`: de controles die waarschuwen; die blokkeren op zich niet.

Een controle die waarschuwt omdat ze niet kon draaien, staat alleen bij de controles die niet konden draaien: elke bevinding staat er één keer. De melding noemt elke soort bevinding die de poort tegenhoudt, zodat een fout geen controle verbergt die niet kon draaien; de bevindingen zelf staan in de groepen. De dialoog van de rapportpagina en van het NIPT-rapport (`reportSignOut.tsx`) toont elke groep onder een eigen kop, vóór het veld voor de reden. Het auditevent van de erkende ondertekening legt dezelfde groepen vast (`qc_failed`, `qc_unverifiable`, `nipt_failed_checks`, `qc_warnings`): wat de erkenning dekte.

**Waar in de code:** `sign_out_report`, `_qc_failure_summary`, `_failed_qc_checks`, `_warning_qc_checks`, `_unverifiable_swap_checks` en `_named` in `report_signout_service.py`; de dialoog in `frontend/src/pages/families/reportSignOut.tsx`.

### Poort 3 — geen onverklaarde onvolledige import

Een pakketimport die voor een deel van de datasets faalt en de familie deels geladen achterlaat, zet de vlag `import_incomplete` in de familiemetadata (hoofdstuk 6). De vlag noemt de mislukte en de gelukte datasets, het tijdstip en de importjob waarin de fout per dataset staat. Zo'n familie kan hele datasets missen, bv. alle structurele varianten (`TF-06`, gevaar H16). Een losse upload van small variants die mislukt nadat hij de callset die hij vervangt al wiste, zet dezelfde vlag voor die callset, zonder importjob (hoofdstuk 6).

Een import waarvan het proces halverwege stopte (een herstart, een crash, geen geheugen meer), zet die vlag niet: daar draait niets meer. Hij laat wel zijn item in `import_unfinished` (in de familiemetadata) achter, dat hij vóór zijn eerste schrijfactie zette (hoofdstuk 6). Het item noemt de importjob, het begin, de datasets die hij zou importeren en de datasets die hij afrondde; de andere kunnen half geschreven zijn of ontbreken. Zo'n item telt voor deze poort als de vlag. Ook deze schrijvers zetten zo'n item, zonder job, vóór hun eerste schrijfactie in ClickHouse:

- een losse upload die naar ClickHouse schrijft: sleutel `upload-…`;
- een verwijdering van data van de familie die daar rijen wist of herschrijft: sleutel `deletion-…`. Dat is het verwijderen van een sample, van zijn SV's of van de familie, een PED-upload of Family Builder die de familie vervangt, en een structuurwijziging die haar data wist (hoofdstuk 6).

Ook die items tellen als de vlag. De melding bij `409` noemt dan „a single-file upload” of „a deletion of this family's data” die „did not finish”, en wat half geschreven kan zijn of ontbreken. Het ondertekende record noemt de upload of de verwijdering, uit de sleutel die het mee bevriest.

Staat er een vlag of een item dat de ondertekenaar niet erkende, dan volgt `409` (`gate = "import_incomplete"`) met de mislukte datasets, de importjob en elk item (`import_unfinished`). Eén erkenning geldt voor beide en vraagt een reden (anders `422`); de rapportpagina opent daarvoor een aparte dialoog waarin de reden verplicht is. De vlag, de items en de reden worden in het snapshot, en dus in de hash, bevroren en in het auditevent opgenomen. Elke gezette vlag en elk item telt, ook in een onverwachte vorm. Zolang er een staat, toont elke familiepagina *Import incomplete*.

Zo verdwijnen ze weer:

- De vlag verdwijnt pas als een import elke mislukte dataset opnieuw heeft geïmporteerd, of een losse upload ze opnieuw binnenbracht. Een latere mislukking houdt de vorige bij, elk met zijn importjob.
- Het item van een gestopte import wist alleen een import met `overwrite` die de datasets opnieuw importeert die de gestopte import niet afrondde, voor dezelfde samples en (bij de small variants) dezelfde bron.
- Het verwijderen van een sample in **Admin** haalt dat sample uit de vlag en uit het item van een gestopte import. Wat alleen dat sample noemde, valt weg, want geen import of upload kan zijn data nog binnenbrengen (hoofdstuk 6).

**Een `overwrite` waarvan ook het terugzetten mislukte.** Mislukte een `overwrite` en daarna ook het terugzetten van de familie (anders dan door te weigeren zonder volledige back-up), dan kan elke dataset van de familie rijen verloren hebben, welke import of upload ze ook schreef. Daarom:

- noemt de vlag elke dataset van die import als mislukt, ook de gelukte;
- blijft het item van die import staan, met elke dataset die de familie in ClickHouse bevat (wat haar imports en losse uploads bijhielden in `imported_scopes`) en elke dataset van de import. Een dataset van een andere import of upload die alleen in Postgres staat (repeats, Paraphase, QC, fenotypes), noemt het niet: het terugzetten zet Postgres in één transactie terug, en kan zo'n dataset dus niet half geschreven achterlaten;
- bevriest het snapshot zo'n item met `whole_family: true`, zodat de `409`, de dialoog en het ondertekende record het een terugzetting noemen die niet afraakte, niet een import die stopte.

De poort blijft dan dicht tot de hele familie opnieuw geïmporteerd is met `overwrite` (niet alleen wat mislukte), en wat een losse upload binnenbracht opnieuw geüpload is met `overwrite`; beide doet een beheerder (hoofdstuk 2). Elke import die met `overwrite` slaagt, en elke upload met `overwrite`, haalt uit het item wat hij opnieuw binnenbracht, voor dezelfde samples en bron. Een `update` haalt er niets uit (hoofdstuk 6).

**Waar in de code:** `sign_out_report`, `_import_incomplete_state` en `_import_unfinished_state` in `report_signout_service.py`; in `family_package_registration.py` de vlag in `_flag_family_import_incomplete`, het item in `ImportMark` (na een mislukt terugzetten in `whole_family_entry`), wat een losse upload bijhoudt in `record_family_upload` en zijn merkteken in `FamilyWriteMark`; de melding in `frontend/src/components/ImportIncompleteBanner.tsx`, die `FamilyPageBanners.tsx` op elke familiepagina toont: in `FamilyPageHeader.tsx`, of in de eigen kop van de NIPT-pagina's, de viewers en de ROI-markerpagina. `frontend/src/__tests__/familyRouteBanners.test.tsx` loopt elke familieroute af en eist de waarschuwingen van poort 0, 3 en 3b.

### Poort 3b — een pakketmap die na de import wijzigde

CoGA toont de reads (IGV), de signaalsporen en het QC-rapport van een familie uit de pakketmap die haar record noemt, waar die map ligt. Die map moet zijn zoals de referentielevering van de familie geïmporteerd werd: de levering waaruit haar data het laatst volledig ingeladen werden, als nieuwe familie of met overwrite (een update slaat de datasets over die de familie heeft, en behoudt de referentie). Na de import volgt de importbewaker die map (hoofdstuk 6; beslissingen van de eigenaar van 2026-11-11), en bij het ondertekenen neemt CoGA ook zelf de vingerafdruk van de map, alleen met stat en buiten de event loop. Is de map gewijzigd (een bestand erbij, weg, of met een andere grootte of tijd), weg, niet te lezen (binnen 30 seconden, `SIGN_OUT_FOLDER_CHECK_SECONDS`, of helemaal niet), of een map waaruit CoGA niets toont (buiten elke importmap; ze wordt niet gelezen; in ontwikkeling zonder ingestelde importmappen telt elke lokale map, zoals voor de bewaker), dan volgt `409` (`gate = "package_source_change"`) met `package_source_change`: de referentielevering, de vingerafdruk waarmee ze geïmporteerd werd en die van nu, en de paden die erbij kwamen, verdwenen of wijzigden (elk lijstje tot 50 paden, met het aantal), of dat de bronbestanden niet meer beschikbaar zijn; `found_at_sign_out` als de eigen controle van de ondertekening het vond voor de bewaker. Een fout bij het lezen van de map is nooit een doorgang: ze telt als een map die niet te lezen is. Dit is een waarschuwing: de ondertekenaar erkent ze met een reden (`acknowledge_package_source_change`, `422` zonder reden), en ondertekenen blijft mogelijk. De wijziging en de reden worden in het snapshot, en dus in de hash, bevroren en in het auditevent opgenomen (REQ-TRACE-018; `TF-06`, gevaar H16). De wijziging wordt gelezen buiten elk savepoint: lukt dat lezen niet, dan stopt de ondertekening, de poort gaat nooit stilzwijgend open. Ze verdwijnt alleen als de map weer is zoals de referentie geïmporteerd werd, als een levering van de familie met overwrite geïmporteerd wordt, of als de nieuwe levering van de map met een reden geweigerd wordt. Een verdwenen map die een superuser bevestigde als bewust gearchiveerd (`archived`) houdt de ondertekening niet tegen: het snapshot bevriest ze, met wie, wanneer en waarom, en de familiepagina zegt dat de bronbestanden gearchiveerd zijn.

**Waar in de code:** `sign_out_report` (`PACKAGE_SOURCE_GATE`) en `_package_source_change_state` in `report_signout_service.py`; `package_changed_after_import.py`; de melding in `frontend/src/components/PackageSourceChangedBanner.tsx`, die `FamilyPageBanners.tsx` op elke familiepagina toont.

### Poort 4 — geen afwijkende repeat- of Paraphase-call zonder review

Een analist reviewt een repeatlocus en een Paraphase-regio per familie en assembly: tags (ook *Review*, *Exclude* en *Report*), een notitie en een classificatie op de eigen schaal van het type. Die staat naast de automatische status; de review wijzigt die status nooit (REQ-DIAG-009, beslissingen van de eigenaar van 2026-10-28 in `docs/repeat-paraphase-review-design.md`).

Heeft een repeatlocus of een Paraphase-regio een afwijkende call en geen review, dan volgt `409` (`gate = "unreviewed_findings"`) met elke zo'n bevinding (`unreviewed_findings`: de soort, het locus of de regio, en elk afwijkend lid met zijn status).

- Afwijkend bij een repeat: de status *pathogenic*, *premutation*, *review* of *not assessed*, of een telling dicht bij een grens op een locus waarvan QA de telwijze niet valideerde. Een grijze zone alleen telt niet.
- Afwijkend bij Paraphase: *pathogenic*, *carrier* of *review*.
- Een review telt zodra ze een classificatie, een tag of een notitie heeft.
- Kon de repeat- of Paraphase-sectie niet gelezen worden, dan staat dat in de lijst.

De ondertekenaar reviewt de bevindingen, of erkent ze met een reden (`acknowledge_unreviewed_findings`, `422` zonder reden). De lijst en de reden worden bevroren in het record en het auditevent (REQ-TRACE-016; `TF-06`, gevaar H1). Het record bevat ook de reviews met een classificatie of *Report*, met hun bevroren calls en hun drift (`repeat_expansion_reviews`, `paraphase_reviews`, REQ-TRACE-017), naast alle calls van de secties `repeat_expansions` en `paraphase`: een review markeert een bevinding, ze neemt er geen weg.

**Waar in de code:** `sign_out_report` (`UNREVIEWED_GATE`) in `report_signout_service.py`; `finding_reviews_for_record` in `finding_review_pg.py`.

### Wat een erkenning dekt

Een erkenning dekt precies de bevindingen die haar dialoog toonde. Elke `409` van poort 1, 2, 3, 3b en 4 geeft daarvoor `concerns_fingerprint` mee: een SHA-256 over de canonieke JSON (gesorteerde sleutels) van de poort en haar bevindingen zoals de `409` ze opsomt, elke lijst gesorteerd. De bevindingen per poort:

- drift: `drifted`, `structural_drifted` en `finding_drifted`;
- sample-QC: de vier groepen (`failed_checks`, `unverifiable_checks`, `nipt_failed_checks`, `warning_checks`);
- onvolledige import: `import_incomplete` en `import_unfinished`, elk zonder zijn `at` (de melding noemt het tijdstip; de jobs en de datasets zeggen welke mislukking het is);
- een pakketmap die na de import wijzigde: `package_source_change`, zonder `detected_at` (dezelfde wijziging later opnieuw gevonden is dezelfde bevinding);
- afwijkende calls zonder review: `unreviewed_findings`.

Er zit geen tijdstip in, en niets dat verandert terwijl de bevindingen gelijk blijven: dezelfde bevindingen geven dezelfde vingerafdruk. De dialoog stuurt de vingerafdruk terug met de erkenning (`drift_concerns_fingerprint`, `qc_concerns_fingerprint`, `import_incomplete_concerns_fingerprint`, `package_source_change_concerns_fingerprint`, `unreviewed_findings_concerns_fingerprint`). De ondertekening leest de poort opnieuw en vergelijkt. Ze krijgt:

- de vingerafdruk van de bevindingen van nu: de erkenning dekt ze;
- een andere (een bevinding kwam erbij, verdween of veranderde nadat de dialoog ze toonde): opnieuw `409`, met de bevindingen zoals ze nu zijn, hun vingerafdruk en `concerns_changed: true`. Er wordt niets geschreven (geen ondertekende versie, geen auditevent). De dialoog toont de bevindingen opnieuw, zegt waarom (*Nothing was signed out: the concerns changed after this dialog listed them …*), maakt het veld voor de reden leeg en vraagt een nieuwe reden, die hij met de nieuwe vingerafdruk stuurt;
- geen: `422`. De SPA (de webapp in de browser), de enige client, stuurt altijd de vingerafdruk die haar dialoog kreeg. Een waarde die geen SHA-256 in kleine hexadecimale letters is, weigert de validatie van het verzoek zelf (`422`).

Zo wordt een bevinding die opdook nadat de dialoog ze toonde, nooit ongezien mee erkend.

- Een poort zonder bevindingen vraagt geen vingerafdruk. Een erkenning van bevindingen die intussen verdwenen, erkent niets.
- De poorten worden in volgorde gelezen. Een wijziging bij een eerdere poort wordt daar eerst geweigerd; de erkenningen die al gegeven waren, gaan mee naar de volgende poging.
- Het auditevent van de ondertekening legt de vingerafdruk vast die elke erkenning meedroeg (`drift_concerns_fingerprint`, `qc_concerns_fingerprint`, `import_incomplete_concerns_fingerprint`, `package_source_change_concerns_fingerprint`, `unreviewed_findings_concerns_fingerprint`; `null` voor een poort zonder bevindingen).
- Het snapshot bevriest de bevindingen zelf (de driftsecties, `sample_qc`, de importstatus, `package_source_change`, de lijst van poort 4), en het auditevent de groepen van de sample-QC. Zo is elke vingerafdruk uit het record na te rekenen. Het snapshot bevat zelf geen vingerafdruk: de bevindingen die het bevriest, zijn die waartegen de ondertekening de vingerafdruk controleerde.

**Waar in de code:** `concerns_fingerprint`, `_gate_refusal` en `_refuse_unless_shown` in `report_signout_service.py`; `ReportSignoutRequest` in `backend/app/schemas/traceability.py`; de dialogen in `frontend/src/pages/families/reportSignOut.tsx`.

## Het bevroren snapshot

| Veld | Inhoud |
| --- | --- |
| `family_id`, `assembly` | De familie en de referentie-assembly |
| `modules` | Het volledige annotatie- en referentiemanifest, per modaliteit |
| `software` | De build die het snapshot maakte (`app_version` en `git_sha`) |
| `drift` | Het aantal gecontroleerde classificaties van small variants en de lijst met drift (ook `no_snapshot`), met per drift wat verschoof |
| `structural_drift` | Hetzelfde voor de classificaties van SV's en CNV's |
| `sample_qc` | De volledige sample-QC |
| `sequencing_qc` | De sequencing-QC per sample en de grenzen waartegen ze beoordeeld werd |
| `import_incomplete` | Leeg (`null`) als de data volledig geïmporteerd is; anders de mislukte en de gelukte datasets, het tijdstip en de importjob |
| `import_unfinished` | Leeg (`{}`) als geen import halverwege stopte; anders per import de job, het begin, de datasets en welke ervan afgerond waren. Na een `overwrite` waarvan het terugzetten niet afraakte: elke dataset van de familie die nog opnieuw geïmporteerd moet worden, geen afgerond. Voor een losse upload of een verwijdering die niet afraakte: geen job, onder zijn sleutel `upload-…` of `deletion-…` |
| `package_source_change` | Leeg (`null`) als de pakketmap waaruit CoGA de bronbestanden van de familie toont, nog is zoals de referentielevering geïmporteerd werd (of de familie niet uit een levering kwam); anders die levering, de vingerafdruk waarmee ze geïmporteerd werd en die van nu, `changed` met de paden die erbij kwamen, verdwenen of wijzigden (elk tot 50, met het aantal) of `gone`, wanneer de bewaker het vond of `found_at_sign_out` als de eigen controle van de ondertekening het vond, en voor een verdwenen map die als bewust gearchiveerd bevestigd werd `archived` (wanneer, door wie en waarom) (poort 3b, REQ-TRACE-018) |
| `reported_variants` | Elke gerapporteerde small variant met klasse, criteria, tags, notitie en bevroren bewijs. Gerapporteerd is een variant waarvan de eigen review of de review van haar compound-heterozygoot paar de tag `report` draagt. Dat is de regel van de reviewtagfilter, waarmee ook het live rapport en *Show reported* in de kop van de NIPT-pagina ze tonen. Bij een variant die haar paar rapporteert, staat ook die paarreview erbij (`compound_het`: de partner, het gen, de classificatie, de tags en de notitie van het paar, en de fase) |
| `reported_structural_variants` | Elke gerapporteerde SV of CNV met classificatie, CNV-criteria, tags, notitie en bevroren bewijs |
| `class_shown` (in elke variant van de twee secties hierboven) | De klasse die de review van de variant toont, volgens de ene regel van de familiepagina (`review_pg_utils.review_class_label`: een kaal klassewoord in de tekst, anders de zwaarste ACMG-klassetag, anders de tekst); `null` als ze er geen toont. De backend berekent ze bij het ondertekenen uit de bewaarde review, nooit uit het verzoek. Een small variant bevriest ook haar classificatietekst zoals bewaard (`classification`), net als een SV. Het ACMG-record en de CNV-score blijven ernaast staan, zodat een klasse die de analist boven de ACMG-score koos, die score in het record houdt |
| `record_statement` | De zin die zegt wat het record is: de analytische ondertekening, waarnaar het EPD verwijst via de rapportcode |
| `variant_descriptions` | Elke gerapporteerde variant zoals de analist ze zag, één per callset of caller. Bij een small variant: de positie, het gen, het transcript (en of het MANE Select is), HGVS c. en p., de consequentie, en per sample het genotype met diepte, GQ, allel-dieptes en allelfractie. Bij een SV of CNV: het type, de kopiewijziging (verlies of winst), de positie, de lengte, de genen, de cytoband, de caller, en per sample het genotype en het kopieaantal (REQ-TRACE-007) |
| `gene_panels` | Elke genpanelversie die de zoekopdrachten van de familie toepasten: naam, id, lokale versie, aantal genen, bron en de release van de bron (PanelApp's versie van het panel, of de Monarch-release van het Mendeliome), wanneer CoGA die release importeerde, en wanneer en door wie ze het eerst werd toegepast (REQ-CARR-003) |
| `reference_data` | Naast de modules: per bron van de genreferentie de release en checksum die antwoordden voor de genen van de gerapporteerde varianten, en de klinische CNV-kennisbank (bron, de release die de bron vermeldt, en wanneer ze geladen werd) |
| `repeat_expansions` | Elke TRGT-call van de samples, met de status die CoGA er bij het ondertekenen aan geeft, de allelen, de drempels erachter en de catalogus (aantal loci, bronnen en een digest van de drempels). Per allel de geclassificeerde telling (`repeat_count`: die van TRGT met de telcorrectie van QA) en die van TRGT (`trgt_repeat_count`). Naast elk allel de waarschuwing dicht bij een grens zoals de repeatpagina ze toont (`count_unit_warning`: de telling, per kant de dichtstbijzijnde telling binnen de marge met een andere status, en de marge; leeg zonder waarschuwing). Per locus het register van QA over de telwijze van TRGT zoals `repeat_locus_count_units` het hield (`count_units`: de correctie `count_offset`, gevalideerd of niet, de reden, wie en wanneer; zonder register `count_offset: 0`, `validated: false` en de rest leeg). En de marge die gold (`count_unit_margin`) |
| `paraphase` | Elk Paraphase-resultaat van de samples, met de status die CoGA er bij het ondertekenen aan geeft, de kopieaantallen, de statusregel en kopie-norm van de regio en de catalogus (aantal regio's en een digest) |
| `pgt_analysis` | Alleen bij een PGT-familie (een familie met een embryo): de PGT-analyse bij de ROI, hetzelfde object waaruit de familiepagina haar embryo-indeling leest (`pgt_analysis.py`, `GET /families/{id}/pgt-analysis`). Het bevat de ROI en het venster van gelezen haplotypeblokken (de ROI en 1 Mb aan elke kant), het overervingsmodel zoals ingevuld en zoals toegepast, de rol, het geslacht en de status van elk lid, het ziekte-haplotype bij de ROI (of dat er geen gevonden werd) en de fasecorrecties van de ouders vlak bij de ROI. Per embryo: het geslacht; de indeling (geen bij een familie zonder ROI); of een *Uninformative* aan de eigen ontbrekende data ligt; beide indelingen als een X-gebonden recessieve indeling van een niet ingevuld geslacht afhangt; de homologen die het embryo bij de ROI draagt, met hun blokken; de blokgrenzen binnen 250 kb van de ROI (recombinaties); de faserings-QC bij de ROI zoals de ROI marker review ze telt over hetzelfde venster (informatieve sites, Mendel-fouten en hun graad, de sites per ouderkant, in totaal en links en rechts van de ROI apart); het oordeel over die QC tegenover de drempels van `pgt_embryo_qc.py` (`qc_verdict`: gehaald; niet gehaald, met elke maat, haar waarde en de drempel; of niet geteld, wat geen geslaagde QC is), met de ouderkanten waarvan de markers getoetst werden. Daarbij de invoer: het haplotypebestand van elk lid en wanneer het geladen werd, het aantal gelezen blokken met een SHA-256-digest ervan, de afstanden die de waarschuwingen gebruiken en de QC-drempels die golden (`rules.qc_thresholds`) (REQ-PGT-005). Een familie zonder embryo heeft deze sleutel niet; een monogene NIPT-familie alleen als een van haar embryo's eigen haplotypeblokken heeft (`embryo_has_haplotype_blocks`). De import leest een kind van onbekend geslacht met een onbekende of niet-aangedane status als embryo (zoals een NIPT-PED de foetus meestal geeft), en de NIPT-trio neemt dat embryo als foetus. De foetus heeft geen sequencingkolom en dus geen blokken |
| `nipt` | Alleen bij een monogene NIPT-familie (`signed_record_nipt.py`): de trio; de foetale fractie met interval, aantal sites, mediaan per site en oordeel; het de-novovenster; de categorieën; de filtertrechter en waarom cfDNA-calls de kwaliteitsfilter niet haalden; de kwaliteitscontroles met hun oordeel en de zin die het uitlegt (vaderschap, het foetale geslacht uit beide signalen, het plasmasample; `nipt_quality_checks.py`, dezelfde oordelen als op de NIPT-pagina); de dekking van elke capture-target en elk gen met een zwak target; zonder dekkingstabel per target de notities van de dekkingscontrole (`coverage_notes`, zie *De notities van de NIPT-dekkingscontrole* hieronder); elke variant met de tag `report` (op haar eigen review of op die van haar paar) zoals de NIPT-variantlijst hem classificeert (categorie, reads, kansen, vlaggen, zijn terugkeer in de andere cfDNA-samples van de assay, en voor een de-novokandidaat in het foetale venster zijn triage); elke gerapporteerde variant die de analyse niet classificeert, met de reden; het recessieve foetale risico van hun genen, zoals de recessieve weergave het over *Show reported* berekent; en waarmee de analyse liep (de dekkingstabellen, de artefactenlijst met een digest van haar allelen, en het model) |
| `coverage_tracks` | Elk dekkingsspoor van de leden, per sample en caller (`web` voor een dekkings-BED, uit een upload of een pakket; `wisecondorx`, `qdnaseq`, `hificnv`), met wat het bevat zoals de dieptelezers het lezen: `read_depth`, `log2_ratio` of `unknown`, volgens hun ene gedeelde regel `coverage_read_depth.coverage_track_content` over dezelfde records (bij een dekkings-BED wat ze bij het laden verklaarde, bij een andere caller wat de transformatie van zijn import ervan maakte). Per bestand de naam, het aantal rijen, wanneer en waarvandaan het geladen werd (`upload` voor een losse upload, `package` voor een pakketimport; leeg als het record het niet zegt), wat een dekkings-BED verklaarde en de transformatie van een andere caller (beslissing van de eigenaar van 2026-10-20). Alleen een spoor met leesdiepte wordt als diepte gelezen (de mtDNA-dekking en haar oordeel `mtdna.mean_depth`, de NIPT-dekkingscontrole zonder tabel per target); het record zegt dus op welk soort dekking een dieptecontrole steunde. Elke familie heeft deze sleutel, zonder dekkingsspoor als `{"tracks": []}`, zodat een spoor dat na het ondertekenen geladen wordt als wijziging telt. De dekkingstabellen per target van een NIPT-familie zijn een ander spoortype en staan in `nipt`, net als de notities van de NIPT-dekkingscontrole over deze sporen (`nipt.coverage_notes`) |
| `package_intake` | Het geleverde pakket waaruit de laatste pakketimport van de familie kwam, zoals het record van de familie het noemt (`families.metadata.package_import.delivery`): het id, de run van de pipeline, de SHA-256 van het pakketbestand van de pipeline, de geïmporteerde revisie en de SHA-256 van het geïmporteerde pakket; en elke correctie die vóór de import in CoGA aan dat pakket gemaakt werd (`import_delivery_revisions`), in volgorde van revisie: elk gewijzigd veld met zijn pad (`members.PROBAND.sex`), zijn soort (`pipeline_used` voor geslacht, ouders en koppels, waarmee de pipeline rekende; anders `laboratory`) en zijn waarde ervoor en erna, met de reden, wie en wanneer (beslissing van de eigenaar van 2026-11-10). Elke familie heeft deze sleutel, als `{"delivery": null}` voor een familie waarvan de laatste import niet uit een levering kwam, zodat een import na het ondertekenen als wijziging telt. De familiepagina toont dezelfde correcties (*Corrected at intake*) |
| `repeat_expansion_reviews`, `paraphase_reviews` | Elke review van een repeatlocus of een Paraphase-regio in de assembly van de familie die een classificatie of de tag *Report* draagt: de sleutel, de classificatie met haar label, de tags, of ze gerapporteerd is, de notitie, wie ze het laatst bewaarde en wanneer, de calls die ze bevroor, en of die nog de calls van de familie zijn (`drift`) (REQ-TRACE-017) |
| `finding_review_drift` | Hoeveel van die reviews getoetst werden, en elke review waarvan de calls veranderden, er niet meer zijn of niet te vergelijken zijn; poort 1 telt ze mee (REQ-TRACE-015) |
| `unreviewed_findings` | De lijst van poort 4: elk repeatlocus en elke Paraphase-regio met een afwijkende call en geen review, met elk afwijkend lid en zijn status, en een sectie die niet gelezen kon worden (REQ-TRACE-016) |
| `version`, `generated_at`, `signed_out_by` | De versie, het tijdstip en de ondertekenaar |
| `acknowledged_drift`, `acknowledged_qc`, `acknowledged_import_incomplete`, `acknowledged_package_source_change`, `acknowledged_unreviewed_findings` | Of de ondertekenaar de bevindingen van poort 1, 2, 3, 3b en 4 erkende. Alleen `true` als die poort zonder erkenning zou weigeren |
| `drift_acknowledgement_reason`, `qc_acknowledgement_reason`, `import_incomplete_acknowledgement_reason`, `unreviewed_findings_acknowledgement_reason` | De reden van elke erkenning; leeg (`null`) zonder erkenning |

De negen van `variant_descriptions` tot `package_intake` leest `signed_record_sections.py`, elk in een eigen savepoint (een deel van de transactie dat apart kan mislukken). Een sectie van één toepassing (`pgt_analysis` bij een PGT-familie, `nipt` bij een monogene NIPT-familie) noemt die toepassing in haar `RecordSection` (`application="pgt"`, `application="nipt"`). Ze wordt alleen voor zo'n familie gebouwd: een andere familie heeft die sleutel niet. De controle (`sign-out-check`) vergelijkt zo'n sectie als het record of de huidige data van de familie ze heeft (heeft maar één van beide ze, dan is dat een wijziging). Hebben geen van beide de sectie, dan vergelijkt de controle ze niet. Een sectie die niet gelezen kan worden, wordt bevroren als `{"unavailable": <een vaste zin>}`, nooit als lege sectie, en staat in `not_captured`. De tekst van de fout zelf, die waarden kan citeren, wordt niet bevroren.

**De genpanels.** Zonder eigen registratie zou niets bijhouden welk panel een zoekopdracht toepaste: de HTTP-toegangslog bewaart standaard alleen de sleutels van de query (`AUDIT_LOG_QUERY_STRING_MODE=keys`), schrijft in batches na het antwoord en kan uitgeschakeld worden. Daarom schrijft een zoekopdracht van de familie die een panel toepast, vóór ze draait, een klinisch auditevent `panel_applied`, de eerste keer dat ze die versie van het panel toepast. Dat geldt voor de lijsten van small variants en SV's en hun CSV-exports, de NIPT-varianten en de NIPT-dekking. Het event bevat het id, de naam, de lokale versie, het aantal genen en de release van de bron.

- Dezelfde versie opnieuw schrijft niets; een nieuwe versie (elke versie wordt gearchiveerd) of een nieuwe PanelApp-versie met dezelfde genen wel.
- De controle loopt een tweede keer onder het slot van de auditketen van de familie, zodat twee gelijktijdige zoekopdrachten het panel één keer vastleggen.
- Lukt het schrijven niet, dan faalt de zoekopdracht.
- Een panel in de variant explorer, die over families heen zoekt, wordt voor geen familie vastgelegd (`panel_use_audit.py`).

**De embryo-indeling.** De backend maakt ze (`pgt_embryo_calls.py`) met dezelfde regels als de haplotypesporen in de browser (`haplotypeRisk.ts`), die het risicolijntje en de risicostatus per lid tekenen. Beide antwoorden gelijk op de gevallen van `frontend/src/lib/__tests__/fixtures/pgtEmbryoCallCases.json`, die `test_pgt_embryo_calls.py` en `haplotypeRisk.test.ts` allebei draaien. De familiepagina toont de indeling die de backend geeft, en de ondertekening bevriest datzelfde object. Het record bevat dus de indeling die de analist zag, gemaakt door één implementatie uit opgeslagen data.

- Het record bevat per embryo het QC-oordeel (`qc_verdict`) en de drempels waartegen het getoetst werd (`rules.qc_thresholds`). De controle vergelijkt de sectie helemaal: een oordeel of drempel die sindsdien veranderde, telt als een wijziging.

**De klasse die elke gerapporteerde variant toont.** Het record bevriest per gerapporteerde variant de klasse die haar review toont (`class_shown`), met de classificatietekst ernaast (beslissing van de eigenaar van 2026-11-10). Het live rapport en de ondertekende versie tonen die klasse, *Not classified* als er geen is, en ernaast de ACMG- of CNV-score als die een andere klasse geeft (*VUS - class 3 (ACMG score: Likely pathogenic - class 4 · 8 pts)*); beide gebruiken `ReportClassification.tsx`. De motivatie noemt dan de klasse die de criteria geven apart van de getoonde klasse.

- De controle vergelijkt de getoonde klasse: een klasse die sinds de ondertekening veranderde, telt als een wijziging, ook als alleen de tekst van een small variant veranderde (*VUS* naast de tag van klasse 4 herschreven als *Likely benign*).

**De repeat-expansies en de telwijze van TRGT.** Het record bevriest de gecorrigeerde telling met die van TRGT ernaast, de waarschuwing dicht bij een grens die de repeatpagina naast een telling toont, en per locus het register van QA met zijn telcorrectie (hoofdstuk 8). Tellingen, statussen, waarschuwingen en register spreken elkaar nooit tegen: de familietabel leest het register in dezelfde query als de calls, en `signed_record_sections.py` bevriest precies dat register. Een locus dat na het ondertekenen gevalideerd, ingetrokken of gecorrigeerd wordt, telt als een wijziging van `repeat_expansions`.

**De notities van de NIPT-dekkingscontrole.** Zonder dekkingstabel per target leest de dekkingscontrole van de NIPT-pagina de dekkingssporen van het plasma, en noemt ze elk spoor dat ze niet als diepte leest, met de reden (hoofdstuk 8). De sectie `nipt` bevriest die notities woord voor woord (beslissing van de eigenaar van 2026-10-28), zoals de NIPT-pagina ze toont, met waarvoor de controle liep (`coverage_notes`: `gene` en `panel`, beide leeg, de `regions` en de `notes`). De ondertekende versie toont zo de notities die de analist zag, niet later opnieuw berekende notities.

- De controle hangt af van het gen of panel van de zoekopdracht op de NIPT-pagina, en dat krijgt de ondertekening niet mee. Ze draait de controle dus met de code van de pagina (`nipt_service.nipt_coverage_notes`) zoals de pagina ze zonder gen of panel draait, over de ROI van de familie, zoals `target_coverage` de controle van elke capture-target bevat. Zonder ROI loopt de controle over geen regio en noemt ze niets. Met een dekkingstabel per target is `coverage_notes` `null`, want dan leest de controle de targets.
- De ondertekende weergave toont de notities onder *Coverage tracks not read as depth*, met waarvoor de controle liep (*The coverage check ran without a gene or panel, over the family’s region of interest …*).
- De controle vergelijkt de sectie helemaal, de notities (ook als `null`) inbegrepen. Een spoor dat sindsdien geladen, verklaard of verwijderd werd, of een gewijzigde ROI, die de notities of de regio veranderen, telt als een wijziging.

**Een sectie toevoegen** vraagt vier stukken: een builder in `signed_record_sections.py` (deterministisch, JSON-veilig, in `RECORD_SECTIONS` en `build_record_sections`); het lezen in `signedReportRecord.ts` (`parseRecordSection`) met een label in `REPORT_SECTION_LABELS`; het tonen in `SignedFamilyReport.tsx` (`RecordSectionBlock`); en de beschrijving in `docs/clinical-traceability.md`. Zonder het tweede en derde stuk ontbreekt de sectie zonder spoor in de ondertekende weergave.

Bij het ondertekenen komen er de versie, het tijdstip, de ondertekenaar en de erkenningen met hun redenen bij. Zo is het snapshot aan **drie versie-assen** gebonden:

- **Software:** `app_version` en `git_sha`, bij het bouwen van de image vastgelegd.
- **Annotatie en pipeline:** het bevroren manifest en, per classificatie, het bevroren bewijs met de hash van de annotatieset (poort 1); bij een SV of CNV de hash van het bevroren bewijs, met de versies van de SV-callset erachter.
- **Referentie:** de assembly, de bron van de genloci die CoGA zelf laadde (GENCODE, of de UCSC-tabel als GENCODE niet lukte), de Monarch-release en de HPO-release (de release van de laatste import van de ontologie).

**De hash.** De inhoudshash is een SHA-256 over een vaste, op sleutel gesorteerde JSON-codering; lijsten worden vooraf op een stabiele sleutel gesorteerd. De klinische secties zijn deterministisch: dezelfde inhoud geeft dezelfde vingerafdruk. Daarop steunt de controle hieronder. De inhoudshash omvat ook de versie, het tijdstip en de ondertekenaar, en is dus per ondertekening uniek. Een opgeslagen hash wordt altijd herberekend over het snapshot zoals het opgeslagen werd.

**Los van ClickHouse.** Het snapshot bevat zelf de waarden die het nodig heeft. Een latere herbouw van ClickHouse of een nieuwe annotatieversie verandert het ondertekende record niet.

**Waar in de code:** `build_report_snapshot`; de codering in `backend/app/services/hash_chain.py`; de tabel `report_signouts` in `04_traceability.sql`.

### De rapportcode

Het klinische verslag in het EPD citeert het ondertekende record via zijn **rapportcode** (`backend/app/services/report_code.py`), bv. `7KQM2X-2-4PHT`. De code wordt berekend uit velden van het record die nooit veranderen. Geen kolom bewaart ze. De code bestaat uit:

- zes tekens uit de SHA-256 van de familie-ID, gelijk in elke versie van het record van de familie;
- de versie, in cijfers;
- drie tekens uit de eerste 15 bits van de inhoudshash, die de code aan de ondertekende inhoud binden;
- een controleteken over alle andere (het Damm-algoritme over GF(32)). Het vangt elk verkeerd getypt teken en elke verwisseling van twee naburige tekens: een fout overgenomen code wordt geweigerd en vindt nooit een ander record.

De tekens zijn Crockfords base 32 (cijfers en hoofdletters zonder I, L, O en U). Hoofd- of kleine letters, koppeltekens en spaties maken niet uit; een I of L leest als 1, een O als 0. Een code telt 13 tekens voor de versies 1 tot 9, en hoogstens 16 tot versie 9999.

**Opzoeken.** `GET /api/report-codes/{code}` geeft de familie, de versie, de ondertekenaar, het tijdstip en de inhoudshash van het record. Een tekst die geen rapportcode is, krijgt `422` (`report_code_invalid`) met de reden; dat volgt uit de tekst alleen. Een geldige code zonder record, een code van een familie buiten de projecten van de gebruiker en een code van een verwijderde familie krijgen allemaal dezelfde `404`; alleen de auditrij noemt de verborgen familie (REQ-SEC-001). In de app staat het opzoeken onder **Report codes** in het hoofdmenu (`/report-codes`, en `/report-codes/<code>` als link vanuit het EPD).

**Waar in de code:** `report_code.py`; `find_report_signout_by_code` en de uniciteit in `sign_out_report` (`report_signout_service.py`); `backend/app/routers/report_codes.py`; `frontend/src/pages/reports/ReportCodeLookupPage.tsx`.

### Toont de rapportpagina het ondertekende rapport?

Alleen de ondertekende versie is het ondertekende rapport. De rapportpagina heeft twee weergaven:

- **Een ondertekende versie** (`?version=N`). Een ondertekende casus opent op de laatste. `SignedFamilyReport.tsx` tekent ze uitsluitend uit het bevroren snapshot dat `GET /families/{id}/report/sign-outs/{versie}` teruggeeft; `signedReportRecord.ts` leest het sectie per sectie. De pagina toont:
    - de versie, de ondertekenaar, het tijdstip, de **rapportcode**, de zin die zegt wat het record is, de inhoudshash en of die nog klopt (`verified`), en de build die ondertekende;
    - per gerapporteerde variant de beschrijving zoals de analist ze zag, de klasse, de aanvaarde criteria, het bevroren bewijs, de tags en de notitie; bij een variant die haar compound-het-paar rapporteert ook die paarreview, onder *Compound-het pair*. De klasse is die het record als getoond bevroor (`class_shown`), met de ACMG-score ernaast als die een andere klasse geeft (zie *De klasse die elke gerapporteerde variant toont* hierboven);
    - de SV's met hun beschrijving en ClinGen-CNV-criteria, de klasse zoals de SV-pagina ze toont en de CNV-score ernaast als die een andere klasse geeft, nooit als klasse;
    - bij een PGT-familie de embryo-indeling (*PGT embryo results*): per embryo de indeling, de homologen bij de ROI zoals het spoor ze noemt (P1, P2, M1 en M2), de waarschuwingen, de QC, het oordeel over die QC en de drempels die golden;
    - de toegepaste genpanels;
    - de repeat-expansies, met naast de call elke telling dicht bij een grens (*⚠ Near a boundary: 53 is within 3 repeats of 55 (Premutation)*), een door QA gecorrigeerde telling met die van TRGT tussen haakjes (*18 (TRGT 19)*), en per locus de telwijze van TRGT zoals QA ze vastlegde (*TRGT’s counting units: not validated (no QA record)*, of gevalideerd of niet, *corrected to TRGT’s count −1*, met de reden, wie en wanneer);
    - de Paraphase-resultaten;
    - de dekkingssporen (*Coverage tracks*): per sample en caller leesdiepte, een log2-ratio of onbekend, met het bestand, waarvandaan het kwam (een losse upload of een pakket) en wanneer het geladen werd;
    - het geleverde pakket en zijn correcties bij de intake (*Package intake*): de run, het pakketbestand en de geïmporteerde revisie, en elke correctie met wat ze veranderde (een wijziging die de pipeline gebruikte gemarkeerd), waarom, door wie en wanneer;
    - de releases van de referentiedata;
    - de gerapporteerde repeat-expansies en Paraphase-bevindingen (*Reported repeat expansions and Paraphase findings*): per bevinding het gen en de ziekte, de call van elk lid zoals de review ze bevroor met de status van CoGA, de classificatie en de notitie van de analist, en de markeringen *differs from CoGA's status* en *Changed since review*;
    - de drift, ook die van de SV- en CNV-classificaties en van de repeat- en Paraphase-reviews;
    - de sample-QC, de sequencing-QC, de afwijkende repeat- en Paraphase-calls zonder review, en de importstatus met de erkenningen;
    - in de voettekst de bevroren versies met de referentiedata, de dekkingssporen op één regel (*Coverage tracks as signed: S1 coverage BED (package) — read depth; …*) en de rapportcode.

    Meteen na het ondertekenen zegt de pagina, alleen op het scherm, *Signed out as version N. Report code …*.

- Wat geen snapshot bevat, zegt die pagina, voor het rapport en per variant: de populatiefrequenties en in-silicovoorspellingen, de segregatie, de gen- en fenotypecontext, het auditspoor en de pipelinesettings. Ze vult het nooit aan met huidige data. Een sectie die bij het ondertekenen niet gelezen kon worden, of die de pagina niet kan lezen, staat er als *Not captured*, met de reden, nooit als leeg. Kan de pagina een lijst in een sectie niet lezen (een waarde die geen lijst is, of een element dat niet van zijn soort is), dan laat ze die weg en zegt de sectie dat (*Part of … cannot be read here*; `parseRecordSection` in `signedReportRecord.ts`).
- De printknop drukt die weergave af. Bovenaan de afdruk staat een melding bij een record dat niet meer bij zijn inhoudshash past, bij een versie die door een latere vervangen is, en bij een versie waarvan niet vaststaat dat ze de laatste is.
- **Het live rapport** (`?view=live`) toont de **huidige** data, met per variant de klasse die haar review op de familiepagina toont en de ACMG- of CNV-score ernaast, zoals de ondertekende versie. Hier wordt de casus ondertekend; daarna toont de pagina de nieuwe versie. Het is nooit het ondertekende rapport, ook niet als de inhoud overeenkomt ("This is the live report, not signed version N"), en elke afdruk ervan krijgt bovenaan een melding. Zolang het ondertekeningsrecord laadt of niet geladen kon worden, biedt het geen ondertekening aan.
- Een controle (`GET /families/{id}/report/sign-out-check`) bouwt het snapshot zoals het nu zou worden bevroren, en vergelijkt het sectie per sectie met de laatste ondertekende versie. Zo zegt het live rapport of het nog overeenkomt, welke delen wijzigden, of dat de controle niet kon draaien. Op de laatste ondertekende versie zegt dezelfde controle, alleen op het scherm, of de data van de familie sindsdien veranderde. Elke sectie wordt helemaal vergeleken.
- Het bevroren record zelf is in beide weergaven als JSON te downloaden.
- Mislukt tijdens het ondertekenen een opzoeking (bv. de QC-grenzen of de versie van de assembly, de genloci, Monarch of HPO), dan gaat de ondertekening door, maar bevriest het snapshot dat deel expliciet als *niet beschikbaar*, met de reden. Het auditevent en de ondertekende versie noemen die delen (`not_captured`, ook in het antwoord van `GET …/sign-outs/{versie}`).
- Ook een sectie die CoGA sindsdien anders berekent, leest als gewijzigd. De ondertekende versie zelf toont wat er ondertekend werd. Wie de nieuwe beoordeling wil vastleggen, ondertekent opnieuw.

- CoGA leest één snapshotformaat: het formaat dat het zelf schrijft. Elk record bevat elke sectie en elk veld (een sectie van één toepassing alleen bij de families van die toepassing), en de controle vergelijkt elke sectie helemaal. De verificatie hasht het record zoals het bewaard is.
- Het snapshot bevriest de variantbeschrijving, de toegepaste genpanels, de releases van de referentiedata en de repeat- en Paraphase-resultaten (beslissing van de eigenaar van 2026-10-14), en ook wat elk dekkingsspoor bevat en, bij een PGT-familie, de embryo-indeling (beslissing van de eigenaar van 2026-10-20). De gen- en fenotypecontext bevriest het niet.
- Geen van de QC-drempels houdt een embryo-indeling tegen. Haalt de faserings-QC bij de ROI haar drempels niet (minstens 50 informatieve sites, een Mendel-foutgraad van hoogstens 2 %, minstens 6 markers aan elke kant van de ROI voor elke ouderkant die de indeling leest), of werd ze niet geteld, dan staat er een waarschuwing naast de indeling, op de familiepagina en in de ondertekende versie. De indeling blijft staan, de ondertekening wordt niet tegengehouden en de analist beslist (beslissing van de eigenaar van 2026-10-20); het record bevriest het oordeel met de drempels. Die drempels zijn voorlopig: de labodirecteur en QA moeten ze nog bevestigen. Eén andere maat wijzigt de indeling wel: een embryo dat bij de ROI beide homologen van een ouder draagt, wordt *Uninformative* (hoofdstuk 8).

## Append-only, hash-geketend auditspoor

CoGA houdt twee auditlogs bij:

- **De HTTP-toegangslog** `audit_log_events`: elk verzoek, met methode, pad, status en gebruiker (hoofdstuk 7). Ernaast staat de log van de UI-events `ui_events` (klikken en navigatie in de browser).
- **Het klinische auditspoor** `clinical_audit_events`: betekenisvolle klinische handelingen, met per veld de waarde ervoor en erna. Het spoor wordt in dezelfde transactie geschreven als de wijziging zelf, zodat het nooit uit de pas loopt met de data. Het legt vast:
    - **Reviews** van small variants, SV's en CNV's: classificeren, een tag zetten of weghalen, een notitie wijzigen, een review leegmaken of wissen. De regel voor de klasse legt het hele bewaarde record vast: per criterium de sterkte of, bij een CNV, de punten, of het aanvaard is, het bewijs en of het een suggestie was, met het puntentotaal (en bij een CNV de ClinGen-soort). Zo staat ook een gewijzigd criterium in het spoor als de klasse gelijk blijft. Regels van SV's en CNV's dragen `metadata.modality = "sv"`, zodat hun id nooit voor dat van een small variant wordt gehouden.
    - **Een compound-heterozygoot paar** (actie `compound_het_pair`): een opslag die een paar vastlegt, wijzigt of opbreekt, schrijft per gewijzigd paar één regel, met het paar ervoor en erna (partner, gen, classificatie, tags, notitie en fase). De regel staat bij de variant waarvan de review het paar droeg en noemt de partner.
    - **Het annotatiemanifest vervangen**, en **ondertekenen**.
    - **Een genpanelversie** die voor het eerst wordt toegepast in een zoekopdracht van de familie (`panel_applied`).
    - **De lijst van NIPT-artefacten:** elke wijziging (een artefact toevoegen, wijzigen of verwijderen, de lijst automatisch aanvullen of een lijst importeren), want ze verandert welke varianten elke NIPT-analyse van die assay wegfiltert (hoofdstuk 15).
    - **De fenotypes van een familie:** per HPO-term die een analist, superuser of beheerder toevoegt, wijzigt of verwijdert een regel `phenotype`, met het lid, de term en zijn label, de status, het begin, het bewijs, de bron en de notitie ervoor en erna, en een samenvatting van de wijziging. Een import die fenotypes verandert, schrijft als handeling van wie ze startte één regel `phenotype` per lid wiens fenotypes ze toevoegde of wijzigde, met het pakket, het fenotypebestand en de importjob; een import die geen fenotype verandert, schrijft niets. Net zo voor het herstel na een mislukte overschrijving dat de fenotypes terugzet, een mislukte import die de familie die ze aanmaakte weer verwijdert, en een PED-upload of de Family Builder die een familie vervangt. Die laatste noteren de fenotypes die met de familie verdwijnen, net vóór de regel `family_deleted` van de familie.
    - **De stamboom en de familieleden** (actie `pedigree`): het aanmaken van de familie; een bewerking van de structuur of van leden (geslacht, rol, aangedaan-status, dragerschap, ouders, een nieuwe naam, een lid inactief maken); een pakketimport die een lid een andere aangedaan- of dragerstatus geeft; het wissen van een sample met zijn data door een beheerder. De regel bevat elk gewijzigd lid en, als die wijzigden, de koppels en de verwanten van onbekende graad, ervoor en erna. Hij noemt de structuurversie die de wijziging bewaarde.
    - **Het verwijderen van een familie** (actie `family_deleted`), door een beheerder met haar data, door een PED-upload of handmatige familie die haar met overwrite vervangt, of door een pakketimport die haar aanmaakte en niets importeerde. Het is de laatste regel van haar spoor: wie ze verwijderde, haar samples, haar laatste structuurversie en wat met haar verdween (de varianten, de reviews, de repeats, de tracks en de bestanden). De regel wordt in de transactie van de verwijdering geschreven, vóór de familierij verdwijnt; een verwijdering die mislukt, legt dus niets vast. Het spoor, de ondertekeningen en de structuurversies blijven daarna bestaan, losgemaakt van de familie en controleerbaar onder haar familienaam.

Beide zijn in de databank **append-only**: een trigger blokkeert wissen en wijzigen. De enige uitzondering is het op `NULL` zetten van een verwijzing naar een account of familie die verwijderd wordt; de gedenormaliseerde velden (kopieën als tekst) bewaren dan wie het was. Dezelfde bescherming geldt voor `ui_events`, `report_signouts`, `integrity_anchors`, `qc_threshold_changes` en `family_structure_versions`. Die laatste is de geschiedenis van de stamboom: één versie per wijziging, met de hele stamboom zoals hij toen was. Het wissen van een familie wist haar structuurversies niet; ze blijven, losgemaakt van de familie, onder haar familienaam.

**Waar in de code:** de triggers in `backend/db/schema/postgres/04_traceability.sql`; het schrijven in `backend/app/services/clinical_audit_service.py`.

### De hash-keten

Een trigger houdt de normale applicatie tegen, maar een databankgebruiker met genoeg rechten kan een trigger uitschakelen. Daarom ligt over `clinical_audit_events`, `report_signouts` en `family_structure_versions` een **hash-keten**. Elke rij krijgt een `row_hash`: een SHA-256 over haar eigen onveranderlijke inhoud **plus** de `row_hash` van de vorige rij (de eerste rij gebruikt de tekst `GENESIS`). Wijzig je een rij, dan klopt haar hash niet meer; wis of verplaats je er een, dan wijst de volgende rij niet meer naar de juiste voorganger.

- De keten loopt **per familie**, op de onveranderlijke familienaam (`family_identifier`), niet op de UUID die bij verwijderen op `NULL` gaat. Zo blijft de ondertekende geschiedenis van een verwijderde familie controleerbaar.
- Een familie die daarna opnieuw onder dezelfde naam wordt aangemaakt (bv. door haar met overwrite te vervangen), schrijft verder op dezelfde ketens.
    - Haar ondertekeningen nummeren door: na de versies 1 en 2 van de verwijderde familie is haar eerste ondertekening versie 3. De keten van de ondertekeningen volgt de versienummers; met een nummering per familierij zou de nieuwe familie opnieuw bij versie 1 beginnen, en zou de controle van de keten falen alsof eraan geknoeid was.
    - Per familienaam is elke versie uniek (`uq_report_signouts_family_identifier_version`). Een familienaam met een versie, en dus een rapportcode, wijst zo altijd één ondertekend record aan.
    - Haar structuurversies nummeren per familierij wel opnieuw vanaf 1. Het auditspoor houdt ze uit elkaar: elke `pedigree`-regel van de nieuwe familie, en haar `family_deleted`-regel, noemt hoeveel structuurversies de verwijderde families vóór haar op de keten zetten (`deleted_family_versions`). Haar eerste regel zegt het ook in de samenvatting (*Made again under this family ID, after 2 structure versions of a deleted family: its own start again at 1.*).
- De wijzigingen aan de NIPT-artefactenlijst horen bij geen familie en vormen een eigen keten, onder de naam `system:nipt-artifacts`. Zo ook het register van de telwijze van TRGT per repeatlocus (hoofdstuk 8), onder `system:repeat-loci`.
- De gehashte inhoud **laat de verwijzingen weg** die op `NULL` mogen gaan, zodat een toegelaten verwijdering de keten niet breekt.
- Bij elke lezing van een ondertekening wordt de inhoudshash opnieuw berekend; een verschil wordt als fout gelogd ("possible tampering"). Een beheerder kan de keten van een familie ook volledig laten nalopen (`GET /api/admin/integrity/verify`, met `table` gelijk aan `clinical_audit_events`, `report_signouts` of `family_structure_versions`).
- De structuurversies hashen hun inhoud (versie, structuurhash, de hele stamboom en de metadata), maar niet wie de wijziging maakte: dat staat in de `pedigree`-regel van het klinische auditspoor, die zelf geketend is. Over `ui_events` en `audit_log_events` ligt geen keten: een UI-event is wat de browser over zichzelf meldt, geen klinische handeling.

**Eerlijke reikwijdte.** De keten is **tamper-evident, niet tamper-proof**: ze maakt manipulatie zichtbaar, niet onmogelijk. Ze betrapt iedereen die de keten niet kan herberekenen. De eigenaar van de tabellen kan echter een trigger uitschakelen, een rij wijzigen en de hashes van die rij en alle volgende opnieuw berekenen. Zolang de app als eigenaar draait (hoofdstuk 2), is dat de app zelf. Daarvoor dienen de ankers hieronder. Formuleer het tegenover een regulator dus als "tamper-evident tegen een tegenstander die alleen de databank heeft, tussen bewaarde ankers", nooit als "tamper-proof" of "onveranderlijk".

**Waar in de code:** `backend/app/services/hash_chain.py`; de kolommen `row_hash` en `prev_hash` in `04_traceability.sql`.

## Integriteitsankers

Een anker maakt ook een herberekende keten zichtbaar. Bij het maken van een anker doet `create_integrity_anchor` drie dingen:

1. de **kop van elke keten** vastleggen (per familie, voor `report_signouts`, `clinical_audit_events` en `family_structure_versions`: de lengte en de laatste `row_hash`);
2. die koppen samenvatten in één hash, die aan het vorige anker ketenen, en het geheel **ondertekenen** met een Ed25519-sleutel die in de configuratie staat, **nooit in de databank**;
3. het anker append-only opslaan in `integrity_anchors`.

Wie de sleutel niet heeft, kan een keten herberekenen, maar geen geldig ondertekend anker vervalsen. Een controle vergelijkt de huidige ketens met het laatste anker, of loopt de hele ankerketen en alle handtekeningen na, en meldt `ok`, `diverged`, `chain_broken`, `signature_invalid`, `unknown_key` of `unverifiable_unsigned`, en `no_anchor` zolang er nog geen anker is.

**Wanneer ontstaat een anker?** Een beheerder of een externe planner roept `POST /api/admin/integrity/anchor` aan; de controles zijn `GET /api/admin/integrity/anchor/verify` en `…/verify-chain`. De code plant dit niet in en er is geen scherm voor: hoe vaak een anker wordt gemaakt, is een procedureafspraak.

**Grenzen.** Een ankersleutel die in verkeerde handen valt (bv. bij een gecompromitteerde server), doorbreekt de bescherming; daartegen zou een hardwaresleutel (HSM) nodig zijn. Het wissen van de *laatste* ankers is alleen te zien met een kopie buiten de databank. Die export naar een externe opslag is voorzien maar nog niet gebouwd (`export_anchor` doet nog niets).

**Waar in de code:** `backend/app/services/integrity_anchor_service.py`; de tabel en trigger in `04_traceability.sql`; de rechten in `05_grants.sql`.

### De variantopslag in ClickHouse

De ankers dekken de Postgres-ketens. De variantopslag in ClickHouse bewaakt een aparte monitor. Kort na het opstarten, en daarna op een vast interval, controleert hij de varianttabellen van elke assembly. Bij een beschadigde of ontbrekende tabel schrijft hij een fout naar de log, bedoeld om een waarschuwing te laten afgaan vóór gebruikers er last van hebben.

- De server bewaart het laatste resultaat per assembly, met het tijdstip van de controle, in het geheugen. Na een herstart is er geen resultaat tot de eerste controle.
- `GET /api/admin/clickhouse/variants/integrity-monitor` toont het aan beheerders; de pagina voor ClickHouse-onderhoud toont het per assembly (hoofdstuk 15).
- Kon een controle niet lopen, dan staat dat er, in plaats van het vorige resultaat; de fout zelf staat in de log.
- Een beheerder start zelf een controle via `GET /api/admin/clickhouse/variants/{assembly}/integrity`.

**Wat de controle beoordeelt.** Per varianttabel geeft ClickHouse zelf zijn oordeel over de hele tabel, over elk actief onderdeel. Alleen van een tabel die niet in orde is, leest CoGA de onderdelen één voor één, om de kapotte te noemen. Een tabel zonder rijen is in orde. Een resultaat dat niet te lezen is, geldt nooit als in orde. Dezelfde controle bewaakt het herbouwen van de gen-index van de small variants: de nieuwe index komt pas in gebruik als al zijn onderdelen in orde zijn.

**Waar in de code:** `backend/app/services/clickhouse_integrity_monitor.py`; de controle zelf in `check_clickhouse_variant_integrity` en `_check_table` (`backend/app/services/clickhouse_variant_storage.py`).

## De volledige traceerbaarheidsketen

1. **Ruw bestand → hash.** Elk bronbestand staat in `raw_import_files`, met zijn SHA-256 (hoofdstuk 6).
2. **Annotatiemanifest.** De tool- en databankversies uit de VCF-headers staan per familie in `family_annotation_manifest` (hoofdstuk 6); een vervanging door een beheerder komt in het klinische auditspoor.
3. **Variant in ClickHouse.** Elke variant draagt de hash van de annotatieset waarmee hij werd geannoteerd (hoofdstuk 3).
4. **Classificatie en bewijs.** Elke ACMG-classificatie van een small variant bevriest de annotatieversie, de hash van de annotatieset, het bewijs van het primaire transcript (ClinVar, consequentie, frequenties, in-silicoscores, bij missense de missense-Z met zijn bron) en het tijdstip. Elke CNV-classificatie bevriest het bewijs van de SV, een hash van zijn annotatie en de versies erachter (hoofdstuk 10).
5. **Poorten.** Vóór het ondertekenen worden de assembly, lopende schrijfacties, de drift, de sample-QC, een onvolledige import en afwijkende repeat- en Paraphase-calls zonder review gecontroleerd. De assembly en lopende schrijfacties zijn een harde weigering; de andere bevindingen moeten met een reden erkend worden.
6. **Bevroren rapport.** Het snapshot (manifest, softwareversie, drift, QC, gerapporteerde varianten en wat de analyse bekeek; zie *Het bevroren snapshot*) wordt gehasht en append-only opgeslagen als nieuwe versie, met een rapportcode die uit het record berekend wordt.
7. **Keten en anker.** De ondertekening komt in de hash-keten van de familie, er komt een `sign_out`-regel in het klinische auditspoor, en een ondertekend anker kan de ketenkoppen extern vastleggen.
8. **Klinisch verslag (buiten CoGA).** Het verslag wordt opgesteld en medisch ondertekend in het EPD, en verwijst 1-op-1 naar de ondertekende versie, met haar rapportcode. Dat deel valt buiten het hulpmiddel: CoGA controleert het niet; met de rapportcode vindt men de versie terug.

Zo loopt een auditor van de hash van het ruwe bestand, via de versies en het bewijs achter elke classificatie, tot een ondertekende analyse waarvan de integriteit extern te controleren is, en waarnaar het klinische verslag in het EPD verwijst.

## IVDR-koppeling

Deze keten is de technische invulling van de eis tot traceerbaarheid onder de IVDR (in-house IVD, Artikel 5(5); device boundary "geannoteerde VCF → afgesloten, bevroren analyse in CoGA; het klinische verslag wordt opgesteld en medisch ondertekend in het EPD en verwijst 1-op-1 naar die analyse"; het EPD valt buiten het hulpmiddel). Het ontwerp staat in `docs/clinical-traceability.md`, de herkomst van annotaties in `docs/annotation-provenance.md`. Voor het regelgevende dossier: `docs/regulatory/`, met name TF-06 (risicobeheer, o.a. de gevaren H4, H12 en H15) en TF-09 (verificatie en validatie).

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/families_reports.py` | Manifest, drift, klinische audit, sample-QC, ondertekenen |
| `backend/app/services/report_signout_service.py` | Ondertekenen, snapshot, poorten, hashes en keten |
| `backend/app/services/hash_chain.py` | Vaste JSON-codering, SHA-256, de keten en haar controle |
| `backend/app/services/clinical_audit_service.py` | Het klinische auditspoor |
| `backend/app/services/family_structure_audit.py` | De structuurversies van de stamboom, hun keten en de `pedigree`-regels |
| `backend/app/services/integrity_anchor_service.py` | Ondertekende ankers en hun controle |
| `backend/app/services/classification_drift_service.py` | Drift |
| `backend/app/services/structural_variant_evidence.py` | Het bewijs van een CNV-classificatie en zijn drift |
| `backend/app/services/annotation_manifest_service.py` | Het annotatie- en referentiemanifest |
| `backend/app/services/clickhouse_integrity_monitor.py` | Bewaking van de variantopslag |
| `backend/db/schema/postgres/04_traceability.sql` · `05_grants.sql` | De tabellen, triggers en rechten |
| `frontend/src/pages/families/FamilyReportPage.tsx` · `FamilyNiptReportPage.tsx` | De rapportpagina's |
| `frontend/src/pages/families/SignedFamilyReport.tsx` · `signedReportRecord.ts` | Een ondertekende versie, getekend uit haar bevroren snapshot |
| `frontend/src/pages/families/ReportClassification.tsx` · `reportNarrative.ts` | De klasse van een gerapporteerde variant met de score ernaast, live en ondertekend |
| `frontend/src/lib/appVersion.ts` · `frontend/src/lib/deviceLabel.ts` | De draaiende build en het label in de voetteksten |
