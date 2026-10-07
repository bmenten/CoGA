# 10. Variant-tagging & semi-automatische ACMG-classificatie

Dit hoofdstuk beschrijft hoe een analist in CoGA varianten *labelt* (taggen) en *classificeert*, en hoe de software daarbij helpt met een **semi-automatische ACMG/AMP-classificator**. CoGA classificeert niet zelf: de software beoordeelt elk criterium vooraf uit de beschikbare variant-, familie- en gendata en zet het op een puntenschaal, maar de reviewer kan *elk* criterium aanpassen. De klasse en het puntentotaal worden altijd **op de server herberekend**, het gebruikte bewijs wordt **bevroren**, en verandert dat bewijs later, dan meldt CoGA **drift**, wat het ondertekenen tegenhoudt (hoofdstuk 11). De regels per criterium staan in de app: *Docs → Semi-automatic ACMG classification* (`frontend/src/content/docs/acmg-classification.md`).

Enkele begrippen:

- **ACMG/AMP-criteria:** een gestandaardiseerde set bewijsregels (Richards et al., 2015) met codes als `PVS1`, `PM2` of `BA1`. Een `P` wijst op pathogeen bewijs, een `B` op benigne bewijs.
- **Tag:** een label aan een variant (bv. "Report" of "Send for validation").
- **Snapshot:** een bevroren kopie van de gegevens zoals ze op een bepaald moment waren.

## Twee aparte eigenschappen: fenotype en dragerschap

CoGA houdt per familielid twee losse eigenschappen bij:

- **Klinische status** (`clinical_status`): `unknown`, `unaffected` of `affected`: heeft de persoon de aandoening?
- **Dragerschap** (`carrier_status`): `unknown`, `not_carrier` of `carrier`: draagt de persoon het risicoallel, ziek of niet?

Die scheiding is klinisch belangrijk: bij een recessieve aandoening is een ouder vaak drager maar niet aangedaan. De segregatieregels van de ACMG-evaluator lezen beide eigenschappen.

## Deel 1 — Taggen

### Wat wordt vastgelegd

Per variant en per familie legt een review vast: de **klasse** (bv. "VUS - class 3"), een lijst **tags** en een **notitie**. Per tag wordt ook bewaard *wie* hem zette en *wanneer*.

**Gelijktijdig bewerken.** Slaat een reviewer op tegen een review die intussen door iemand anders werd gewijzigd, dan weigert de server (`409`) en stuurt hij de actuele versie mee, zodat niemand ongemerkt het werk van een ander overschrijft. Dat geldt voor small variants en structurele varianten.

**Waar in de code:** `backend/app/services/small_variant_review_pg.py`; de controle op gelijktijdige wijzigingen in `_raise_on_stale_review` (`backend/app/services/review_pg_utils.py`).

### Welke tags bestaan

- **Systeemtags** zijn ingebouwd en niet te wijzigen: samenwerkingstags (bv. `review`, `send_for_validation`, `validated`, `report`, `excluded`) en de klassetags (`acmg_class_1` tot `acmg_class_5`, de VUS-niveaus `acmg_vus_hot`, `acmg_vus_warm` en `acmg_vus_cold`, en `secondary_finding`).
- **Eigen tags** maakt een beheerder aan. Ze gelden overal (`global`) of voor één project (`project`), en een projecttag kan met extra projecten gedeeld worden.

Bij het opslaan controleert de backend dat elke tag die de review *toevoegt* bestaat en in deze familie gebruikt mag worden; een onbekende tag geeft `400`. Een tag die de review al draagt, blijft staan, ook als hij intussen verwijderd is: de snelknoppen en de reviewdialoog sturen de opgeslagen tags mee terug, en anders zou elke volgende opslag van die variant mislukken, ook die waarmee de tag weggehaald wordt.

Een tag wordt in reviews, bewaarde filterpresets en de audit trail aangeduid met zijn **sleutel** (`key`). Die ligt vast bij het aanmaken, afgeleid van het label; hernoemen wijzigt alleen het label, zodat de tag op elke review en in elke filter blijft staan. Een label dat leest als dat van een andere actieve tag of van een systeemtag wordt geweigerd (`409`). Krijgt een nieuwe tag het label van een hernoemde of verwijderde tag, dan krijgt hij een genummerde sleutel (bv. `probe_x_2`) en neemt hij geen reviews van de oude tag over.

Eigen tags aanmaken, wijzigen of verwijderen mag alleen een beheerder (`admin` of `superuser`). Verwijderen zet een tag op inactief in plaats van hem te wissen: de reviews die hem dragen tonen hem nog, met zijn label en gemarkeerd als *(deleted)*, en hij kan er weggehaald worden, maar aan geen enkele review meer toegevoegd. Het beheerscherm staat onder Administratie (`frontend/src/pages/admin/AdminVariantTagsPage.tsx`).

**Waar in de code:** `backend/app/services/small_variant_review_tags.py`; de tabellen `small_variant_tag_definitions` en `small_variant_tag_definition_project_links` in `03_assay.sql`.

### Filterpresets en de lichte reviewdialoog

Een analist kan zijn filterinstellingen bewaren als **preset**, voor één familie of voor al zijn families; alleen de eigenaar kan een preset verwijderen. Naast de volledige ACMG-classificator is er een **lichte reviewdialoog** om een klasse te kiezen, tags aan of uit te zetten en een notitie te schrijven, met de prioriteitsscore (hoofdstuk 12) als hulp.

**Waar in de code:** `backend/app/services/small_variant_review_presets.py`; `frontend/src/pages/families/SmallVariantReviewDialog.tsx`.

## Deel 2 — De ACMG-classificator voor small variants

### Beslissingssteun, geen automaat

Vanuit een variant opent **ACMG classify** een dialoog die:

1. de criteria vooraf beoordeelt uit de gegevens die CoGA al heeft;
2. elk criterium in één van vier toestanden zet;
3. de analist elk criterium laat bevestigen, aanpassen of verwerpen;
4. de aanvaarde criteria op een puntenschaal zet;
5. bij het opslaan alles **op de server** herberekent.

### De vier toestanden

| Toestand | Weergave | Betekenis |
| --- | --- | --- |
| **Applies** | aangevinkt, groen | De data steunt het criterium duidelijk; het telt mee |
| **Consider** | oranje stip, niet aangevinkt | Relevant signaal, niet doorslaggevend |
| **Argues against** | rood kruis, niet aangevinkt | De data wijst de andere kant op (bv. een benigne voorspelling bij PP3) |
| **Not applicable** | grijs, doorgestreept | Kan voor dit varianttype niet gelden, maar blijft aanklikbaar |

Alle vier zijn aan te passen: een klik op een criterium zet het altijd aan of uit.

**Een geclassificeerde variant opnieuw openen.** De dialoog toont dan elk opgeslagen criterium met zijn toestand, sterkte en bewijs; de evaluator vult alleen ontbrekend bewijs aan en zet de criteria die nooit werden opgeslagen in hun eigen toestand. Opnieuw opslaan zonder wijziging houdt de criteria dus zoals ze waren. Elke lijst van small variants (de tabel en de kaarten, het rapport, de NIPT-kandidaten, de mtDNA-analyse) levert de review met hetzelfde ACMG-record als het lezen van die ene review (`_fetch_review_rows_for_variants` in `small_variant_review_repository.py`).

### Wat de evaluator vooraf beoordeelt

De evaluator leest de variant, en waar beschikbaar het genprofiel (ClinGen-dosage, overervingswijze uit GenCC, fenotypes van het gen), de HPO-termen van de proband en de genotypes in de familie. Hij verandert niets; hij stelt alleen voor. De belangrijkste regels:

| Criterium | Bron | Regel (samengevat) |
| --- | --- | --- |
| **PVS1** | gevolg, LOFTEE, ClinGen-dosage | Een voorspeld verlies van functie (stopcodon, frameshift, canonieke splicing, …). *Very strong* als LOFTEE `HC` zegt én het verliesmechanisme bewezen is; anders *strong*, of *consider* als het mechanisme niet bevestigd is |
| **PM2**, **BA1**, **BS1**, **BS2** | gnomAD | Afwezig of zeer zeldzaam → PM2 (*supporting*); zeer frequent → BA1, dat op zichzelf de variant benigne maakt; frequent → BS1; homozygoten aanwezig → BS2 |
| **PP2** | gnomAD missense-Z | Missense in een gen dat weinig missense verdraagt |
| **PP3** / **BP4** | REVEL, SpliceAI, AlphaMissense | De voorspellers bepalen de sterkte; het ene criterium telt als tegenargument voor het andere |
| **BP7** | gevolg en SpliceAI | Synoniem zonder voorspelde splice-impact |
| **PP5** / **BP6** | ClinVar | ClinVar zegt pathogeen → PP5; benigne → BP6. *Conflicting classifications of pathogenicity* is geen van beide: geen PP5 en geen BP6 |
| **PP4** | Monarch-fenotypescore of HPO-overlap | Hoe specifiek het fenotype bij het gen past |
| **PM6**, **PP1**, **BS4** | genotypes in de familie | De novo (afwezig bij beide ouders) → PM6. De ouders zijn de vader en moeder die de stamboom aan de proband koppelt, niet de leden met die rol: ook een grootouder heeft de rol vader of moeder. Heeft een ouder minder dan 8 reads, of is de proband homozygoot (buiten de X of Y van een zoon), dan wordt PM6 *consider* in plaats van toegepast. Bij een zoon op X of Y buiten de pseudo-autosomale regio's beslist alleen de ouder die dat chromosoom doorgeeft. Meerdere aangedane dragers → PP1 (*consider*); een aangedaan familielid zonder de variant → BS4 (*consider*). PS2 blijft een manuele keuze |

De drempels volgen de ClinGen-kalibratie van 2022 en staan als benoemde constanten in de evaluator. Kon het genprofiel of de HPO-lijst niet geladen worden, dan zegt het bewijs dat het criterium *niet beoordeeld* is, niet dat het niet geldt. Criteria die een menselijk oordeel vragen dat CoGA niet uit de annotatie kan afleiden (o.a. PS1, PS3, PS4, PM1, PM3, PM5 en BP2), stelt de evaluator nooit als van toepassing voor.

**Waar in de code:** `frontend/src/lib/acmg/evaluate.ts` (de evaluator), `pedigree.ts` (de ouders uit de stamboom) en `criteria.ts` (de catalogus van de 28 criteria); de ClinVar-lezing in `frontend/src/lib/clinvar.ts`; het ophalen van de context in `frontend/src/pages/families/AcmgClassificationModal.tsx`.

### De puntenschaal en de vijf klassen

CoGA gebruikt het Bayesiaanse puntensysteem van Tavtigian en ClinGen. Elk aanvaard criterium telt punten volgens zijn sterkte; benigne criteria tellen negatief.

| Sterkte | Pathogeen | Benigne |
| --- | ---: | ---: |
| Supporting | +1 | −1 |
| Moderate | +2 | −2 |
| Strong | +4 | −4 |
| Very strong | +8 | — |

Het totaal geeft de klasse: **≥ 10** pathogeen (klasse 5), **6 tot 9** waarschijnlijk pathogeen (4), **0 tot 5** VUS (3), **−1 tot −6** waarschijnlijk benigne (2), **≤ −7** benigne (1). Een aanvaarde **BA1** maakt de variant benigne, ongeacht de rest. Een VUS krijgt nog een niveau naar de afstand tot de grens van klasse 4: **4–5 hot**, **2–3 warm**, **0–1 cold**.

**Server herberekent altijd.** De frontend stuurt zijn eigen totaal mee, maar de backend negeert dat. Hij controleert elke criteriumcode en elke sterkte (onbekend geeft `400`) en berekent klasse, totaal en VUS-niveau opnieuw. Een opgeslagen classificatie hangt dus nooit af van de browser. Frontend en backend gebruiken dezelfde drempels, en beide kanten worden tegen die drempels getest. De dialoog schrijft de klasse ook terug als tag (`acmg_class_N`, en voor een VUS `acmg_vus_<niveau>`), zodat kaarten en samenvattingen ze tonen.

**Waar in de code:** `frontend/src/lib/acmg/score.ts` en `backend/app/services/acmg_points.py`; de herberekening in `backend/app/services/small_variant_review_acmg.py`.

### Mitochondriale varianten

Een variant uit de mtDNA-analyse gaat naar een **aparte evaluator** volgens de ClinGen-specificaties voor mtDNA (McCormick 2020). mtDNA erft via de moeder en is haploïd, dus: geen de-novoregels, strengere frequentiedrempels, PVS1 alleen in eiwitcoderende loci, geen in-silicovoorspellers, en segregatie via de moederlijn en de heteroplasmie. De moederlijn begint bij de moeder die de stamboom aan de proband koppelt; PP1 vraagt dat zij de variant draagt. De puntenschaal en de klassen zijn gelijk.

**Waar in de code:** `frontend/src/lib/acmg/evaluateMito.ts`.

## Deel 3 — ACMG voor CNV's

Kopieaantalvarianten worden geclassificeerd volgens de **ClinGen-CNV-standaard van 2019** (Riggs et al., 2020), een ander puntensysteem:

- De bewijssecties en gewichten verschillen tussen **verlies** (deletie) en **winst** (duplicatie): er zijn twee catalogi.
- Punten zijn **continu** (bv. +0,90 of −0,60), en veel criteria hebben een toegestaan bereik waarbinnen de reviewer een waarde kiest.
- De klassen: **≥ 0,99** pathogeen, **0,90 tot 0,98** waarschijnlijk pathogeen, **−0,89 tot 0,89** VUS, **−0,98 tot −0,90** waarschijnlijk benigne, **≤ −0,99** benigne.

De evaluator is voorzichtig: hij stelt alleen voor op basis van wat de SV betrouwbaar meedraagt (overlappende genen, genconstraint, geannoteerde overerving, het aantal genen). Net als bij small variants **begrenst en herberekent** de server: elke waarde wordt binnen het bereik van haar criterium gehouden voordat ze wordt opgeteld. Anders dan bij small variants bestaat er geen analoog van BA1.

**Waar in de code:** `frontend/src/lib/cnvAcmg/` (catalogi, evaluator, score) en `frontend/src/pages/families/CnvAcmgClassificationModal.tsx`; `backend/app/services/cnv_acmg_points.py` en `structural_variant_review_pg.py`.

## Deel 4 — Opslag en het bevroren bewijs

De volledige beoordeling per criterium wordt als JSON bewaard, met het herberekende totaal en de klasse ernaast (voor filters en samenvattingen): `acmg`, `acmg_point_total` en `acmg_class` op `small_variant_reviews`, en `cnv_acmg`, `cnv_point_total` en `cnv_class` op `structural_variant_reviews`. Zo is achteraf te zien welke criteria met welke sterkte en welk bewijs werden toegepast.

**Het bewijssnapshot.** Bij het classificeren van een small variant bevriest CoGA het bewijs in `acmg_evidence_snapshot`: de annotatieversie en de **hash van de annotatieset** (die verandert zodra enige annotatie verandert), de ClinVar-waarde en het tijdstip. Elke wijziging van een review, van een small variant, een SV of een CNV, schrijft bovendien, in dezelfde transactie, een voor-en-na-regel in het append-only, hash-geketende **klinische auditspoor** (hoofdstuk 11): wie, wanneer en wat veranderde (klasse, criteria, tags, notitie). De regel voor de klasse bevat het hele bewaarde record: per criterium de sterkte (bij een CNV de punten), of het aanvaard is, het bewijs en of het een automatische suggestie was, met het puntentotaal (en bij een CNV de soort). Elke wijziging daaraan komt in het spoor, ook als de klasse gelijk blijft; een ongewijzigde herbewaring schrijft niets. Ook het leegmaken of wissen van een review wordt vastgelegd.

**Het bewijssnapshot van een CNV.** Een SV heeft in ClickHouse geen hash van de annotatieset. Bij het opslaan van een CNV-classificatie bevriest CoGA daarom in `cnv_evidence_snapshot` (op `structural_variant_reviews`) de waarden die de CNV-evaluator leest: het type, de overlappende genen en hun aantal, de pLI en de geannoteerde overerving. De evaluator leest geen klinische CNV's, dosagescores of DGV; die criteria scoort de analist zelf. Het snapshot bevat ook de gebeurtenis zelf (de caller, het chromosoom, het begin, het einde, de lengte en de breukpuntpartner), een hash van de volledige annotatie van de SV, het tijdstip en de versies waar het bewijs vandaan komt: die van de SV-callset in het annotatiemanifest van de familie, en de referentie-assembly en de genloci. De driftsleutel is `evidence_hash`, een SHA-256 over het bewijs; de versies worden vastgelegd, niet vergeleken. De genotypes zitten er niet in. Een CNV-classificatie van een SV die niet in de data van de familie zit, weigert de server (`404`). Heeft de familie geen assembly, dan is er geen SV-opslag om te lezen en wordt de classificatie zonder snapshot bewaard.

**Beperking.** De review van een compound-heterozygoot paar schrijft geen regel in het klinische auditspoor.

**Waar in de code:** `build_evidence_snapshot` in `backend/app/services/small_variant_review_acmg.py`; `build_structural_evidence_snapshot` in `backend/app/services/structural_variant_evidence.py`; het auditspoor in `backend/app/services/clinical_audit_service.py` (`record_review_changes` voor small variants, `record_structural_review_changes` voor SV's en CNV's).

## Deel 5 — Classificatiedrift

Annotaties veranderen: een nieuwe ClinVar- of gnomAD-release kan het bewijs onder een bestaande classificatie verschuiven, en een herimport kan de genen onder een CNV veranderen. CoGA vergelijkt daarom per geclassificeerde small variant, en per SV of CNV met een CNV-classificatie, het bevroren snapshot met de huidige data, met dezelfde functie die het snapshot bouwde:

| Status | Betekenis |
| --- | --- |
| `current` | Ongewijzigd (de hashes zijn gelijk) |
| `drifted` | Het bewijs veranderde sinds de classificatie |
| `variant_missing` | De variant zit niet meer in de data |
| `unknown` | Een van beide hashes ontbreekt, of het bevroren bewijs is niet te lezen; de koppeling is niet te controleren |

Ontbreekt een hash, dan meldt de controle **niet** `current` maar `unknown`, en bij het ondertekenen telt `unknown` als drift. Een drift moet worden herbekeken of, met een reden, erkend voordat een rapport ondertekend kan worden (hoofdstuk 11). Wie de classificatie opnieuw opslaat, bevriest het bewijs zoals het nu is.

Bij een SV of CNV zegt `drifted` ook wat verschoof (`changed`): de genen, de pLI, de overerving, het type, de positie, de caller of de overige annotatie, met de waarde ervoor en erna. De controle vergelijkt alleen de velden die het snapshot bevroor. De SV wordt gelezen zoals de SV-pagina hem leest.

**Waar in de code:** `backend/app/services/classification_drift_service.py` (de statuslogica in `_diff`; voor SV's en CNV's `evaluate_structural_classification_drift`, met `diff_structural_evidence` uit `structural_variant_evidence.py`).

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `frontend/src/content/docs/acmg-classification.md` | De regels per criterium, zoals het labo ze leest |
| `frontend/src/lib/acmg/` | Catalogus, evaluator (ook mtDNA) en puntenschaal |
| `frontend/src/lib/cnvAcmg/` | CNV-catalogi, evaluator en puntenschaal |
| `frontend/src/pages/families/AcmgClassificationModal.tsx` · `CnvAcmgClassificationModal.tsx` | De classificatiedialogen |
| `backend/app/services/acmg_points.py` · `cnv_acmg_points.py` | Herberekening op de server |
| `backend/app/services/small_variant_review_pg.py` · `small_variant_review_acmg.py` | Reviews opslaan, ACMG controleren, het bewijssnapshot |
| `backend/app/services/structural_variant_review_pg.py` | CNV/SV-reviews en CNV-ACMG |
| `backend/app/services/structural_variant_evidence.py` | Het bewijssnapshot van een CNV-classificatie en de vergelijking ervan |
| `backend/app/services/small_variant_review_tags.py` | Systeemtags, eigen tags, rechten |
| `backend/app/services/classification_drift_service.py` | Drift |
| `backend/app/services/clinical_audit_service.py` | Het klinische auditspoor |
