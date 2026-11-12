# 10. Variant-tagging & semi-automatische ACMG-classificatie

Dit hoofdstuk beschrijft hoe een analist varianten *labelt* (taggen) en *classificeert*, met hulp van een **semi-automatische ACMG/AMP-classificator**. CoGA classificeert niet zelf. De software beoordeelt elk criterium vooraf uit de variant-, familie- en gendata en zet het op een puntenschaal; de reviewer kan *elk* criterium aanpassen. De server **herberekent** altijd de klasse en het puntentotaal, en het gebruikte bewijs wordt **bevroren**. Verandert dat bewijs later, dan meldt CoGA **drift**, en dat houdt het ondertekenen tegen (hoofdstuk 11). De regels per criterium staan in de app: *Docs → Semi-automatic ACMG classification* (`frontend/src/content/docs/acmg-classification.md`).

Enkele begrippen:

- **ACMG/AMP-criteria:** een gestandaardiseerde set bewijsregels (Richards et al., 2015) met codes als `PVS1`, `PM2` of `BA1`. Een `P` wijst op pathogeen bewijs, een `B` op benigne bewijs.
- **Tag:** een label aan een variant (bv. "Report" of "Send for validation").
- **Snapshot:** een bevroren kopie van de gegevens zoals ze op een bepaald moment waren.

## Twee aparte eigenschappen: fenotype en dragerschap

CoGA houdt per familielid twee losse eigenschappen bij:

- **Klinische status** (`clinical_status`): `unknown`, `unaffected` of `affected`: heeft de persoon de aandoening?
- **Dragerschap** (`carrier_status`): `unknown`, `not_carrier` of `carrier`: draagt de persoon het risicoallel, ziek of niet?

Die scheiding is klinisch belangrijk: bij een recessieve aandoening is een ouder vaak drager maar niet aangedaan. De segregatieregels van de ACMG-evaluator lezen alleen de klinische status en de genotypes. Het dragerschap dient voor de haplotypesegregatie van PGT (hoofdstuk 8).

## Deel 1 — Taggen

### Wat wordt vastgelegd

Een review legt per variant en per familie vast: de **klasse** (bv. "VUS - class 3"), een lijst **tags** en een **notitie**. Per tag bewaart CoGA ook *wie* hem zette en *wanneer*.

**Klasselabels in zinshoofdletters.** De klassetags en de VUS-niveaus staan in zinshoofdletters: "Likely pathogenic - class 4", "VUS — hot" (beslissing van de eigenaar). Een tag staat op een review als sleutel. De klassetekst van een review is vrije tekst: een opslag bewaart ze zoals verstuurd, zonder witruimte vooraan en achteraan (`_stored_classification` in `review_pg_utils.py`). De filters en de Variant Explorer lezen een klasselabel ongeacht hoofdletters.

**Waar in de code:** de labels in `acmg_points.py`, `cnv_acmg_points.py` en `small_variant_review_tags.py`; de filters in `small_variant_review_pg.py`, `structural_variant_review_pg.py` en `variant_explorer_service.py`.

**De classificatiefilter volgt de getoonde klasse.** Een review toont zijn klasse zoals de familiepagina die leest (`review_class_label` in `review_pg_utils.py`, dezelfde regel als `normalizeReviewClassification` in de frontend): een kaal klassewoord in de tekst ("VUS") als zijn label, anders de ernstigste klassetag (`acmg_class_5` … `acmg_class_1`), anders de tekst zoals opgeslagen. De classificatiefilter van de familiepagina en die van de Variant Explorer houden een variant over op die klasse, van de eigen review of van de review van het compound-het-paar, ongeacht hoofdletters (beslissing van de eigenaar van 10 november 2026). Zo vindt *Likely pathogenic - class 4* ook een review met alleen de klassetag `acmg_class_4`, en niet een review met de tekst "VUS - class 3" naast de klassetag `acmg_class_5`: die toont klasse 5. Een filterwaarde wordt gelezen zoals de tekst van een review: "VUS" vraagt klasse 3, vrije tekst de reviews die die tekst tonen (dus zonder klassetag). De familiepagina past de regel toe in Python (`review_shows_class`), de Explorer in SQL (`review_class_label_sql`, dat de klassewoorden, de klassetags en de labels uit de Python-tabellen bindt); een integratietest op Postgres legt de SQL naast de Python-regel voor elk geval van de gedeelde tabel `review-class-cases.json`. De SV-pagina toont de klasse van een SV-review met dezelfde regel, uit de tekst en de tags, en de classificatiefilter van de SV-zoekopdracht houdt een SV op die klasse over, met `review_shows_class` (beslissing van de eigenaar van 10 november 2026). Een CNV-classificatie telt mee via het klasselabel dat de CNV-dialoog als tekst bewaart; haar klasse in `cnv_class` alleen toont de pagina niet, en de filter leest ze dus niet.

**De familiepagina toont de klasse volgens dezelfde regel.** Overal waar de familiepagina de klasse van een small-variantreview toont, leest ze die met die ene regel (beslissing van de eigenaar van 10 november 2026): de tabel, de kaarten, de compound-het-paren, de mtDNA-kaarten en de lichte reviewdialoog, en de kolom *Classification* van de CSV-export van de familie (`review_class_label`). Een review toont één klasse. Een klassetag van een andere klasse verschijnt er niet naast; de VUS-niveaus en *Secondary finding* blijven tags, nooit de klasse (`reviewClassChips` in `smallVariantResultUtils.ts`). De tabel en de kaarten van de SV-pagina lezen de klasse van een SV-review met dezelfde helper (beslissing van de eigenaar van 10 november 2026).

**Naast de getoonde klasse de tekst zoals getypt.** De CSV-exports van small variants, van SV's en van de Variant Explorer schrijven in de kolom *Classification* de klasse die de review toont, en in de kolom *Classification text* ernaast de classificatie zoals de reviewer ze schreef en ze opgeslagen is. Zo blijft "VUS - class 3" naast de tag `acmg_class_4` in het bestand staan, al toont de review klasse 4. Een review met alleen een klassetag heeft een lege *Classification text*. De Explorer schrijft de tekst van de review waarvan hij de klasse toont: de eigen review of die van het compound-het-paar, in de familie die de ernstigste klasse geeft (hoofdstuk 14).

**Een snelle tag verandert de classificatie niet.** *Review*, *Exclude* en *Report* sturen de classificatietekst terug zoals ze bewaard is (`quickTagTogglePayload` in `smallVariantReview.ts`, ook op de SV-pagina). Zo'n opslag schrijft alleen de tag in het spoor. De lichte reviewdialoog opent op de getoonde klasse. Blijft de klasse ongemoeid, dan bewaart de dialoog de tekst en de klassetags zoals ze waren. Kiest de gebruiker een andere klasse, of geen, dan vervangt hij de tekst en elke klassetag; anders zou een strengere klassetag ernaast de gekozen klasse blijven verbergen. Het rapport en een getekende versie tonen dezelfde klasse als de familiepagina: het rapport leest ze met deze regel, een getekende versie uit het bevroren record (`class_shown`, hoofdstuk 11), met de ACMG- of CNV-score ernaast als die een andere klasse geeft.

**Gelijktijdig bewerken.** Werd een review (van een small variant of een structurele variant) intussen door iemand anders gewijzigd, dan weigert de server de opslag (`409`) en stuurt hij de actuele versie mee. Zo overschrijft niemand ongemerkt andermans werk.

Wie een compound-heterozygoot paar opslaat, schrijft ook de review van de partnervariant. Valt een paar daarbij uiteen, dan herschrijft CoGA de review van elk van zijn varianten. CoGA vergrendelt al die reviews eerst, in één vaste volgorde, zoals bij een gewone opslag, en leest ze pas daarna. Een partnerreview die intussen werd opgeslagen (bv. als klasse 5 met een nieuwe notitie), blijft dus behouden en wordt nooit teruggezet naar een eerder gelezen kopie. De server controleert de versie van de partner zoals die van de variant zelf (`compound_het.partner_expected_updated_at`). Werd de partner intussen gewijzigd, dan volgt dezelfde `409`, die de partner noemt. `updated_at` gaat nooit terug in de tijd.

**Waar in de code:** `backend/app/services/small_variant_review_pg.py` (het paar in `_lock_pair_save_reviews`); de controle op gelijktijdige wijzigingen in `_raise_on_stale_review` (`backend/app/services/review_pg_utils.py`).

### Welke tags bestaan

- **Systeemtags** zijn ingebouwd en niet te wijzigen: samenwerkingstags (bv. `review`, `send_for_validation`, `validated`, `report`, `excluded`) en de klassetags (`acmg_class_1` tot `acmg_class_5`, de VUS-niveaus `acmg_vus_hot`, `acmg_vus_warm` en `acmg_vus_cold`, en `secondary_finding`).
- **Eigen tags** maakt een superuser of een beheerder aan. Ze gelden overal (`global`) of voor één project (`project`); een projecttag kan met extra projecten gedeeld worden.

Bij het opslaan controleert de backend of elke tag die de review *toevoegt* bestaat en in deze familie gebruikt mag worden; een onbekende tag geeft `400`. Een tag die de review al draagt, blijft staan, ook als hij intussen verwijderd is. De snelknoppen en de reviewdialoog sturen de opgeslagen tags immers mee terug; anders zou elke volgende opslag van die variant mislukken, ook die welke de tag weghaalt.

Reviews, bewaarde filterpresets en de audit trail noemen een tag bij zijn **sleutel** (`key`). Die wordt bij het aanmaken afgeleid van het label en ligt dan vast. Hernoemen wijzigt alleen het label; de tag blijft dus op elke review en in elke filter staan. Een label dat leest als dat van een andere actieve tag of van een systeemtag, wordt geweigerd (`409`). Krijgt een nieuwe tag het label van een hernoemde of verwijderde tag, dan krijgt hij een genummerde sleutel (bv. `probe_x_2`). Hij neemt geen reviews van de oude tag over.

Alleen een superuser of een beheerder (`get_current_superuser`, hoofdstuk 2) maakt eigen tags aan, wijzigt of verwijdert ze, op de beheerpagina en op de routes van een familie; een ander krijgt `403 Superuser access required`. Verwijderen wist een tag niet, maar zet hem op inactief. Reviews die hem dragen, tonen hem nog met zijn label, gemarkeerd als *(deleted)*. Hij kan er weggehaald worden, maar aan geen enkele review meer toegevoegd. Het beheerscherm staat onder Administratie (`frontend/src/pages/admin/AdminVariantTagsPage.tsx`).

Wordt een project verwijderd, dan gaat het met zijn tags net zo. `delete_project_record` zet ze eerst op inactief en verwijdert het project in dezelfde transactie; de tags blijven bestaan, zonder project (`project_id` wordt `NULL`). Ze blijven projecttags, dus geen lijst toont ze als globale tag, en ze houden hun sleutel: een nieuwe tag met hetzelfde label krijgt een genummerde sleutel en neemt geen reviews, ondertekende records of auditregels over. De taglijst van een familie toont bovendien, als inactief, elke tag die haar eigen reviews dragen maar die haar projecten niet (meer) kennen, zoals een tag van een verwijderd project of van een project dat de familie verliet. Een tag van een ander project die geen van haar reviews draagt, blijft onzichtbaar.

**Waar in de code:** `backend/app/services/small_variant_review_tags.py`; de tabellen `small_variant_tag_definitions` en `small_variant_tag_definition_project_links` in `03_assay.sql`.

### Filterpresets en de lichte reviewdialoog

Een analist kan zijn filterinstellingen bewaren als **preset**; ook een viewer of een lezer kan dat, want het zijn zijn eigen gegevens (hoofdstuk 2). Alleen wie de preset bewaarde, krijgt hem aangeboden en kan hem verwijderen; een superuser of beheerder ziet de presets voor small variants van iedereen (hoofdstuk 15). De eigenaar is het account (`owner_id`), niet de gebruikersnaam: verandert die bij een aanmelding met Microsoft, dan blijft de preset van hem. De preset toont de gebruikersnaam waaronder hij laatst bewaard werd (`owner`). Een preset voor small variants geldt in elke familie die de eigenaar kan openen. Een preset voor structurele varianten geldt voor één familie, of herbruikbaar voor al zijn families.

Naast de volledige ACMG-classificator is er een **lichte reviewdialoog**: een klasse kiezen, tags aan- of uitzetten en een notitie schrijven, met de prioriteitsscore (hoofdstuk 12) als hulp.

**Waar in de code:** `backend/app/services/small_variant_review_presets.py` en, voor de SV-presets, `structural_variant_review_pg.py`; `frontend/src/pages/families/SmallVariantReviewDialog.tsx`.

### Repeatloci en Paraphase-regio's

Een repeatlocus en een Paraphase-regio krijgen een eigen review, per familie en assembly. Die gebruikt dezelfde tagcatalogus, maar classificeert op de eigen schaal van het type in plaats van met een ACMG-klasse (REQ-DIAG-009; `docs/repeat-paraphase-review-design.md`).

- De kaart op de repeat- en de Paraphase-pagina heeft de knoppen van elke variantkaart. *Review*, *Exclude* en *Report* zetten hun tag aan of uit; *Tags & notes* opent de dialoog (`FindingReviewDialog.tsx`).
- De dialoog biedt de klassetags met sleutel `acmg_*` (de ACMG-klassen en de VUS-niveaus) niet aan. Draagt een review er toch een, dan toont hij die gemarkeerd, zodat ze weggehaald kan worden (beslissing B14). *Secondary finding* blijft beschikbaar.
- De kaart toont de classificatie (*Review: …*), de tags en de notitie. *Differs from CoGA's status* verschijnt als de classificatie bij geen enkele call van de leden past. *Changed since review* meldt drift; de tooltip zegt wat veranderde. *Exclude* klapt de kaart dicht tot haar kop.
- Een opslag schrijft de review meteen in de geladen tabel. Het antwoord van de server, met de drift, vervangt ze; een mislukte opslag zet de kaart terug (`useFindingReviewSave` in `findingReview.ts`).
- De familiepagina telt de reviews per type (`GET /families/{id}/finding-review-summary`).

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
| **Applies** | aangevinkt, groen (pathogeen) of grijsblauw (benigne) | De data steunt het criterium duidelijk; het telt mee |
| **Consider** | oranje stip, niet aangevinkt | Relevant signaal, niet doorslaggevend |
| **Argues against** | rood kruis, niet aangevinkt | De data wijst de andere kant op (bv. een benigne voorspelling bij PP3) |
| **Not applicable** | grijs, doorgestreept | Kan voor dit varianttype niet gelden, maar blijft aanklikbaar |

Alle vier zijn aan te passen: een klik op een criterium zet het altijd aan of uit.

**Een geclassificeerde variant opnieuw openen.** De dialoog toont elk opgeslagen criterium met zijn toestand, sterkte en bewijs. De evaluator vult alleen ontbrekend bewijs aan, en geeft criteria die nooit werden opgeslagen hun eigen toestand. Opnieuw opslaan zonder wijziging laat de criteria dus zoals ze waren. Elke lijst van small variants (de tabel en de kaarten, het rapport, de NIPT-kandidaten, de mtDNA-analyse) levert de review met hetzelfde ACMG-record als het lezen van die ene review (`_fetch_review_rows_for_variants` in `small_variant_review_repository.py`).

### Wat de evaluator vooraf beoordeelt

De evaluator leest de variant en, waar beschikbaar, het genprofiel (ClinGen-dosage, overervingswijze uit GenCC, fenotypes van het gen), de HPO-termen van de proband en de genotypes in de familie. Hij stelt alleen voor en verandert niets. De belangrijkste regels:

| Criterium | Bron | Regel (samengevat) |
| --- | --- | --- |
| **PVS1** | gevolg, LOFTEE, ClinGen-dosage | Voorspeld verlies van functie (stopcodon, frameshift, canonieke splicing, …). *Very strong* als LOFTEE `HC` zegt én het verliesmechanisme bewezen is; anders *strong*, of *consider* als het mechanisme niet bevestigd is. Met een LOFTEE-flag (bv. `SINGLE_EXON`, `NAGNAG_SITE`) of een variant die volgens de NMD-plugin van VEP aan nonsense-mediated decay ontsnapt: *consider* (*strong*), met de reden in het bewijs |
| **PM2**, **BA1**, **BS1**, **BS2** | gnomAD | Zeer zeldzaam → PM2 (*supporting*). Zonder gnomAD-frequentie in de annotatie is de variant afwezig uit gnomAD, of werd gnomAD niet geannoteerd: de annotatie zegt niet welke. PM2 wordt dan *consider*, niet toegepast, en BA1 en BS1 blijven open: kijk gnomAD zelf na. BA1 en BS1 lezen gnomAD's *filtering allele frequency* (FAF95, het hoogste over de genetische herkomstgroepen) waar de annotatie die heeft, zoals ClinGen SVI aanraadt, anders de allelfrequentie. Zeer frequent → BA1, dat de variant op zichzelf benigne maakt. Frequent → BS1. Homozygoten aanwezig → BS2: *strong* bij een gen met recessieve overervingswijze, anders *consider* op *supporting*. Zonder homozygotenaantal in de annotatie blijft BS2 ongemarkeerd: kijk gnomAD zelf na |
| **PP2** | gnomAD missense-Z: van het genprofiel, anders de eigen `MISSENSE_Z` van de VCF | Missense in een gen met een missense-Z van 3,09 of meer (de drempel die de Sequence Variant Interpretation-werkgroep van ClinGen voor PP2 bespreekt) → *consider* (*supporting*), nooit toegepast: de analist oordeelt of missense in dat gen een gewoon ziektemechanisme is. Het bewijs zegt welke missense-Z PP2 las. Een genprofiel onder 3,09 beslist, ook als de VCF een hogere waarde heeft (beslissing van de eigenaar van 2026-10-28) |
| **PP3** / **BP4** | REVEL, SpliceAI, AlphaMissense | REVEL beslist eerst, dan SpliceAI, dan AlphaMissense; wie beslist, bepaalt ook de sterkte. Alleen een oordeel op REVEL markeert het andere criterium als tegenargument. BP4 vraagt dat SpliceAI geen splice-impact voorspelt: zonder SpliceAI-score wordt BP4 *consider*. Zonder enige voorspelling zijn beide niet van toepassing. Een REVEL tussen de drempels of een *ambiguous* AlphaMissense-klasse laat beide open |
| **BP7** | gevolg en SpliceAI | Synoniem zonder voorspelde splice-impact (SpliceAI onder 0,1). Zonder SpliceAI-score *consider*: kijk de splice-impact zelf na |
| **PP5** / **BP6** | ClinVar | ClinVar zegt pathogeen → PP5; benigne → BP6, allebei *consider*, nooit toegepast: ClinGen SVI raadt af de classificatie van een ander labo als bewijs te tellen. Het bewijs noemt de reviewstatus van ClinVar, de sterren en de aandoening(en). *Conflicting classifications of pathogenicity* geeft geen van beide. ClinVar is het ClinVar-bestand dat de pipeline annoteerde; de oudere classificatie uit de VEP-cache telt alleen als er geen ClinVar-bestand geannoteerd werd |
| **PP4** | Monarch-fenotypescore of HPO-overlap | Hoe specifiek het fenotype bij het gen past |
| **PM6**, **PP1**, **BS4** | genotypes in de familie | De novo (afwezig bij beide ouders) → PM6. De ouders zijn de vader en moeder die de stamboom aan de proband koppelt, niet de leden met die rol: ook een grootouder heeft de rol vader of moeder. Heeft een ouder minder dan 8 reads, of is de proband homozygoot (buiten de X of Y van een zoon), dan wordt PM6 *consider* in plaats van toegepast. Bij een zoon op X of Y buiten de pseudo-autosomale regio's beslist alleen de ouder die dat chromosoom doorgeeft. Meerdere aangedane dragers → PP1 (*consider*); een aangedaan familielid zonder de variant → BS4 (*consider*). PS2 blijft een manuele keuze |

De REVEL-drempels volgen de ClinGen-kalibratie van 2022; alle drempels staan als benoemde constanten in de evaluator. Kon het genprofiel of de HPO-lijst niet geladen worden, dan zegt het bewijs dat het criterium *niet beoordeeld* is, niet dat het niet geldt. Criteria die een menselijk oordeel vragen dat CoGA niet uit de annotatie kan afleiden (o.a. PS1, PS3, PS4, PM1, PM3, PM5 en BP2), stelt de evaluator nooit als van toepassing voor.

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

De dialoog toont het totaal op een balk volgens de Bayesiaanse lezing van de schaal (voorafkans 10%, odds op pathogeniciteit 350^(punten/8)). Elk VUS-punt heeft een eigen vak, de streepjes dragen de posterieure kans (0,1%, 10%, 18,8%, 32,5%, 50%, 67,5%, 81,2%, 90% en 99%), en de klassegrenzen zijn sterker aangeduid. De balk is alleen een weergave; de klasse volgt uit de drempels hierboven (`frontend/src/pages/families/AcmgScaleBar.tsx`).

**Server herberekent altijd.** De frontend stuurt zijn eigen totaal mee, maar de backend negeert dat. Hij controleert elk criterium en weigert (`400`):

- een onbekende code of sterkte;
- een criterium dat twee keer voorkomt;
- een sterkte die het criterium niet toelaat.

Elk criterium telt dus één keer, op een sterkte die de dialoog ervoor aanbiedt. Anders zou een twee keer gestuurde PVS1 +16 tellen (pathogeen), en BS1 op *very strong* −8. Daarna berekent de backend klasse, totaal en VUS-niveau opnieuw. Een opgeslagen classificatie hangt dus nooit af van de browser. Alleen het opslaan wordt gecontroleerd: een eerder bewaarde classificatie wordt gelezen zoals ze geschreven werd. Frontend en backend gebruiken dezelfde drempels, en beide kanten worden tegen die drempels getest. De dialoog schrijft de klasse ook terug als tag (`acmg_class_N`, en voor een VUS `acmg_vus_<niveau>`), zodat kaarten en samenvattingen ze tonen.

**Toegelaten sterktes.** De pathogene criteria laten *very strong* tot *supporting* toe, behalve PM2 (*moderate* of *supporting*). BA1 is alleen *stand-alone*, en *stand-alone* is alleen voor BA1. BS1 tot BS4 en BP4 laten *strong* tot *supporting* toe, de andere BP-criteria *moderate* of *supporting*. De lijst staat twee keer: als `allowedStrengths` in `frontend/src/lib/acmg/criteria.ts` (de keuzes in de dialoog) en als `ALLOWED_STRENGTHS` in `acmg_points.py` (de controle op de server). `backend/tests/test_acmg_frontend_parity.py` leest `criteria.ts` en faalt als de twee verschillen.

**Waar in de code:** `frontend/src/lib/acmg/score.ts` en `backend/app/services/acmg_points.py`; de controle en de herberekening in `backend/app/services/small_variant_review_acmg.py`.

### Mitochondriale varianten

Een variant uit de mtDNA-analyse gaat naar een **aparte evaluator** volgens de ClinGen-specificaties voor mtDNA (McCormick 2020). mtDNA erft via de moeder en is haploïd. Daarom: geen de-novoregels, strengere frequentiedrempels, PVS1 alleen in eiwitcoderende loci, geen in-silicovoorspellers, en segregatie via de moederlijn en de heteroplasmie. Met gnomAD-MT of HelixMTdb in de annotatie lezen BA1 en BS1 de hoogste homoplasmische populatiefrequentie; PM2 wordt alleen toegepast als ook de heteroplasmische onder 0,002% ligt, anders *consider*. Waar twee mtDNA-genen overlappen (MT-ATP8/MT-ATP6, MT-ND4L/MT-ND4), toont de kaart de consequentie per gen. De moederlijn begint bij de moeder die de stamboom aan de proband koppelt; PP1 vraagt dat zij de variant draagt. De puntenschaal en de klassen zijn gelijk.

**Waar in de code:** `frontend/src/lib/acmg/evaluateMito.ts`.

## Deel 3 — ACMG voor CNV's

Kopieaantalvarianten worden geclassificeerd volgens de **ClinGen-CNV-standaard van 2019** (Riggs et al., 2020), een ander puntensysteem:

- De bewijssecties en gewichten verschillen tussen **verlies** (deletie) en **winst** (duplicatie): er zijn twee catalogi.
- Punten zijn **continu** (bv. +0,90 of −0,60). Veel criteria hebben een toegestaan bereik waarbinnen de reviewer een waarde kiest.
- De klassen: **≥ 0,99** pathogeen, **0,90 tot 0,98** waarschijnlijk pathogeen, **−0,89 tot 0,89** VUS, **−0,98 tot −0,90** waarschijnlijk benigne, **≤ −0,99** benigne.

De evaluator is voorzichtig. Hij stelt alleen voor op basis van wat de SV betrouwbaar meedraagt: overlappende genen, genconstraint, geannoteerde overerving en het aantal genen. Net als bij small variants **begrenst en herberekent** de server: hij houdt elke waarde binnen het bereik van haar criterium voordat hij optelt. Een criterium dat twee keer voorkomt, weigert hij (`400`). Anders zou het twee keer tellen, voorbij zijn bereik: 2B op 0,90 twee keer zou 1,80 geven, pathogeen in plaats van waarschijnlijk pathogeen. De catalogi staan in de frontend en in `cnv_acmg_points.py`; `test_acmg_frontend_parity.py` faalt als hun codes, secties of bereiken verschillen. Anders dan bij small variants bestaat er geen analoog van BA1.

**Eigen voorstellen per soort.** De dialoog houdt een eigen formulier bij voor verlies en voor winst. Elk krijgt de voorstellen die de evaluator voor die soort berekent: de trap van sectie 3 volgens de drempels van die soort, en 2H (uit de pLI) alleen bij verlies. Wie van soort wisselt, krijgt dus nooit de criteria, punten of notities van de andere soort. Een wijziging met de hand blijft bij de soort waarin ze gemaakt werd, en staat er nog na terugwisselen. Alleen de getoonde soort wordt bewaard.

**Waar in de code:** `frontend/src/lib/cnvAcmg/` (catalogi, evaluator, score) en `frontend/src/pages/families/CnvAcmgClassificationModal.tsx`; `backend/app/services/cnv_acmg_points.py` en `structural_variant_review_pg.py`.

## Deel 4 — Opslag en het bevroren bewijs

De volledige beoordeling per criterium wordt als JSON bewaard. Het herberekende totaal en de klasse staan ernaast, voor filters en samenvattingen: `acmg`, `acmg_point_total` en `acmg_class` op `small_variant_reviews`, en `cnv_acmg`, `cnv_point_total` en `cnv_class` op `structural_variant_reviews`. Zo is achteraf te zien welke criteria met welke sterkte en welk bewijs werden toegepast.

**Het bewijssnapshot.** Bij het classificeren van een small variant bevriest CoGA het bewijs in `acmg_evidence_snapshot`: de annotatieversie, de **hash van de annotatieset** (die verandert zodra enige annotatie verandert), de ClinVar-waarde en het tijdstip. Daarnaast de consequentie, populatiefrequenties en in-silicoscores waarop de classificatie steunt, zoals de ACMG-dialoog ze leest: van het primaire transcript van de variant (MANE Select, dan canoniek, dan de zwaarste impact; zijn id staat in `transcript_id`), via dezelfde hulpfuncties als de variantenlijst (`small_variant_evidence`). Per groep:

- `consequence`: de consequentieterm(en) (`effect`) die de criteria voor een null-, missense-, synonieme of lengteveranderende variant lezen (PVS1, PM4, PP2, BP7 en de criteria die een consequentie uitsluit); de `impact`; de loss-of-functionoproep van LOFTEE (`lof`), waarvan de sterkte van PVS1 afhangt, met de flags van LOFTEE (`lof_flags`) en de NMD-voorspelling van VEP (`nmd`), die PVS1 *consider* maken. Alleen bij een multiallelisch record (ALT `G,T`) ook het ALT-allel waarvan deze waarden zijn (`alt_allele`).
- `frequencies`: de gnomAD-allelfrequentie die de frequentiecriteria lezen; de exoom-, genoom-, gnomAD-popmax-, VEP-`MAX_AF`- (`max_af`) en TOPMed-frequenties en de gnomAD-tellingen (allelen, homozygoten, hemizygoten) die de filters lezen; gnomAD's FAF95 (`gnomad_faf95_af`) die BA1 en BS1 lezen; alleen voor een chrM-variant de homo- en heteroplasmische frequenties van gnomAD-MT en HelixMTdb. Naast de ClinVar-classificatie ook de reviewstatus van dat ClinVar-record (`clinvar_review_status`).
- `in_silico`: CADD, REVEL, SpliceAI, SIFT, PolyPhen en de AlphaMissense-klasse en -score.
- `gene_constraint`, alleen voor een missense-variant: de gnomAD-missense-Z van het gen (`gene_missense_z`) die PP2 leest, en waar die vandaan kwam (`gene_missense_z_source`).

Een missense-variant heeft `missense_variant` in haar consequentie; zo herkent de dialoog er een voor PP2 (`reads_gene_missense_z`). Bij elke andere variant leest PP2 geen missense-Z: de groep ontbreekt en het genprofiel wordt niet gelezen (beslissing van de eigenaar, 2026-10-20). De missense-Z komt uit:

1. **Het genprofiel** (`gene_info`), niet de annotatie van de variant. PP2 leest dit eerst, met de opzoeking van de variantenlijst (`fetch_gene_constraint_metrics`): in de genprofielen van de assembly van de familie, voor het gen waarmee de variant getoond wordt. Dat is het gen-id van het primaire transcript, anders zijn symbool (in hoofd- of kleine letters), anders het eerste gen van de variant.
2. **De VCF**, als het genprofiel geen missense-Z heeft: de eigen `MISSENSE_Z` van de VCF (`pp2_gene_missense_z`, beslissing van de eigenaar van 2026-10-28). De import bewaart die als `gene_missense_z` in de annotatie van het primaire transcript. De variantkaart toont ze onder *Other scores & annotations* als *Missense Z (gnomAD, from the VCF)*.

De bron is `gene_profile` of `vcf`. Heeft geen van beide een waarde, dan zijn bron en waarde `null`.

Elk veld wordt bevroren, ook zonder waarde (`null`), zodat een later verschenen waarde als wijziging telt. Een waarde die JSONB niet kan bevatten (NaN, oneindig), wordt `null`.

Elke wijziging van een review, van een small variant, een SV of een CNV, schrijft in dezelfde transactie een voor-en-na-regel in het append-only, hash-geketende **klinische auditspoor** (hoofdstuk 11): wie, wanneer en wat veranderde (klasse, criteria, tags, notitie). De regel voor de klasse bevat het hele bewaarde record: per criterium de sterkte (bij een CNV de punten), of het aanvaard is, het bewijs en of het een automatische suggestie was, met het puntentotaal (en bij een CNV de soort). Elke wijziging daaraan komt in het spoor, ook als de klasse gelijk blijft; een ongewijzigde herbewaring schrijft niets. Ook het leegmaken of wissen van een review wordt vastgelegd, en de review van een compound-heterozygoot paar krijgt een eigen regel (actie `compound_het_pair`, hoofdstuk 11).

**Het bewijssnapshot van een CNV.** Een SV heeft in ClickHouse geen hash van de annotatieset. Bij het opslaan van een CNV-classificatie bevriest CoGA daarom in `cnv_evidence_snapshot` (op `structural_variant_reviews`):

- de waarden die de CNV-evaluator leest: het type, de overlappende genen en hun aantal, de pLI en de geannoteerde overerving;
- de gebeurtenis zelf: de caller, het chromosoom, het begin, het einde, de lengte en de breukpuntpartner;
- een hash van de volledige annotatie van de SV, en het tijdstip;
- de versies waar het bewijs vandaan komt: die van de SV-callset in het annotatiemanifest van de familie, de referentie-assembly en de genloci.

De evaluator leest geen klinische CNV's, dosagescores of DGV; die criteria scoort de analist zelf. De genotypes zitten niet in het snapshot. De driftsleutel is `evidence_hash`, een SHA-256 over het bewijs; de versies worden vastgelegd, niet vergeleken. Een CNV-classificatie van een SV die niet in de data van de familie zit, weigert de server (`404`). Heeft de familie geen assembly, dan is er geen SV-opslag om te lezen, en wordt de classificatie zonder snapshot bewaard.

**Welke rij bevroren wordt.** Meerdere callers kunnen dezelfde gebeurtenis onder één SV-id oproepen (de id van een geüploade SV noemt geen caller, en die van een NeedlR-SV heeft dezelfde vorm). Ze delen dan één review, maar elke caller heeft een eigen rij op de SV-pagina, met eigen genen, pLI en overerving. Het snapshot komt daarom van de rij van de caller waarop de classificatie gemaakt werd: de CNV-dialoog stuurt die caller mee in `source`.

- Noemt de opslag geen caller, dan neemt CoGA de enige rij van de SV.
- Roepen dan meerdere callers de SV op, dan is niet te zeggen op welke rij de classificatie gemaakt werd. De server weigert dan (`422`) in plaats van het bewijs van een andere caller te bevriezen. (`409` betekent op deze route al dat iemand anders de review intussen opsloeg.)
- Een caller die de SV niet oproept, geeft `404`.

Het snapshot noemt de caller waarvan het komt (`evidence.source`) en bevat diens genen, pLI en overerving; de driftcontrole vergelijkt het met de rij van die caller.

**Waar in de code:** `build_evidence_snapshot` in `backend/app/services/small_variant_review_acmg.py`; `build_structural_evidence_snapshot` en `fetch_structural_variant_records` in `backend/app/services/structural_variant_evidence.py`, de bevroren rij in `_classified_row` (`structural_variant_review_pg.py`); het auditspoor in `backend/app/services/clinical_audit_service.py` (`record_review_changes` voor small variants, `record_structural_review_changes` voor SV's en CNV's, `record_compound_het_pair_changes` voor een paar).

## Deel 5 — Classificatiedrift

Annotaties veranderen: een nieuwe ClinVar- of gnomAD-release kan het bewijs onder een classificatie verschuiven, een herimport de genen onder een CNV. Daarom vergelijkt CoGA, per geclassificeerde small variant en per SV of CNV met een CNV-classificatie, het bevroren snapshot met de huidige data, met dezelfde functie die het snapshot bouwde:

| Status | Betekenis |
| --- | --- |
| `current` | Ongewijzigd (de hashes en elke bevroren waarde zijn gelijk) |
| `drifted` | Het bewijs veranderde sinds de classificatie |
| `variant_missing` | De variant zit niet meer in de data |
| `unknown` | Niets is zichtbaar verschoven, maar een van beide hashes ontbreekt, of het bevroren bewijs is niet te lezen; de koppeling is niet te controleren |

Ontbreekt een hash, dan meldt de controle **niet** `current` maar `unknown`; bij het ondertekenen telt `unknown` als drift. Een opgeslagen snapshot dat geen JSON-object is (de schrijver bewaart er nooit een), wordt vergeleken als een snapshot dat niets bevroor. Het meldt dus ook `unknown` (of `variant_missing`). Een drift moet herbekeken worden, of met een reden erkend, voordat een rapport ondertekend kan worden (hoofdstuk 11). Wie de classificatie opnieuw opslaat, bevriest het bewijs zoals het nu is.

Bij een small variant zegt `drifted` ook wat verschoof (`changed`):

- de ClinVar-waarde (`clinvar`);
- het primaire transcript (`transcript_id`);
- elke bevroren consequentie, frequentie of in-silicoscore, bij haar naam (`effect`, `impact`, `lof`, `gnomad_af`, `revel`, …);
- de missense-Z van het gen (`gene_missense_z`);
- zijn bron (`gene_missense_z_source`), als PP2 de missense-Z nu uit de andere bron leest omdat het genprofiel er een kreeg of verloor (*gnomAD missense Z 4.20 (VCF) → 3.52 (gene profile)*);
- `annotations`, als de hash veranderde.

`evidence_from` en `evidence_to` geven het transcript, de consequentie, de frequenties, de scores en de missense-Z met zijn bron, zoals bevroren en zoals nu. Regels:

- Een bevroren waarde die verschoof, is drift, ook onder dezelfde hash.
- De missense-Z van een missense-variant valt niet onder die hash: een vernieuwing van de genprofielen kan hem doen verschuiven terwijl de hash gelijk blijft. Een classificatie van een andere variant (bv. een stopcodon) bevroor er geen, en verschuift daardoor nooit.
- De controle vergelijkt elke waarde die het snapshot bevroor, en getallen op hun waarde (JSONB bewaart de cijfers van een getal, niet of het een geheel getal was).
- De huidige waarden worden gelezen zoals een classificatie die nu bewaard wordt ze zou bevriezen. Een variant die als missense-variant geclassificeerd werd en dat niet meer is, noemt zijn consequentie en zijn missense-Z als verdwenen (*gnomAD missense Z 2.95 → n/a*).
- De missense-Z van de missense-varianten van de familie wordt in één query gelezen.

Bij een SV of CNV vergelijkt de controle het snapshot met de rij van de caller waarvan het bevroren werd (`evidence.source`), het bewijs als geheel, op zijn hash. De oproep van een andere caller is ander bewijs, geen wijziging van dit bewijs.

- `drifted` zegt ook wat verschoof (`changed`): de genen, de pLI, de overerving, het type, de positie of de overige annotatie, met de waarde ervoor en erna.
- `variant_missing` betekent dat de oproep van die caller niet meer in de data zit, ook als een andere caller de SV nog oproept. De driftbanner en de ondertekende versie noemen die caller (*its needlr call is no longer present*).
- Bevroren bewijs dat geen caller noemt, telt als `unknown`.

De controle vergelijkt alleen de velden die het snapshot bevroor, en leest de SV zoals de SV-pagina hem leest. De header van de CNV-dialoog noemt de caller van de rij waarop hij geopend werd; de SV-tabel toont de kolom *Source* standaard.

**Waar in de code:** `backend/app/services/classification_drift_service.py` (de statuslogica in `_diff`; voor SV's en CNV's `evaluate_structural_classification_drift`, met `fetch_structural_variant_records`, `frozen_evidence_source` en `diff_structural_evidence` uit `structural_variant_evidence.py`).

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `frontend/src/content/docs/acmg-classification.md` | De regels per criterium, zoals het labo ze leest |
| `frontend/src/lib/acmg/` | Catalogus, evaluator (ook mtDNA) en puntenschaal |
| `frontend/src/lib/cnvAcmg/` | CNV-catalogi, evaluator en puntenschaal |
| `frontend/src/pages/families/AcmgClassificationModal.tsx` · `CnvAcmgClassificationModal.tsx` | De classificatiedialogen |
| `backend/app/services/acmg_points.py` · `cnv_acmg_points.py` | Toegelaten sterktes en bereiken, herberekening op de server |
| `backend/app/services/small_variant_review_pg.py` · `small_variant_review_acmg.py` | Reviews opslaan, ACMG controleren, het bewijssnapshot |
| `backend/app/services/structural_variant_review_pg.py` | CNV/SV-reviews en CNV-ACMG |
| `backend/app/services/structural_variant_evidence.py` | Het bewijssnapshot van een CNV-classificatie en de vergelijking ervan |
| `backend/app/services/small_variant_review_tags.py` | Systeemtags, eigen tags, rechten |
| `backend/app/services/classification_drift_service.py` | Drift |
| `backend/app/services/clinical_audit_service.py` | Het klinische auditspoor |
