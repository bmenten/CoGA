# 9. Visualisaties (chromosome, genome, circos, IGV)

CoGA toont genomische data in vier weergaven: het **genoomoverzicht**, de **weergave per chromosoom**, de **Circos-plot** en de ingebedde **IGV-browser**. Ze steunen op *tracks*, het *ideogram* en de *stamboom*. Per weergave staat hier welk endpoint de data levert, hoe er getekend wordt en hoe de getoonde regio bepaald wordt. De uitleg voor gebruikers staat in de gebruikersgids in de app (`frontend/src/content/docs/user-guide/visualization.md`).

Enkele begrippen:

- **Track:** één horizontale strook die één soort gegeven over een genomisch bereik toont.
- **SVG** is een vectorformaat (scherpe vormen); **canvas** is een pixelvlak (sneller bij heel veel punten); **D3** is een JavaScript-bibliotheek die data op SVG of canvas tekent.
- **Downsampling:** uit een grote dataset een representatieve kleinere selectie kiezen, zodat het tekenen snel blijft.

## De twee hoofdpagina's

**Genoomoverzicht** (`GenomeOverviewPage.tsx` met `GenomeOverviewWorkspace.tsx`). Per geselecteerd familielid toont het genoombrede tracks voor dekking, APCAD, structurele varianten, haplotypes en repeat-expansies, met onderaan een ideogram per chromosoom. De chromosomen 1–22, X en Y liggen achter elkaar op één as. Een gesleepte selectie opent die regio in de weergave per chromosoom.

Het mitochondrion ontbreekt in het overzicht, net als in de Circos-plot: 16,6 kb van een genoom van 3,1 Gb is op een track van 1200 pixels zo'n 0,006 pixel breed. Het staat dus niet op de as, in de chromosoomstrook of in de lijst *Chromosomes* van de zijbalk, en het overzicht vraagt er geen track voor op. De keuzelijst van de weergave per chromosoom en de mtDNA-pagina tonen het wel (beslissing van de eigenaar, 2026-10-28).

**Weergave per chromosoom** (`ChromosomeViewPage.tsx` met `ChromosomeViewWorkspace.tsx`). Bovenaan staat het volledige ideogram, met het getoonde venster in het rood. Daaronder volgen per lid de sample-tracks (dekking, APCAD, SV's, small variants, haplotypes, repeats). Onderaan staan de referentietracks (genen, klinische CNV's, DGV, blacklist, segmentale duplicaties) en een uitvergroot ideogram van het venster. Knoppen zoomen en schuiven; een veld springt naar een gen of locus.

Heeft de familie een regio van interesse (ROI), dan ligt die in beide weergaven als een lichte rode band met randen over de tracks (`RoiMarkerOverlay.tsx`).

**Welke tracks een lid krijgt.** De frontend vraagt eerst op welke tracks een sample heeft (`GET /api/families/{family_id}/track-availability`), zodat geen lege strook verschijnt voor data die de familie niet heeft. Met een genpanel volgt de beschikbaarheid de lijsten van hoofdstuk 8:

- **SV's.** Komt de weergave van de SV-pagina met een panel, dan krijgt een lid een SV-track als het panel een van zijn SV's overhoudt, volgens de regel van de SV-lijst: de annotatie noemt een gen van het panel, of de SV overlapt een panelregio.
- **Small variants.** Een lid krijgt een track als de small-variantlijst met dat panel in het venster een variant van hem toont: een gen van het panel in de annotatie (de gensymbolen van de rij of de genindex) of een positie in een panelregio, hoe groot het panel ook is. De beschikbaarheid bouwt die voorwaarde met de functie van de lijst: `_small_variant_present_sample_names` in `clickhouse_family_variants.py` roept `_small_query_filter_parts` aan. De regio's gaan één keer als parameter naar ClickHouse; een join op vensters van 64 kb zoekt ze bij de eigen rijen van de familie. Een panel zonder genen en zonder regio's voor de assembly van de familie houdt in de lijst niets over en biedt dus geen track aan.

    De vraag is één SELECT over de rijen van de familie, die per rij de leden met een ALT-allel uit de calls haalt. Met het Mendeliome duurt ze 0,04 s over een venster van 10 Mb (gemeten op ClickHouse 26.8; metingen in `docs/variant-ranking-cache.md`, *A panel of any size*).

**Waar in de code:** `frontend/src/pages/genome/`; de beschikbaarheid in `backend/app/routers/families_tracks.py`.

## Het ideogram

Het ideogram is de gestreepte chromosoomtekening. De cytobanden komen uit Postgres (`GET /api/chromosomes/{assembly}/{chrom}`) en worden in SVG getekend, met een kleur per kleuringscode (bv. `gpos100`, `acen` voor het centromeer). Op het volledige ideogram selecteert de gebruiker een bereik; het uitvergrote ideogram toont alleen het getoonde venster. Op het genoomoverzicht is een chromosoom maar enkele pixels breed; daar worden de banden per pixelgroep samengevoegd tot de overheersende kleuring.

**Waar in de code:** `frontend/src/components/visualizations/Ideogram.tsx` en `ZoomedIdeogram.tsx`; de hulpfuncties in `frontend/src/lib/ideogram.ts` en `stainColors.ts`.

## De sample-tracks

- **Dekking en segmenten.** Een canvas toont de log-ratio van de dekking als stippen per bin, met de CNV-segmenten als lijnen erover. De kleurdrempels (winst, verlies) zijn **per browser** instelbaar. De legende toont ze altijd en markeert afwijkende waarden, omdat een andere werkplek dezelfde data anders kan kleuren.

    De data komen uit de interval-tabel in ClickHouse: het overzicht vraagt alle chromosomen in één keer op (`GET /api/bed/{sample_id}/coverage/batch` en `…/segments/batch`), de weergave per chromosoom één chromosoom (`GET /api/bed/{sample_id}/coverage` en `…/segments`). Elke CNV-caller krijgt een eigen track; de weergaven noemen bij dekking en segmenten de caller (`source`). Heeft het staal in het gevraagde gebied dekking van meerdere callers, dan wordt een dekkingsvraag zonder caller geweigerd (400). De leesroutes antwoorden in JSON, ook zonder `format`; een vraag naar het tekstformaat krijgt 422.

    `GET /api/families/{family_id}/track-availability` noemt de callers, met per dekkingstrack de omrekening (`coverage_normalizations`) en de inhoud (`coverage_contents`: `read_depth`, `log2_ratio` of `unknown`, volgens de regel van de dieptelezers, hoofdstuk 6). Elke track komt op de as van zijn inhoud (`coverageScale` in `frontend/src/lib/coverageSources.ts`, de prop `scale` van `CoverageSegmentsChart`):

    - een **log2-ratio** op de log2-ratio-as, met de kleuren voor winst en verlies en de segmenten van de caller;
    - het **maternale plasma van een conventionele NIPT** (ccnv, onder de eigen bron van zijn zwangerschap, `ccnv_p…`, die de weergaven als `ccnv` lezen: `sourceBase` in `frontend/src/lib/pregnancySources.ts`), ook een log2-ratio, op een eigen NIPT-as (±0,3): een foetale trisomie verhoogt het plasma maar met log2(1 + FF/2), zo'n +0,08 bij 12 % foetale fractie, wat de kleurdrempels (±0,35) nooit zouden kleuren. Elk segment en zijn bins krijgen de kleur van de call van ccnv (winst, verlies, of paars voor een call die ccnv discordant noemt met de foetale fractie), en gestippelde lijnen tonen waar een foetale trisomie en monosomie liggen bij de effectieve foetale fractie van ccnv, op chromosoom 1–22 en X (`frontend/src/lib/ccnvTracks.ts`). De kop van de track toont die foetale fractie, het foetale geslacht en de geslachtschromosoomcall (`ccnv` in de track-availability), en alle schattingen bij hover;
    - **leesdiepte** (een dekkings-BED die als leesdiepte verklaard is, of de diepte van HiFiCNV, zoals gelezen bewaard omdat het bestand geen bruikbare autosomale mediaan gaf) op een diepte-as van 0 tot een rond getal boven het 99ste percentiel van de getoonde waarden, zonder die kleuren of segmenten, die log2-ratio's zijn;
    - waarden van **onbekende soort** (een dekkings-BED zonder verklaring, een omrekening die de viewer niet kent) op hun eigen bereik, zonder as.

    De kop van de track zegt waarom: rustig voor de diepte van HiFiCNV, in de waarschuwingskleur voor onbekende waarden (`coverageScaleNote`). Een segmentenlezing levert elk segment met de waarden die zijn caller ernaast geeft (`details`: bij ccnv de call, z-score, bins, verwachte ratio, impliciete foetale fractie, discordantie en oorsprong, bij WisecondorX de z-score); de weergaven tonen ze boven het segment onder de muis.

- **APCAD.** Een spreidingsdiagram van de B-allelfrequentie, met twee homozygote banden en een heterozygote middenband: het signaal dat ouderlijke haplotypes onderscheidt. Data: `GET /api/bed/{sample_id}/apcad` en `…/apcad_pcf`, op het overzicht hun variant `…/batch`.

    Een sample heeft miljoenen markers, dus kiest de server een selectie: alleen informatieve markers die de kwaliteitsfilter doorstonden, over de chromosomen verdeeld naar het aantal opgeslagen sites, zodat elk chromosoom zijn eigen dichtheid per Mb houdt. Binnen een chromosoom komen de beste eerst, zo verdeeld dat alle banden zichtbaar blijven. Een rangschikking op kwaliteit over het hele genoom zou een chromosoom dichter tekenen dan zijn sites zijn: de gezamenlijke variant-calling scoort de markers van een chromosoom hoger waar één sample van de familie er extra reads heeft (bv. een embryo met een trisomie), en dat voor alle embryo's.

    Heeft een track geen enkele marker met een ouderlijke oorsprong (de minor-allelfractie van HiFiCNV, uit een bigWig), dan toont ze haar ongefaseerde markers, op een as van 0 tot 0,5. Een embryo van de PGT-pipeline met haar APCAD-tabel toont de sites die die run las (ouders homozygoot voor tegengestelde allelen): bij een euploïd embryo een middenband rond 0,5 per ouder, zonder de homozygote banden van een trio-VCF.

- **Ouderlijke oorsprong** (`ParentalOriginChart.tsx`). Voor een embryo met de segregatietabel van de PGT-pipeline, in beide weergaven; standaard uit, aan te vinken als *Parental origin (aneuploidy)*. Data: `GET /api/bed/{sample_id}/apcad_segregation` en `…/batch`, enkel lezen.

    Getekend zoals de eigen figuren van de pipeline, in twee panelen: *Mother heterozygous* (groep 2: moeder heterozygoot, vader homozygoot) en *Father heterozygous* (groep 3). Elke site staat op de allelfractie van het embryo (0 tot 1), gekleurd naar het homozygote allel van de andere ouder: amber voor het referentieallel (2B, 3B), de kleur van de heterozygote ouder voor het alternatieve (2A, 3A). De server bewaart elke site op de fractie van het allel dat de homozygote ouder mist, met haar groep (`group`); de grafiek rekent daaruit de allelfractie terug. In het genoomoverzicht staan alle chromosomen naast elkaar, zodat een aneuploïd chromosoom tussen de andere opvalt.

    Zo leest u een paneel:

    - banden op 0, ½ en 1: één homoloog van die ouder (disomie);
    - alleen ⅓ en ⅔, niets op 0 of 1: beide homologen (heterodisomie);
    - 0, ⅓, ⅔ en 1: twee kopieën van één homoloog (isodisomie), of in een trisomie het paneel van de ouder die één kopie van drie gaf.

    Tegen het centromeer gelezen wijst heterodisomie bij het centromeer op meiose I; isodisomie bij het centromeer die voorbij een crossover heterodisoom wordt, op meiose II; isodisomie over het hele chromosoom op een mitotische fout. CoGA tekent de sites en maakt er geen indeling uit.

- **Small variants.** Eén teken per variant van het sample (`GET /api/families/{familyId}/small-variants` in track-modus, met een bovengrens). De tekens (`frontend/src/lib/smallVariantMarks.ts`):

    - een variant die ClinVar (waarschijnlijk) pathogeen noemt: een grotere rode ruit, welke impact ze ook heeft;
    - elke andere variant: een stip van 6 px breed, waarvan de kleur de klasse geeft: groen voor ClinVar (waarschijnlijk) benigne, dan naar impact oranje voor HIGH, geel voor MODERATE en grijs voor LOW, MODIFIER of geen;
    - een reviewtag: een ring in de kleur van de tag rond het teken.

    De stippen verschillen alleen door hun kleur; de tooltip noemt de impact en de ClinVar-klasse, en de toegankelijke naam van de track telt de ruiten en de HIGH-varianten. Het opvallendste teken ligt bovenop: grijs eerst, dan groen, geel, oranje en tot slot de ruiten.

    Is het sample geen vader of moeder en heeft de familie minstens één ouder, dan splitst de track in rijen naar ouderlijke oorsprong (vaderlijk, onbepaald, moederlijk): met beide ouders volgens hun genotypes, anders volgens de fase van het kind.

    Tot een venster van 250 kb tekent de track elke variant. In een breder venster vraagt ze met `salient_only` alleen de varianten op die ze opvallend tekent: ClinVar (waarschijnlijk) pathogeen, of HIGH- of MODERATE-impact op een transcript en niet ClinVar (waarschijnlijk) benigne (`_small_salient_condition` in `clickhouse_variant_queries.py`, een filter van `SmallVariantQueryFilters` zoals de andere, met zijn geval in de pariteitstest). Een WGS-trio heeft zo'n 1,5 varianten per kb per lid: een venster van 2 Mb zou anders duizenden varianten per lid met hun volledige annotatie lezen, om ze als grijze stippen te tekenen. De kop van de track zegt het (`SMALL_VARIANT_ALL_MARKS_MAX_SPAN` en `smallVariantTrackScope` in `frontend/src/lib/trackSampling.ts`).

- **Structurele varianten.** Het overzicht gebruikt `SvTrack` (canvas, met een berekende hover), de weergave per chromosoom `VariantTrack` (SVG-vormen met eigen muisafhandeling). Beide halen `GET /api/families/{familyId}/structural-variants` op, in track-modus voor het sample van de track. Elke SV die de SV-lijst toont, wordt getekend, in een van zes rijen: DEL, DUP, INV, INS, BND en onderaan *Other*.

    - Een SV die een kopie verwijdert, staat bij DEL; een die er een toevoegt, bij DUP. De backend zegt per SV welke van de twee (`copy_change`), met de regel van de SV-rangschikking en de tweede hit (`sv_copy_change.py`), uit de call van het sample van de track. Zo staat een `DEL:ME` bij DEL en een `DUP:TANDEM` bij DUP.
    - Een `CNV` staat bij DEL of DUP volgens zijn kopieaantal; een man heeft op chrX en chrY buiten de PAR's één kopie. Een CNV zonder opgeslagen kopieaantal telt als verlies, zoals in de rangschikking.
    - Elke andere SV staat bij zijn type zonder subtype (een `INS:ME` bij INS), een `TRA` bij BND. Een type dat geen rij noemt (bv. `CPX`) en een CNV die geen kopie verandert, staan in de grijze rij *Other*.

    DEL, DUP en *Other* zijn balken, INV een omlijnde balk, INS een verticale streep en BND een driehoek. De tooltip noemt het type zoals opgeslagen, bij een CNV met de kopieverandering waarmee hij getekend is, en per call het kopieaantal (CN) als de call er een heeft. Ze schrijft het chromosoom zoals de kop het noemt (`chr1`), hoe het ook opgeslagen is.

    Het overzicht legt een SV op zijn chromosoom zoals de as het noemt (`normalizeChrom` in `frontend/src/lib/chromosomes.ts`), hoe de VCF het ook schreef (`chrX` of `X`). De weergave per chromosoom van chrM toont een SV op het mitochondrion, zoals de mitochondriale SV's (callset `mito_sv`, opgeslagen als `M`); het overzicht laat het mitochondrion weg en tekent of telt zo'n SV dus niet.

- **Haplotypes.** Op het overzicht `GenomeHaplotypeTrack`, per chromosoom `HaplotypePhasedTrack`, met data uit `GET /api/families/{family_id}/haplotypes` en `…/phased-markers`; het overzicht vraagt de haplotypes van alle chromosomen op met `…/haplotypes/batch`. De import slaat de naam van een blok op zonder `chr`; het overzicht legt een blok, zoals een SV, op zijn chromosoom zoals de as het noemt.

    - **De ruwe gefaseerde markers** worden per marker gekleurd, zonder groeperen; de haplotypetrack is de opgekuiste versie (`docs/haplotype-segregation-analysis.md`). De markeroverlay verschijnt alleen als beide ouders aanwezig zijn, de gebruiker ze aanzet en het venster 5 Mb of kleiner is (`PHASED_MARKER_MAX_SPAN`); in een breder venster zegt de kop van de track dat de markers bij inzoomen verschijnen.
    - **De kop van een kind** noemt naast de tracknaam zijn informatieve sites in het venster, of, boven een Mendel-foutratio van 2 %, die fouten in het rood (`HaplotypeMarkerNote.tsx`). De kop en de track lezen dezelfde markers, uit één aanvraag (`usePhasedMarkers`).
    - **Het risicohaplotype** wordt afgeleid in de regio van interesse. Zijn baan krijgt boven en onder een rand in de risicokleur, doorlopend voor aangedaan en gestreept voor drager, en in de weergave per chromosoom een lichte tint van die kleur achter de band, die zijn homoloogkleur houdt (`drawHaplotypeRiskLane` in `frontend/src/lib/haplotypeCanvas.ts`). Op het overzicht wordt het alleen op het chromosoom van die regio getekend, omdat de labels van de homologen per chromosoom gelden. Zonder regio van interesse tekent het overzicht geen risico en toont het de status "not assessed".
    - **Een rechtgezette fase.** Een ononderbroken lijn met een driehoekje (in de waarschuwingskleur) markeert waar CoGA de fase van een ouder heeft rechtgezet. Daar wisselden (bijna) alle kinderen van het koppel tegelijk van haplotype aan de kant van die ouder: eerder een fasewissel in de ouder dan crossovers. Vanaf dat punt lezen de haplotypeblokken, de lineagekleuring en de ruwe markers de fase van die ouder omgewisseld; de genotypes zelf blijven zoals ze zijn opgeroepen. Ligt zo'n correctie in of vlak bij de ROI, dan toont elk embryo de waarschuwing **phase corrected**.

- **Repeat-expansies.** Per locus een teken in de kleur van de status, uit `GET /api/families/{familyId}/repeat-expansions/sample/{sampleId}`. De statussen zijn normaal, review, niet beoordeeld, intermediair, premutatie en pathogeen; elke andere status kleurt als onbekend. *Niet beoordeeld* is een telling op een locus zonder drempels, buiten het benigne bereik dat de catalogus er vermeldt. De status is die van de repeatpagina: de call opnieuw beoordeeld tegen de catalogusrij die hem nu classificeert, met de opmerking bij twee verschillende chrX-allelen bij een man. Op het overzicht ligt een locus, zoals een SV, op zijn chromosoom zoals de as het noemt. De weergave per chromosoom van chrM toont een locus op het mitochondrion, hoe hij ook opgeslagen is (`chrM` of `M`); het overzicht laat het mitochondrion weg.

    - **Een gecorrigeerde repeattelling.** Heeft QA voor een locus een telcorrectie vastgelegd (hoofdstuk 8), dan tekent de track de status van de gecorrigeerde telling. De tooltip noemt de gecorrigeerde tellingen met die van TRGT tussen haakjes, en de correctie (`allele_repeat_counts`, `allele_trgt_repeat_counts`, `count_offset`).
    - **Een telling dicht bij een grens.** De tooltip noemt elke telling op 3 repeats of minder van een grens, met de grens, waarom, en wat er over de telling van TRGT bij het gen bekend is (`count_unit_warnings` in hetzelfde antwoord; hoofdstuk 8). De toegankelijke naam van de track telt zulke loci (*1 near a boundary*).

**Aneuploïdie en grote SV's bij PGT: de analist beoordeelt.** Op de segment-, kopieaantal- (dekking) en APCAD-tracks van elk embryo beoordeelt de analist zelf de aneuploïdie en de structurele varianten groter dan 10 Mb. CoGA toont die tracks, maar heeft geen automatische aneuploïdiedetectie (`TF-09a`, REQ-PGT-008). De grenzen van die beoordeling (mozaïcisme boven 35 %, SV's boven 10 Mb) moet de klinische validatie nog vaststellen (`TF-10` §3.2).

**Waar in de code:** `frontend/src/components/visualizations/` (`CoverageSegmentsChart.tsx`, `ApcadChart.tsx`, `SmallVariantTrack.tsx`, `SvTrack.tsx`, `VariantTrack.tsx`, de haplotype- en repeattracks); de tekens van de small variants in `frontend/src/lib/smallVariantMarks.ts`; de APCAD-selectie in `fetch_apcad_downsampled` (`backend/app/services/clickhouse_interval_tracks.py`).

## De referentietracks

Deze tracks hangen niet aan een sample en tonen referentieannotatie over het getoonde venster:

| Track | Endpoint | Toont |
| --- | --- | --- |
| `GeneTrack` | `GET /api/genes/{assembly}/{chrom}` | Genen met exonen en intronen (D3), één transcript per gen: het MANE Select-transcript (GENCODE, of HGNC's MANE-accessies voor een RefSeq-referentie), anders het canonieke transcript van Ensembl (GENCODE's tag `Ensembl_canonical`), anders het langste. Chevrons langs de intronen wijzen de richting van de transcriptie aan (› op de +-streng, ‹ op de −-streng). De tooltip noemt de streng, het transcript, boven een exon of intron zijn nummer geteld vanaf het begin van het transcript (*Exon 3 / 12*), en de panels waarin het gen zit |
| `CnvTrack` | `GET /api/cnvs/{assembly}/{chrom}` | Klinische CNV's; een klik opent de detailpagina |
| `DgvTrack` | `GET /api/dgv/{assembly}/{chrom}` | DGV-varianten; bij te veel overlap toont de server een dichtheidsprofiel in plaats van losse varianten |
| `BlacklistTrack` · `SegmentalDuplicationTrack` | `GET /api/blacklist/{assembly}/{chrom}` · `GET /api/segmental-duplications/{assembly}/{chrom}` | Onbetrouwbare zones en segmentale duplicaties |

**Waar in de code:** de componenten in `frontend/src/components/visualizations/`; de routers in `backend/app/routers/`.

## Groot en toch juist: downsampling

Hoeveel punten of segmenten een track hoogstens toont, hangt af van haar breedte in pixels (`frontend/src/lib/trackSampling.ts`). Dat tast de juistheid niet aan: CoGA vat alleen samen op overzichtsniveau, waar meerdere posities toch op dezelfde pixel vallen. Wie inzoomt, krijgt meer detail, en alle records van het venster zodra hun aantal binnen de grens van de track valt. Bij APCAD sturen kwaliteit en signaal de selectie, niet het toeval. De exacte beoordeling gebeurt nooit op deze tracks, maar op de gefilterde variantlijsten (hoofdstuk 8) en in IGV.

## Circos

De Circos-plot legt de chromosomen in een cirkel en tekent structurele varianten als bogen ertussen, met D3 op SVG. Elke SV wordt getekend zoals in de SV-tracks, met de calls van de hele familie: een kopieverlies als deletie, een kopiewinst als duplicatie, een `TRA` als breakend. Een SV die geen rij van de tracks noemt, is dun en grijs (*Other* in de legende).

De pagina neemt de assembly van het project van de familie (`useFamilyReference`, zoals de andere weergaven). Ze haalt de chromosomen van die assembly op, met hun lengtes en banden (`GET /api/chromosomes/{assembly}/details`), en zodra de assembly bekend is ook alle SV's van de familie. Ze tekent de chromosomen 1–22, X en Y die de assembly heeft, elk op zijn eigen lengte; contigs en het mitochondrion laat ze weg. Een SV ligt op zijn chromosoom hoe de naam ook opgeslagen is (`chrX`, `X` of `x`, via `normalizeChrom`). De tooltip van een SV schrijft de chromosomen zoals de weergave per chromosoom ze noemt (`chr1:10 → chr2:30`).

Kan de pagina de assembly niet tekenen, dan zegt ze waarom en tekent ze niets. Dat gebeurt bij:

- een familie zonder gekoppeld project;
- een familie of projectlijst die niet laadde (met een knop om het opnieuw te proberen);
- een assembly zonder chromosoomlengtes;
- een assembly die geen enkel van de chromosomen 1–22, X en Y heeft;
- een assembly met chromosomen die de plot niet kan plaatsen (chr23 en hoger, W, Z). Die noemt ze bij naam: zonder hen zou de plot voor het hele genoom doorgaan.

Een klik op een chromosoom opent de weergave per chromosoom; een klik op een translocatie (BND of TRA) opent het genoomoverzicht met beide chromosomen.

**Waar in de code:** `frontend/src/pages/genome/CircosPlotPage.tsx` en `frontend/src/components/visualizations/CircosPlot.tsx`.

## IGV: reads op basisniveau

Voor de fijnste controle, de afzonderlijke reads, bedt CoGA de **IGV**-browser in. `IgvViewer.tsx` vraagt twee lijsten op: de alignmentbestanden van alle familieleden, de proband eerst (`GET /api/cram/{familyId}/manifest`), en apart de signaalbestanden van de CNV-caller (diepte, allelfractie en kopieaantal: `GET /api/signal-tracks/{familyId}/manifest`). Mislukt die tweede vraag, dan laden de reads toch en meldt de pagina dat de signaaltracks ontbreken. Een sample met een alignment van meer dan één analyse, zoals kortelees- en daarna langelees-WGS van hetzelfde DNA, krijgt er een track van elk: het manifest geeft elke alignment met zijn analyse (`analysis_type`, ook als `?analysis=` in de adressen), en de track draagt de naam van het sample met die analyse (`alignmentTrackName` in `lib/alignmentTracks.ts`). In een familie met meerdere analyses noemt elke track haar analyse, ook als er van het sample maar één alignment getoond kan worden; een alignment die geen import vastlegde, heet *analysis not recorded*.

De backend serveert de bestanden met dezelfde toegangscontrole als de rest: elk endpoint van `cram.py` en `signal_tracks.py` controleert dat het sample tot een familie hoort die de gebruiker mag zien, en geeft anders `404`. IGV vraagt met *range requests* alleen de bytes die het nodig heeft. IGV krijgt nooit het adres van een bucket of share: elk byte komt via de backend.

Elk verzoek draagt het token dat de sessie op dat moment heeft: de functie `oauthToken` van elke track vraagt het aan `sessionAccessToken` uit `lib/api.ts`, zoals de API-client. Een Microsoft-token leeft ongeveer een uur; een token dat IGV maar één keer las, bij het openen, zou een lange sessie zonder tracks laten.

Waar de backend een bestand zoekt:

- **De pakketmap.** Eerst op het pad dat de import bij het sample vastlegde, in de pakketmap die de import las van de analyse die het vastlegde. Een pakket wordt gelezen waar het ligt, dus ook op een share of bucket die als importmap is gemount (`/data/share`, `/data/imports`), en in een map die anders heet dan de familie. Die pakketmap telt alleen als ze, symbolische koppelingen gevolgd, onder een `FAMILY_IMPORT_ROOTS`-map ligt en niet die map zelf is (`local_family_package_folder` in `services/family_package_source.py`). Het bestand moet in de pakketmap liggen, en een vastgelegde CRAM/BAM of index moet de extensie van dat bestand hebben. Een gewijzigde databankrij kan zo hoogstens naar een ander bestand van die soort in het pakket van de familie wijzen, nooit naar het pakket van een andere familie.
- **De eigen mappen van de familie.** Een CRAM wordt alleen met zijn eigen index gecombineerd. Een vastgelegde alignment waarvan het bestand weg is, ontbreekt: de backend zoekt ze niet elders, waar hij de reads van een andere analyse zou kunnen vinden. Alleen voor een sample waarvan geen import een alignment vastlegde, zoekt de backend in `data/<family>/` en `data/families/<family>/`: de reads op hun vaste plaatsen (`<sample>.cram` met `<sample>.cram.crai`). De signaalbestanden worden daar op het vastgelegde pad gezocht. Een pad dat uit zo'n map leidt, wordt niet opgezocht: een sample-ID als `../F2/S2`, of een gewijzigde databankrij met `..` of een absoluut pad naar elders. Een bestand dat via een symbolische koppeling in de map van een andere familie of buiten de datamap ligt, wordt niet vermeld en niet geserveerd.

IGV tekent de reads en varianten op het genoom van de assembly uit het project van de familie (`mapAssemblyToIgvGenome` in `frontend/src/lib/reference.ts`):

- `hg38` voor GRCh38 of hg38;
- `hg19` voor GRCh37 of hg19 (in hoofd- of kleine letters, met of zonder patch-achtervoegsel: de regel van `grc_build` in de backend);
- `hs1` voor T2T-CHM13v2.0 onder elk van zijn namen (`isT2tChm13`, hoofdstuk 4);
- enkele andere (`mm39`, `mm10`, `equCab3`).

Kent CoGA geen IGV-genoom voor de assembly, dan opent de pagina er geen en zegt ze dat ("No IGV genome for …"): op het genoom van een andere assembly zouden de reads op de verkeerde plaats staan.

**Waar in de code:** `frontend/src/components/IgvViewer.tsx` en `frontend/src/pages/families/FamilyIgvPage.tsx`, met het genoom in `frontend/src/lib/reference.ts`; `backend/app/routers/cram.py` en `signal_tracks.py`, met de eigen mappen van de familie in `backend/app/core/family_folders.py`.

## De stamboom

De stamboom wordt niet apart opgehaald, maar in de browser berekend uit de leden en relaties die met de familie meekomen. De tekening volgt de ouder-id's en de relaties, niet het platte rolveld (dat ook voor grootouders `mother`/`father` zegt).

- Mannen zijn vierkanten, vrouwen cirkels, onbekend geslacht een ruit; aangedane leden zijn gevuld, dragers half gevuld; bloedverwante partners krijgen een dubbele lijn.
- Een lid dat in een onbekende graad verwant is met een ander lid (een relatie `relative`, zoals een PGT-index van wie enkel de kant bekend is), staat naast dat lid in diens generatie, verbonden met een gestippelde boog met een vraagteken.
- Twee samples van één persoon (een relatie `same_individual`, zoals de plasma's van een monogene en een conventionele NIPT van één zwangerschap onder twee sample-ID's, of de plasma's van twee zwangerschappen van één vrouw) zijn één symbool, getekend onder het sample met de meeste verbanden in de stamboom (bij gelijkheid het eerste lid) en gelabeld met alle ID's (*PLASMA1 / PLASMA2*). Elk ander verband met een van die samples loopt naar dat symbool. Het symbool tekent wat een van de samples vastlegt (geslacht, aangedaan- en dragerstatus, ouders): een onbekende waarde van het ene sample verbergt nooit een bekende van het andere. Zijn QC-ring is de slechtste van de samples, het HPO-teken staat er als een van hen HPO-termen heeft, en de toegankelijke naam zegt hoeveel leden met meerdere samples getekend zijn. De ledentabel houdt een rij per sample.
- Een foetus (rol `fetus`) draagt een *P* in zijn symbool, zoals de stamboomnomenclatuur een zwangerschap tekent; zijn label is, zoals dat van een embryo, een maat kleiner.
- Het QC-oordeel per sample is een ring die niet alleen door kleur verschilt: een dunne ring met ✓ voor *pass*, een gestreepte met ! voor *warn* en een dikke met ✕ voor *fail*.

**Waar in de code:** `frontend/src/components/visualizations/Pedigree.tsx`.

## Fouten en grenzen

Een track, een pagina of IGV toont een mislukte vraag altijd als fout, nooit als lege data.

- Mislukt de vraag naar de beschikbare tracks of naar de chromosoomlengtes, dan meldt de werkruimte "Could not load … — this is not an empty result", met een knop om het opnieuw te proberen.
- Tijdens het schuiven blijft het vorige beeld alleen staan zolang het nieuwe venster laadt. Mislukt de vraag, dan toont de track de fout boven een leeg venster, nooit de data van het vorige venster.
- Een venster zonder breedte vraagt niets op en heet "no region in view".
- Heeft een venster meer varianten of SV's dan een track kan tekenen, dan zegt de track dat ("Too many … Zoom in or apply filters.") in plaats van een deel te tekenen; op het overzicht en in Circos gebeurt dat ook boven de grens van de backend.

**Chromosoomnamen.** In de tracknamen, de chromosoomlijsten en de kop heet het mitochondrion `chrM`, hoe de data het ook schrijft. De keuzelijst *Chromosome* van de weergave per chromosoom noemt elk chromosoom zoals de kop (`chr1`, `chrX`, `chrM`); de chromosoomstrook van het genoomoverzicht schrijft ze zonder `chr` (`1`, `X`). De ideogrammen volgen dezelfde regel; hun toegankelijke naam schrijft het chromosoom zonder `chr` (*Chromosome M ideogram*). Een band van het mitochondrion, dat geen cytobanden heeft, heet `chrM` in de tooltip en in de toegankelijke naam van het uitvergrote ideogram, net als een band zonder eigen naam. De repeattracks lezen het chromosoom van een locus zoals de API het leest (`MT`, `M`, `chrM` en `chrMT` zijn één chromosoom). De pagina *ROI marker review* schrijft het chromosoom van de ROI ook zo (`chr1`, `chrX`): in de uitleg bovenaan, het getoonde venster, de titel van elke site en de tooltip. Ze vraagt de markers en de blokken op met de naam zoals de familie hem opslaat.

**Een afgebroken sleepgebaar.** Soms kan een sleepgebaar niet eindigen met het loslaten van de muis: de track waarop het begon verdwijnt tijdens het slepen, de aanwijzer wordt geannuleerd (`pointercancel`), of het venster verliest de focus en daarmee het loslaten. Het gebaar breekt dan af zonder het venster te verplaatsen, en haalt zijn verschuiving of selectieband van de tracks.

## Veiligheid en traceerbaarheid

- **Alles achter een login.** De referentierouters (`chromosomes`, `cnvs`, `dgv`, `blacklist`, `segmental-duplications`) leggen de login op al hun endpoints; de andere routers vragen hem per endpoint.
- **Projectgebonden toegang.** Familie- en sampletracks lopen via het toegangscheckpoint (hoofdstuk 2). Wie een `family_id` kent maar er geen toegang toe heeft, krijgt de tracks niet.
- **Uploads alleen voor beheerders.** Een BED-track uploaden (`POST /api/bed/upload/{sample_id}/{bed_type}`) vraagt `get_current_admin_user`, en het bronbestand wordt geregistreerd.
- **Herkomst van de tracks.** Elke rij in de interval-tabel draagt haar bron en bestandsnaam. `sample_interval_track_sources` houdt per sample, soort, bron en bestand het aantal rijen en het uploadmoment bij, en ook de omrekening als de import de waarden van een track omrekent (`normalization`): bij HiFiCNV worden het kopieaantal en de diepte een log2-ratio. Geeft een dieptebestand geen bruikbare autosomale mediaan, dan blijft de diepte zoals gelezen en legt de import dat vast als `none`. Een dekkings-BED legt vast wat hij verklaarde te bevatten (`content`: `read_depth`, `log2_ratio`, of `unknown` zonder verklaring; hoofdstuk 6).

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `frontend/src/pages/genome/GenomeOverviewPage.tsx` · `GenomeOverviewWorkspace.tsx` | Genoomoverzicht |
| `frontend/src/pages/genome/ChromosomeViewPage.tsx` · `ChromosomeViewWorkspace.tsx` | Weergave per chromosoom |
| `frontend/src/pages/genome/CircosPlotPage.tsx` · `frontend/src/components/visualizations/CircosPlot.tsx` | Circos |
| `frontend/src/components/visualizations/` | Alle tracks, het ideogram, de ROI-band en de stamboom |
| `frontend/src/lib/smallVariantMarks.ts` · `trackSampling.ts` | Tekens van small variants; grenzen per track |
| `frontend/src/components/visualizations/svTypes.ts` | De rij en de kleur waarmee de SV-tracks en de Circos-plot een SV tekenen |
| `frontend/src/components/IgvViewer.tsx` | De ingebedde IGV-browser |
| `backend/app/routers/families_tracks.py` · `bed.py` | Familietracks en interval-tracks |
| `backend/app/routers/cram.py` · `signal_tracks.py` | Alignment- en signaalbestanden, met toegangscontrole |
| `backend/app/services/clickhouse_interval_tracks.py` | De interval-opslag en de APCAD-selectie |
