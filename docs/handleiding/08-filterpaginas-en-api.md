# 8. Filterpagina's ↔ API

Dit hoofdstuk beschrijft hoe CoGA, nadat een pakket correct is geïmporteerd (hoofdstuk 6), de juiste varianten van een familie *filtert* en *toont*. Deel A volgt de hele keten voor de twee grootste datatypes: de *small variants* (SNV's en indels: puntmutaties en kleine inserties of deleties) en de *structurele varianten* (SV's: grote deleties, duplicaties, …). Deel B vat dezelfde keten samen voor de gespecialiseerde analyses: mitochondriaal DNA en sample-QC, Paraphase, repeat-expansies, monogene NIPT en PGT. De klinische regels voor labogebruikers staan in de app, onder *Docs*; dit hoofdstuk wijst aan waar ze in de code zitten.

Een **endpoint** is een URL waarop de backend luistert; een **queryparameter** is een `?sleutel=waarde`-paar in die URL, waarmee de frontend de filterkeuzes doorgeeft. Een **geparametriseerde query** geeft gebruikerswaarden apart mee in plaats van ze in de SQL-tekst te plakken (hoofdstuk 7).

## Het vaste stramien

Voor elk variantdatatype verloopt zoeken op dezelfde manier:

1. **Filter in de UI:** de analist vult een filterformulier in (frequentie, effect, genen, genotype per sample, tags, …).
2. **Queryparameters:** de frontend zet de keuzes om in een querystring en vraagt het endpoint op.
3. **Endpoint:** de router controleert de toegang en bouwt de context van de familie (hoofdstuk 2).
4. **Filterservice:** een service maakt er een geparametriseerde ClickHouse-query van, met paginering en een begrensde telling.
5. **Koppeling met Postgres:** de variantrijen worden aangevuld met reviewtoestand, tags, genmetadata en de frequentie in het eigen cohort.
6. **Weergave:** de browser toont het resultaat als tabel of kaarten.

**Waar in de code:** de routers in `backend/app/routers/`, de filterservices in `backend/app/services/clickhouse_family_variants.py` en `clickhouse_variant_queries.py`, de pagina's in `frontend/src/pages/families/`.

## Deel A — Small variants

### Het filterformulier

De pagina `FamilySmallVariantsPage.tsx` laadt de familie en de genpanels; de zoektoestand zit in `smallVariantSearch.ts` en het formulier in `SmallVariantFilterForm.tsx`. Het formulier heeft groepen filters:

| Groep | Voorbeelden |
| --- | --- |
| Locus en gen | genen, chromosoom met begin en einde, een lijst intervallen, transcript, uit te sluiten genen en intervallen |
| Overerving | overervingsmodel (de novo/dominant, recessief, X-gebonden, compound heterozygoot), genotype per familielid, *expanded carrier screening* |
| Kwaliteit per sample | genotypekwaliteit, leesdiepte, allelfractie, alt-diepte |
| Frequentie | maximale frequentie in gnomAD (exomen, genomen, popmax), TOPMed, aantallen homo- en hemizygoten |
| Effect | impact (HIGH, MODERATE, …), effecttermen, alleen canonieke of MANE-transcripten, alleen loss-of-function |
| ClinVar | in te sluiten en uit te sluiten status, en "een (waarschijnlijk) pathogene ClinVar-variant passeert het frequentiefilter" |
| Voorspellers | CADD, REVEL, SpliceAI, SIFT, PolyPhen |
| Panel en tweede hit | een genpanel (standaard het Mendeliome), en "alleen genen die ook door een SV geraakt worden" |
| Review | ACMG-klasse, tags, "heeft notities" |

Bij een nieuwe familie start de pagina met de fenotypeprioritering en het Mendeliome-panel als scope.

**Waar in de code:** `frontend/src/pages/families/SmallVariantFilterForm.tsx` en `smallVariantSearch.ts`.

### Van formulier naar verzoek

`buildSmallVariantQueryParams` zet de keuzes om in een querystring. Twee details zijn belangrijk voor de traceerbaarheid:

- **Standaardwaarden gaan expliciet mee.** De fenotypeprioritering en de ClinVar-uitzondering op het frequentiefilter staan standaard aan en worden altijd als `true` of `false` meegestuurd, zodat een uitgezette keuze in de URL bewaard blijft.
- **Een filter per sample** gaat als één parameter mee: `sample_filter=<sample>:<genotypes>:<kwaliteit>:<diepte>:<allelfractie>:<alt-diepte>`, bijvoorbeeld `child:0/1|1/1:20:10:0.2:5`.

De backend leest genotypes als **klassen**, niet als letterlijke tekst: *hom_alt* (alle allelen dezelfde ALT, ook een haploïde `1`), *het* (minstens één ALT, ook `1/2` of `./1`), *hom_ref* (alle allelen `0`) en *no_call*. Een filter op "Hom" vindt dus ook een hemizygote `1` op chrX bij een man of op chrM. Eén definitie bedient alle filters, tellingen en tracks.

**De novo bij een zoon op X en Y.** Buiten de pseudo-autosomale regio's heeft een man één X en één Y. Een de-novovariant daar staat als `1` of `1/1` in de VCF, nooit als `0/1`. Het de-novo/dominant-patroon telt zo'n call bij een aangedane man als één kopie. De strikte de-novocontrole volgt de overdracht: een zoon krijgt zijn X van zijn moeder en zijn Y van zijn vader, dus alleen die ouder moet betrouwbaar referentie zijn; de andere ouder mag de variant niet dragen (dat wijst op een artefact). De grenzen van de pseudo-autosomale regio's staan per assembly vast (GRCh38 en GRCh37); op een assembly zonder bekende grenzen gelden de diploïde regels.

**Waar in de code:** `frontend/src/pages/families/smallVariantSearch.ts`; de genotypeklassen in `backend/app/services/genotypes.py` (gespiegeld in `frontend/src/lib/genotypes.ts`); de PAR-grenzen in `backend/app/services/sex_chromosomes.py`.

### Het endpoint en de filterservice

`GET /api/families/{family_id}/small-variants` begrenst de paginagrootte, leest alle filters in via één gedeelde dependency (dezelfde als de CSV-export) en bouwt het toegangscheckpoint van de familie. Daarna kiest de service een weg:

- een **aanwezigheidscontrole** (`count_only`) voor het dashboard: "heeft deze familie small variants?";
- eerst de **afbakening**: panel, reviewselecties, intervallen en uitgesloten regio's. Kan de vraag niets opleveren (bv. een panel zonder genen), dan is het antwoord meteen een lege pagina. Een intervalregel die niet te lezen is (bv. een BED-regel of een einde vóór het begin), wordt met `422` geweigerd en bij naam genoemd; het formulier meldt zulke regels al vóór het zoeken;
- de **fenotypeprioritering** (standaard aan), met een cache (hoofdstuk 12);
- de **track-modus** voor de genoombrowser: boven een grens geeft die alleen de telling terug, zodat de track kan zeggen dat er te veel varianten zijn;
- anders filtert en pagineert **ClickHouse** zelf, of haalt de service een begrensde kandidatenset op en past in Python toe wat ClickHouse niet kan (gensymbolen, dragerscreening, overerving tussen varianten zoals compound-het).

**De query zelf** begint altijd met de familie en `sign = 1`, voegt de projectfilter toe en daarna elke filter, telkens als parameter. De rijen worden per variant ontdubbeld en op genomische positie gesorteerd; die volgorde is niet door de gebruiker te sturen (de sorteerknoppen in de tabel werken in de browser). De volledige annotatie per variant wordt per pagina apart opgehaald. De **telling** is begrensd: boven een grens toont de UI een "+"-teller in plaats van een dure volledige telling. Een te zware query geeft een `422` met de vraag de zoekopdracht te verfijnen.

**Waar in de code:** `backend/app/routers/families_small_variants.py`; `get_family_small_variants_page` in `backend/app/services/clickhouse_family_variants.py`; de WHERE-bouwers in `clickhouse_variant_queries.py`; de filterset in `family_variant_filters.py`.

### Aanvullen met Postgres

Voor elke pagina worden de rijen aangevuld met:

- de **review**: klasse, ACMG-criteria, tags en notitie uit Postgres (hoofdstuk 10);
- de **frequentie in het eigen cohort** over de toegankelijke projecten, zodat terugkerende artefacten opvallen;
- **genconstraint** uit de tabel `gene_info`;
- een markering als een **structurele variant** hetzelfde gen raakt (de "tweede hit", hieronder).

### De weergave

`SmallVariantResults.tsx` toont de teller, de keuze tussen tabel en kaarten ("Auto" kiest kaarten voor een klein aantal, anders een tabel), de CSV-export met dezelfde querystring, een waarschuwing als de zoekopdracht maar een deel van de callset las, en een melding als de rangschikking uit de cache komt. Die waarschuwing (`CandidateCapNotice.tsx`) komt uit twee vlaggen van het antwoord: `candidates_capped` (compound-het, recessief of dragerscreening las een begrensd kandidatenvenster, tussen 1.000 en 5.000 rijen naargelang de gevraagde pagina) en `ranking_truncated` (de prioritering rangschikte alleen haar venster). `candidate_limit` zegt na hoeveel kandidaten de zoekopdracht stopte; de teller is dan een ondergrens en de waarschuwing vraagt de filters te verfijnen met een regio, een genpanel of een gen. Compound-heterozygote paren verschijnen bovenaan als paarkaarten. De tabel (`SmallVariantTable.tsx`) is in de browser sorteerbaar op positie, gen, impact en, bij prioritering, de score.

### Compound heterozygoot, tweede hit en dragerscreening

Bij een recessieve aandoening is één heterozygote variant meestal niet oorzakelijk; twee verschillende heterozygote varianten in hetzelfde gen kunnen dat wel zijn, als ze **in trans** liggen (één op elk allel). CoGA toont zulke kandidaten daarom als **paar**, met hun fase: *in trans* of onbekend. Een paar ontstaat wanneer beide varianten heterozygoot zijn bij alle aangedane leden en geen niet-aangedaan lid beide draagt. De fase komt uit de reads (een gedeelde *phase set*), en anders uit de ouders in de stamboom: gaat elke variant terug op een andere ouder, dan liggen ze in trans. Een paar in cis valt weg: de reads leggen beide varianten op hetzelfde haplotype, of beide gaan terug op dezelfde ouder terwijl beide ouders voor beide varianten gegenotypeerd zijn. Een familielid dat geen van beide varianten draagt en geen ouder is, zegt niets over de fase. Dezelfde faseregel geldt voor de tweede hit, die een fase in cis wel toont.

Een **tweede hit** is een heterozygote small variant samen met een structurele variant (bv. een deletie) in hetzelfde gen: de deletie verwijdert de tweede kopie. Per familie houdt CoGA een index bij van welke genen door een SV geraakt worden; de SNV-pagina gebruikt die als markering en als filter. Elke wijziging aan de SV's van een familie (een import, een upload voor één sample, een verwijdering) verandert een versie in ClickHouse (`SV/family_data_version`); de volgende keer dat de pagina opent, bouwt CoGA de index daarom opnieuw op. De regels voor trans en cis staan in de app (*Docs → SNV + SV compound heterozygosity*, `frontend/src/content/docs/sv-second-hit.md`) en in `docs/snv-sv-compound-het.md`.

**Expanded carrier screening** is een reproductieve vraag: lopen twee partners samen risico op een aangedaan kind? CoGA toont dan alleen genen waarin **beide** partners een kwalificerende variant dragen. De keuze is alleen beschikbaar als de frontend een koppel in de familie herkent. Het verschil met compound-het: daar zitten twee hits bij één persoon; hier één hit bij elk van twee partners.

**Waar in de code:** de paren in `clickhouse_variant_queries.py` en `SmallVariantPairCards.tsx`; de faseregel via de ouders in `backend/app/services/compound_het_phase.py`; de SV-index in `backend/app/services/sv_gene_index_service.py`; de dragerscreening in `clickhouse_variant_queries.py` en `smallVariantSearch.ts`.

## Deel A — Structurele varianten

De SV-pagina volgt hetzelfde stramien, met eigen bestanden (`FamilyStructuralVariantsPage.tsx`, `structuralVariantSearch.ts`, `StructuralVariantFilterForm.tsx`). De filters omvatten type (DEL, DUP, …), lengte, bron, regiovlaggen, frequentie in controles en populatie, genconstraint, fenotypevelden, gen of panel, overerving en genotype per sample. Naast de gefilterde vraag doet de pagina één vraag zonder filters, om het totaal "All variants" te tonen.

Het endpoint is `GET /api/families/{family_id}/structural-variants`; de SV's van één sample vraag je op met de parameter `sample`. De verschillen met small variants:

| Aspect | Small variants | Structurele varianten |
| --- | --- | --- |
| Filteren | Vooral in ClickHouse, met annotatie-indexen | Deels in ClickHouse; anders eerst ophalen en in Python filteren, met een harde grens (50.000 SV's). Boven die grens is het totaal een ondergrens (`candidates_capped`, `candidate_limit`) en toont de pagina dezelfde waarschuwing als bij small variants. Een reviewselectie (tag, classificatie, notitie), zoals de lijst van gerapporteerde SV's van het rapport, gaat als variant-id's mee in de SQL (`_structural_variant_where_clauses`), vóór de grens: die kan een gerapporteerde SV dus niet afsnijden |
| Cytoband | — | Uit de Postgres-tabel `chromosomes` |
| Review | `small_variant_review_pg.py` | `structural_variant_review_pg.py` |
| Track-modus | Ja | Ja, zonder review en cytoband |

**Waar in de code:** `backend/app/routers/families_structural_variants.py`; `get_family_structural_variants_page` in `clickhouse_family_variants.py`.

## Veiligheid en traceerbaarheid in deel A

- **Toegang aan de poort.** Elk endpoint vraagt `get_current_user` (uploads en tagbeheer `get_current_admin_user`) en doorloopt het toegangscheckpoint van de familie.
- **Scoping in elke query.** De WHERE-bouwers beginnen altijd met de familie en voegen de projectfilter toe. Daarnaast beperkt een filter de rijen tot de samples die de gebruiker mag zien; is die lijst leeg, dan krijgt de query een voorwaarde die niets oplevert. Parameters manipuleren helpt dus niet.
- **Geen injectie.** Waarden gaan alleen als parameter mee; tabelnamen komen uit een veilige functie; paginagroottes en paginanummers worden begrensd.
- **Een mislukte vraag is geen leeg resultaat.** Mislukt een zoekopdracht, dan tonen de filterpagina's "Could not load … — this is not an empty result", met de reden en een knop om opnieuw te proberen; tellers tonen dan "—", niet 0. Een mislukte panellijst zegt dat de standaard-Mendeliome-scope niet is toegepast.
- **De export is wat de analist zag.** De CSV-export gebruikt dezelfde filters als het scherm, en cellen worden ontdaan van formule-injectie (`csv_safe_cell` in `backend/app/core/csv_export.py`).

## Deel B — Gespecialiseerde analyses

### Mitochondriaal DNA en sample-QC

**mtDNA.** Mitochondriaal DNA erft alleen via de moeder en komt in vele kopieën per cel voor. Een variant kan dus in een deel van de kopieën zitten (**heteroplasmie**) of in bijna alle (**homoplasmie**). De mtDNA-pagina toont per variant de allelfractie per familielid en deelt elke call in als homoplasmisch, heteroplasmisch, laag niveau of referentie, met vaste drempels in de service. Ze koppelt elke positie aan een mitochondriaal gen of gebied, met een link naar MITOMAP, en toont of een variant via de moederlijn wordt gedeeld, met de moeder die de stamboom aan de proband koppelt (een variant alleen bij de vader is verdacht). Classificeren gebruikt dezelfde ACMG-dialoog als small variants, met een eigen mtDNA-evaluator (hoofdstuk 10). Per sample toont de pagina dekking en een QC-status; de grenzen komen uit het QC-profiel van de familie (*Admin → Sequencing QC Thresholds*, hoofdstuk 15). Zonder ingestelde grens telt een metriek niet mee: de status is dan *skip*, geen *pass*.

**Sample-QC.** Deze pagina controleert of de juiste data aan de juiste persoon hangt: een verwisseld sample of een verkeerde stamboom geeft anders ongemerkt een foute overervingsredenering. Drie controles rekenen op de genotypes zelf: **geslacht** (uit de heterozygotie op X, waarbij een haploïde call als homozygoot telt, vergeleken met het geregistreerde geslacht), **verwantschap** (KING-kinship en IBS0 per paar, vergeleken met de stamboom) en de **Mendel-foutgraad** (genotypes die het kind niet van zijn ouders kan hebben). Elke controle geeft *pass*, *warn*, *fail* of *skip*; de slechtste bepaalt het geheel. Welke controles draaien, hangt af van de toepassing (WGS-familie, PGT, NIPT, koppel, één sample); bij NIPT komt het vaderschap uit de cfDNA-analyse, en tellen alleen sites met een bruikbare call van de vader. Kan een controle niet draaien, dan verschijnt een waarschuwing, geen stille *pass*. Een *fail* blokkeert het ondertekenen tot hij erkend is (hoofdstuk 11). De regels voor het labo staan in de app (*Docs → Sample-integrity QC*, `frontend/src/content/docs/sample-qc.md`).

**Waar in de code:** `backend/app/services/mitochondrial_analysis.py`; `sample_integrity_qc.py` (de rekenkern) en `sample_integrity_service.py` (laden en toepassingsprofielen); de pagina's `FamilyMitoDNAAnalysisPage.tsx` en `FamilySampleQcPage.tsx`.

### Paraphase: paraloge genen

Sommige belangrijke genen liggen in regio's met bijna-identieke kopieën, zoals *SMN1/SMN2* of *PMS2*; gewone variant-calling faalt daar. **Paraphase** (een externe tool) bepaalt voor die regio's het kopieaantal, de reads per onderscheidende positie en de haplotypes. CoGA bewaart het resultaat in `sample_paraphase_results` en duidt het met een catalogus van medische regio's (`data/ref-data/paraphase-medical-regions.json`). Die legt per locus vast welke velden klinisch tellen, welke aandoeningen erbij horen en, waar mogelijk, een regel die uit het kopieaantal *normal*, *carrier* of *pathogenic* afleidt; zonder regel wordt een afwijkend signaal *review*. De pagina toont per locus een kaart met de duiding en per sample de status.

**Waar in de code:** `backend/app/services/paraphase_pg.py`; de pagina `FamilyParaphasePage.tsx`.

### Repeat-expansies (TRGT)

Bij een repeat-expansieziekte wordt een kort motief te vaak herhaald (bv. `CAG` in *HTT*). **TRGT** bepaalt per locus de allellengtes. Bij de import wordt elk locus gekoppeld aan de catalogus `repeat_loci` (een ingebouwde lijst plus STRchive, met drempels per locus), en krijgt elk allel de status *normal*, *intermediate* of *pathogenic*. De calls staan in `repeat_expansions`; de versie van TRGT uit de VCF-header komt in het annotatiemanifest. Bij het tonen worden de allelen opnieuw beoordeeld tegen de **huidige** drempels, zodat een bijgewerkte drempel meteen doorwerkt. Voor een X-gebonden locus bij een man vervalt het tweede (fantoom-)allel, want een man heeft één X. Uploaden los van een pakket kan alleen een beheerder.

**Waar in de code:** `backend/app/services/repeat_expansion_pg.py` en `repeat_expansion_catalog.py`; de pagina `FamilyRepeatExpansionsPage.tsx`.

### Monogene NIPT

Monogene NIPT onderzoekt een zwangerschap op enkelgenaandoeningen via **celvrij DNA (cfDNA)** in het bloed van de moeder, samen met een sample van de vader. Het cfDNA is een mengsel: vooral maternaal DNA met een kleine **foetale fractie**. De foetus wordt nooit zelf gesequenced; zijn genotype volgt uit hoe ver de allelfractie in het cfDNA afwijkt van wat de moeder alleen zou geven. Een familie is een NIPT-familie als haar analysetype `monogenic_nipt` is en het cfDNA-sample de assay `nipt_cfdna` draagt.

CoGA deelt elke variant in bij één van acht categorieën (combinaties van de toestand bij moeder en foetus), schat de foetale fractie uit de sites die alleen de vader draagt (met een betrouwbaarheidsinterval), en classificeert elke variant met een statistisch model tegen de verwachte allelfracties. Het genotype van de vader beperkt de mogelijke categorieën; een call van de vader onder 10× telt als geen call. Vooraf vallen varianten van lage kwaliteit en bekende terugkerende artefacten (per assay bijgehouden in `nipt_artifact_variants`) weg; de pagina toont die trechter. De samenvatting en de variantlijst rekenen met dezelfde foetale fractie. De endpoints leveren een samenvatting, de geclassificeerde varianten (met de gewone small-variantfilters plus categorie, betrouwbaarheid en overervingspresets) en de dekking van de doelregio's. De variantlijst classificeert de eerste 5.000 varianten van een zoekopdracht, in genomische volgorde; komen er meer overeen, dan zegt de pagina dat (`total_is_estimated`, `count_limit`) en is het aantal een ondergrens. Het rapport gebruikt alleen het genpanel en het gen van de zoekopdracht: het noemt ze onder *Scope* en zegt dat geen andere filter geldt. Het toont hoogstens 500 kandidaten. Toont het er minder dan er in zijn scope vallen, dan zegt het op het scherm en op de afdruk hoeveel van hoeveel, waar de lijst stopt en waarom. Het rapport draagt een disclaimer dat de classificaties bevestigd moeten worden met een invasieve diagnostische test. Is de familie geen NIPT-familie, dan zegt de pagina dat.

De klinische uitleg staat in de app (*Docs → Monogenic NIPT (cfDNA)*, `frontend/src/content/docs/monogenic-nipt.md`), het ontwerp in `docs/monogenic-nipt.md`.

**Waar in de code:** `backend/app/services/nipt.py` (het trio), `nipt_analysis.py` (de rekenkern), `nipt_service.py` (koppeling met de data), `nipt_coverage.py` en `nipt_artifact_pg.py`; `backend/app/routers/families_nipt.py`; de pagina's `FamilyNiptPage.tsx` en `FamilyNiptReportPage.tsx`.

### PGT: haplotype-segregatie

Bij preïmplantatie genetische testing (PGT) maakt een koppel, of één ouder met een donor, embryo's, en een bekende aandoening segregeert in de familie. De vraag per embryo: erfde het het ziekte-haplotype? CoGA kleurt de twee haplotypes van elk lid naar de grootouderlijke stamvader waarvan ze afstammen (*identity by descent*), en bepaalt welk haplotype het ziekte-allel draagt. De track heeft twee lagen: **gekleurde haplotypeblokken** (de opgekuiste interpretatie) en de **ruwe gefaseerde markers**, één punt per informatieve site, bewust zonder groeperen of middelen, zodat faseringsruis en de exacte plaats van een recombinatie zichtbaar blijven.

De opgeslagen blokken zijn alleen betekenisvol voor de kernfamilie. Het rolveld van een familielid is plat (`mother`/`father` geldt ook voor een grootouder), dus CoGA herberekent de kleur van verwanten uit de ruwe genotypes en volgt de stamboom naar buiten. Een lid dat niet zeker te plaatsen is, blijft grijs, nooit fout gekleurd; op de geslachtschromosomen en mtDNA blijven verwanten grijs. Een familie met **één bekende ouder** (bv. met een donor) wordt ondersteund: de donorzijde blijft grijs. De ruwe markers worden alleen voor de kinderen van de indexouders berekend, met per kind een QC (het aantal informatieve sites en de Mendel-foutgraad). De indeling van elk embryo (*affected or at risk*, *carrier*, *unaffected non-carrier* of *uninformative*) gebeurt in de frontend. Een indeling die zegt dat het embryo een ziekte-haplotype niet draagt, steunt op het eigen haplotype van het embryo: dat moet over de hele regio van interesse te zien zijn, aan elke ouderkant die de indeling nodig heeft. Anders is de indeling *uninformative*. Een man heeft op chrX één kopie, behalve in de pseudo-autosomale regio's (PAR1 en PAR2): daar draagt hij ook de kopie van zijn vader, en telt de indeling beide. De backend markeert elk haplotypeblok `hemizygous_in_males` met de PAR-grenzen van de assembly van de familie, uit dezelfde tabel als de variantfilters (`sex_chromosomes.py`). Kent CoGA die grenzen niet, dan leest het een man overal met twee kopieën. Is bij X-gebonden recessieve overerving het geslacht van het embryo niet ingevuld, dan deelt CoGA het in als zoon en als dochter. Verschillen die twee, dan neemt het geen geslacht aan: *affected or at risk* als een van beide dat is, anders *uninformative*. Waarschuwingen melden een recombinatie dicht bij de regio van interesse, een indeling zonder uitkomst, en een ontbrekend geslacht waarvan de indeling afhangt. De pagina *ROI marker review* toont de ruwe genotypes rond die regio, zodat de analist een verrassende uitkomst kan natrekken.

Deze keuzes staan in `docs/haplotype-segregation-analysis.md`; de uitleg voor het labo in de app (*Docs → Haplotype segregation analysis*, `frontend/src/content/docs/haplotype-segregation.md`).

**Waar in de code:** `backend/app/services/haplotype_lineage_service.py` (kleuring, ook met één ouder) en `phased_marker_service.py` (ruwe markers en QC); `frontend/src/lib/haplotypeRisk.ts` en `embryoSegregation.ts` (embryo-indeling); de pagina `FamilyRoiMarkersPage.tsx`.

## Veiligheid en traceerbaarheid over alle analyses

- **Toegang:** alle endpoints vragen `get_current_user` (de TRGT-upload `get_current_admin_user`) en bouwen hun data via het toegangscheckpoint van de familie.
- **Herkomst:** de TRGT-versie komt uit de VCF-header in het annotatiemanifest; NIPT-artefacten zijn per assay bijgehouden, met hun bron (gecureerd of automatisch).
- **Poort vóór het rapport:** een sample-QC-fout moet erkend worden voordat een rapport ondertekend kan worden (hoofdstuk 11).
- **Berekend, niet ingetypt:** de NIPT-categorieën, de foetale fractie, de haplotypekleuring en de embryo-indeling zijn berekend uit de VCF's en de stamboom, dus herleidbaar tot de invoer.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `backend/app/routers/families_small_variants.py` · `families_structural_variants.py` | Endpoints voor small en structurele varianten |
| `backend/app/services/clickhouse_family_variants.py` | De filterservices en de weg van een vraag |
| `backend/app/services/clickhouse_variant_queries.py` | Geparametriseerde WHERE-bouwers, paren, dragerscreening |
| `backend/app/services/family_variant_filters.py` · `genotypes.py` | De filterset en de genotypeklassen |
| `backend/app/routers/families_tracks.py` · `families_nipt.py` · `families_reports.py` | mtDNA, Paraphase, repeats, haplotypes; NIPT; sample-QC |
| `backend/app/services/mitochondrial_analysis.py` · `sample_integrity_service.py` | mtDNA-duiding; sample-QC |
| `backend/app/services/paraphase_pg.py` · `repeat_expansion_pg.py` | Paraphase; repeat-expansies |
| `backend/app/services/nipt_service.py` · `nipt_analysis.py` | Monogene NIPT |
| `backend/app/services/haplotype_lineage_service.py` · `phased_marker_service.py` | PGT: kleuring en ruwe markers |
| `frontend/src/pages/families/FamilySmallVariantsPage.tsx` · `smallVariantSearch.ts` | Small-variantpagina en zoektoestand |
| `frontend/src/pages/families/FamilyStructuralVariantsPage.tsx` | SV-pagina |
| `frontend/src/lib/haplotypeRisk.ts` · `embryoSegregation.ts` | Embryo-indeling |
