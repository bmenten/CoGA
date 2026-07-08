# 9. Visualisaties (chromosome, genome, circos, IGV)

In dit hoofdstuk wordt beschreven hoe CoGA genomische data visueel toont. We volgen de vier grote weergaven — het **whole-genome-overzicht**, de **per-chromosoom-weergave**, de **Circos-plot** en de ingebedde **IGV-browser** — plus de onderliggende *tracks* (horizontale gegevensbalken: coverage, APCAD, varianten, CNV's, genen, DGV, blacklist, segdups, repeats, haplotypes), het *ideogram* (de gestreepte chromosoomtekening) en de *pedigree* (stamboom). Per visualisatie maken we telkens drie dingen expliciet: **welk API-endpoint** de data levert, **hoe** er getekend en gesampled wordt (canvas, SVG of D3), en **hoe de juiste regio** wordt getoond. We sluiten af met de toegangscontrole die ook op deze visualisatie-endpoints geldt.

Een paar begrippen vooraf, kort uitgelegd:

- **Endpoint** = een URL die de backend aanbiedt en die data teruggeeft (bv. `/chromosomes/GRCh38/1`).
- **Track** = één horizontale strook in de viewer die één soort gegeven toont over een genomisch bereik.
- **SVG** = vectortekenformaat in de browser (scherpe lijnen/vormen); **canvas** = een pixel-tekenvlak (sneller bij héél veel punten); **D3** = een JavaScript-bibliotheek om data op SVG/canvas te projecteren.
- **Downsampling** = uit een enorme dataset een representatieve, kleinere selectie kiezen zodat de tekening snel blijft.
- **Region** = het getoonde venster op een chromosoom: `chrom`, `regionStart`, `regionEnd`.

## De twee dragende pagina's: genome-overzicht en per-chromosoom

De viewer bestaat uit twee samengestelde pagina's die de losse track-componenten stapelen. Beide splitsen de logica in een *page* (haalt data + rekent lay-out uit) en een *workspace* (rendert de tracks).

**Whole-genome-overzicht.** De pagina `frontend/src/pages/genome/GenomeOverviewPage.tsx` bouwt eerst voor elk familielid de *URL-kaarten* (`urlMaps`) waar de tracks hun data vandaan halen, en geeft die door aan `GenomeOverviewWorkspace` (`frontend/src/pages/genome/GenomeOverviewWorkspace.tsx`), die alle geselecteerde leden onder elkaar rendert. De workspace toont per lid een genoombrede **Coverage**-, **APCAD**-, **SV**-, **Haplotype**- en **Repeat-expansion**-track, en onderaan een rij **ideogrammen** (één per chromosoom). Omdat het hier om het hele genoom gaat, worden de chromosomen achter elkaar gelegd via een `layout`-object (`offsets`, `lengths`, `total`, `chroms`): elk chromosoom krijgt een genoombrede X-offset, zodat een positie op chromosoom 5 verderop ligt dan op chromosoom 1.

- **Waar in de code:** URL-opbouw in `GenomeOverviewPage.tsx` (de `buildBatchBedUrl`-helper binnen de `urlMaps`-memo, en de `layout`-`useEffect` die de offsets berekent); stapeling en interactie in `GenomeOverviewWorkspace.tsx` (component `GenomeOverviewWorkspace`, met de helper `resolveGenomeRegionSelection` die een sleep-selectie op een track omzet naar een `{chrom, start, end}` en naar de chromosoomweergave springt).

**Per-chromosoom-weergave.** De pagina `ChromosomeViewPage.tsx` en de workspace `ChromosomeViewWorkspace.tsx` (beide in `frontend/src/pages/genome/`) tonen één chromosoom in detail. Bovenaan staat een volledig `Ideogram` met een rode markering van het getoonde venster; daaronder per lid de sample-tracks (Coverage, APCAD, `VariantTrack` = SV's, `SmallVariantTrack`, `HaplotypePhasedTrack`, `RepeatExpansionTrack`) en onderaan de referentie-tracks die niet sample-specifiek zijn (`GeneTrack`, `CnvTrack`, `DgvTrack`, `BlacklistTrack`, `SegmentalDuplicationTrack`) plus een `ZoomedIdeogram` die het geselecteerde bereik uitvergroot toont. Er is ook een navigatiebalk met zoom/pan-knoppen en een "Jump to gene or locus"-veld (via de endpoints `/genes/search` en `/genes/profile`).

- **Waar in de code:** de `ViewerTrackBlock`-secties in `ChromosomeViewWorkspace.tsx` (Ideogram, Coverage, APCAD, VariantTrack, SmallVariantTrack, Haplotypes, Repeat expansions, GeneTrack, CnvTrack, DgvTrack, Blacklist, SegDup/LCR, ZoomedIdeogram).

### Welke tracks tonen we eigenlijk? — de availability-poort

Voordat een track wordt gerenderd, vraagt de frontend aan de backend welke datasoorten voor deze familie/dit bereik überhaupt bestaan. Dat gebeurt via het endpoint `GET /families/{family_id}/track-availability` (functie `get_family_track_availability` in `backend/app/routers/families_tracks.py`). Zo verschijnt er geen lege APCAD-strook als de familie geen APCAD-data heeft. Het antwoord bevat een `samples`-map; de frontend zet die om in `availability[sample_id]` en gebruikt dat in de `&&`-condities die elke track omhullen in beide workspaces.

## Het ideogram: cytobanden tekenen (Ideogram / ZoomedIdeogram)

Het **ideogram** is de klassieke gestreepte chromosoomtekening. De banden (*cytobands*) en hun kleuring komen uit Postgres.

**Data.** Beide componenten halen hun chromosoom op via `GET /chromosomes/{assembly}/{chrom}`, dat `{chr, size, bands[]}` teruggeeft. Elke band heeft `name`, `start`, `end` en een `stain`-code (bv. `gpos100`, `gneg`, `acen`). De query wordt oneindig gecachet (`staleTime: Infinity`, `gcTime: Infinity`) want referentiedata verandert niet.

- **Waar in de code:** `frontend/src/components/visualizations/Ideogram.tsx` en `ZoomedIdeogram.tsx` (de `useQuery` met key `["chromosome", assembly, chrom]`); backend-endpoint `get_chromosome` in `backend/app/routers/chromosomes.py`, dat delegeert naar `get_chromosome_data` in `backend/app/services/reference_metadata_service.py`.

**Tekenen (SVG).** Beide tekenen in **SVG**. Elke band wordt een `<rect>` waarvan de X-positie evenredig is met `start/size`; de kleur komt uit `getStainColor(stain)` in `frontend/src/lib/stainColors.ts`, dat de stain-code naar een CSS-kleurvariabele vertaalt (bv. `gpos100 → --color-stain-gpos100`). Voor diepte krijgt elke band een subtiel kleurverloop via `getBandGradientStops` in `frontend/src/lib/ideogram.ts`. De **acen**-banden (het centromeer) worden als driehoekige `<polygon>`'s getekend; `getAcenDirection` (in `ideogram.ts`) bepaalt of het de p- of q-arm is. Een lokale helper `buildChromosomeOutlinePath` in `Ideogram.tsx` tekent daaromheen de karakteristieke "geknepen" chromosoomomtrek.

- `Ideogram` toont het héle chromosoom en markeert het getoonde venster met een rood, half-transparant kader (`showHighlight`). De gebruiker kan met de muis een bereik selecteren (`onRegionSelect`), wat de detailweergave doet inzoomen.
- `ZoomedIdeogram` doet het omgekeerde: het toont **alleen** het venster `regionStart..regionEnd`, uitvergroot, met een fijnere as (`niceTickInterval`) en rode randlijnen links/rechts.
- **Waar in de code:** de `renderBands.map(...)`-lus en `handleMouseUp` (regio-selectie) in `Ideogram.tsx`; de venster-gebonden `bands.filter(...)` in `ZoomedIdeogram.tsx`.

**Bandresolutie.** Op het genome-overzicht is een chromosoom maar enkele pixels breed. Om te voorkomen dat honderden banden op elkaar gepropt worden, wordt `collapseBandsForResolution` (in `ideogram.ts`) aangeroepen met `bandResolution="compact"`: het genoom wordt in "buckets" (emmertjes) van minimaal 4 px verdeeld en per bucket wint de band met de grootste overlap ("dominante stain"). Zo blijft het beeld getrouw maar goedkoop te tekenen.

## De sample-tracks

Deze tracks tonen data die per **sample** (individu) verschilt. Ze delen enkele patronen: coördinaten worden lineair naar pixels geschaald, en veel tracks houden bij een *pan* (verschuiven met gelijke breedte) tijdelijk het vorige beeld vast via de hook `useSameSpanFallbackData`, zodat het beeld glijdt in plaats van te knipperen.

### Coverage & segments (CoverageSegmentsChart)

De coverage-track toont de dieptedekking (log-ratio) als een **scatter van stippen** (per bin één stip) met daaroverheen horizontale **segment**-lijnen (de CNV-calling-segmenten).

**Data.** De component krijgt kant-en-klare URL's binnen (`coverageUrls`, `segmentsUrls`) die de pagina heeft opgebouwd naar het **BED-batch-endpoint**: `GET /bed/{sample_id}/coverage/batch` en `.../segments/batch` (met `format=json`). Zie `backend/app/routers/bed.py`, functie `fetch_bed_batch`. Die roept de bed-service aan, die de intervallen uit de ClickHouse interval-store haalt: `backend/app/services/clickhouse_interval_tracks.py` bewaart intervallen (coverage/apcad/segments/haplotype) in de tabel `.../INTERVAL/entries` en levert ze terug via `fetch_interval_track_rows`.

**Tekenen (canvas).** Omdat een genoombrede coverage-track duizenden punten bevat, tekent `CoverageSegmentsChart` op een **`<canvas>`** (sneller dan SVG). Kleuren worden per waarde bepaald (winst/verlies/neutraal, drempels uit de gebruikersinstellingen). Bij één chromosoom (`stableChroms.length === 1` en een gezet `regionStart/regionEnd`) schakelt de chart over op de venster-modus (`isFocusedRegion`) en schaalt op `regionStart..regionEnd`; anders gebruikt hij de genoombrede `layout`.

- **Waar in de code:** `frontend/src/components/visualizations/CoverageSegmentsChart.tsx` (de grote `useEffect` met de canvas-2D-tekencode; `TRACK_DOT_RADIUS` uit `trackSampling.ts` bepaalt de stipgrootte).

### APCAD (ApcadChart)

APCAD is een **BAF-scatter** (B-allele-frequentie): twee homozygote banden (rond 0 en 1) en een heterozygote middenband — het signaal waarmee ouderlijke haplotypes worden onderscheiden. Er komen ook PCF-segmenten (gladgestreken segmenten) overheen.

**Data.** URL's wijzen naar `GET /bed/{sample_id}/apcad/batch` en `.../apcad_pcf/batch`. Hier is de **downsampling cruciaal**: APCAD staat op SNV-resolutie (miljoenen markers per sample). De functie `fetch_apcad_downsampled` in `clickhouse_interval_tracks.py` doet de selectie volledig **server-side** in ClickHouse: ze houdt alleen informatieve markers (`origin IN ('paternal','maternal')`), filtert op kwaliteit (VCF `filter = PASS`, of markers zonder geregistreerde filter uit oudere uploads), en verdeelt een puntenbudget band-bewust — een deel gereserveerd voor de homozygote banden zodat ze zichtbaar blijven, de rest voor de heterozygote markers (het fasesignaal). Binnen elke band worden de markers met de hóógste `qual` gekozen (geen ruwe ruimtelijke steekproef). Zo blijft de payload begrensd zonder het overzichtsbeeld te vertekenen.

**Tekenen (canvas).** `ApcadChart` tekent de ruwe BAF-stippen vaag (`globalAlpha 0.45`) en de PCF-segmenten daaroverheen, gekleurd per ouderlijke oorsprong (`--color-apcad-paternal/maternal`). De Y-as is licht ingeklemd (`yPad`) zodat de banden op 0 en 1 niet tegen de rand geklemd raken.

- **Waar in de code:** `frontend/src/components/visualizations/ApcadChart.tsx`; server-side selectie in `fetch_apcad_downsampled` (`clickhouse_interval_tracks.py`), aangeroepen via de bed-service. Het puntenbudget komt uit `getApcadPointLimit(width)` in `frontend/src/lib/trackSampling.ts`.

### Small Variants (SmallVariantTrack)

Deze track plaatst één gekleurde stip per Small Variant (SNV/indel) van het getoonde sample.

**Data.** `GET /families/{familyId}/small-variants` met `track_mode=true` en `track_result_limit=10000`. Boven die grens toont de track "Too many variants to display. Zoom in or apply filters." De kleur wordt bepaald in deze volgorde: tag-kleur (indien getagd) → anders een ClinVar-override (benign/pathogeen) → anders een functionele-impact-kleur. Wanneer het getoonde sample een kind is met beschikbare ouders, splitst de track in drie rijen op **ouderlijke oorsprong** (paternaal boven / onbepaald midden / maternaal onder); de functie `variantOrigin` leidt die af uit Mendeliaanse overerving of, bij één ontbrekende ouder, uit de gefaseerde haplotypevolgorde.

**Tekenen (D3 op SVG).** Eén data-join tekent alle stippen; een enkele transparante *hit-laag* met een D3-**quadtree** (een boomstructuur om snel het dichtstbijzijnde punt te vinden) vangt hover-events op, in plaats van duizenden losse hitboxen.

- **Waar in de code:** `frontend/src/components/visualizations/SmallVariantTrack.tsx` (`variantOrigin`, `getVariantColor`, de `d3.quadtree`-hitlaag); grens `SMALL_VARIANT_TRACK_RESULT_LIMIT` en `TRACK_DOT_RADIUS` in `trackSampling.ts`.

### Structurele varianten (SvTrack en VariantTrack)

Het genome-overzicht gebruikt `SvTrack`, de per-chromosoom-weergave gebruikt `VariantTrack` (label "SVs"). Beide halen de structurele varianten op via `GET /families/{familyId}/structural-variants`.

**SvTrack (genome-overzicht).** Krijgt één URL binnen en haalt de SV's op met een ruwe `fetch` (met bearer-token uit `storage`). Vijf typen worden in vaste rijen getekend: `DEL, DUP, INV, INS, BND`.

**Tekenen (canvas).** DEL/DUP als balken, INV als een omkaderde (witte) balk, INS als verticale streep, BND als driehoek. Positionering gebeurt via het genoombrede `layout` (`offset + start`). Hover-detectie is puur wiskundig (rechthoek-hittest), geen extra DOM.

- **Waar in de code:** `frontend/src/components/visualizations/SvTrack.tsx` en `VariantTrack.tsx`.

### Haplotype- en repeat-tracks

- **Haplotypes.** Op het overzicht `GenomeHaplotypeTrack`, op één chromosoom `HaplotypePhasedTrack`. Data uit `GET /families/{family_id}/haplotypes(/batch)` en `GET /families/{family_id}/phased-markers`. De gefaseerde marker-overlay wordt alleen getoond als beide ouders aanwezig zijn en de gebruiker de overlay aanzet. Zoals in de projectgeheugens vastgelegd: **gefaseerde imputed markers worden ruw per marker gekleurd** (niet gebinned); de haplotype-track zelf is de opgekuiste versie.
- **Repeat expansions.** Op het overzicht `GenomeRepeatExpansionTrack`, op één chromosoom `RepeatExpansionTrack`; beide halen `GET /families/{familyId}/repeat-expansions/sample/{sampleId}` op. In *overview-mode* (als `chromosomeSize` is meegegeven) vraagt de track chromosoombreed en cachet stabiel; elke locus krijgt een kleur naar status (normaal/intermediair/pathogeen).
- **Waar in de code:** endpoints `get_family_haplotypes`, `get_family_haplotypes_batch`, `get_family_phased_markers`, `get_sample_repeat_expansions` in `backend/app/routers/families_tracks.py`; componenten in `frontend/src/components/visualizations/` (`GenomeHaplotypeTrack.tsx`, `HaplotypePhasedTrack.tsx`, `GenomeRepeatExpansionTrack.tsx`, `RepeatExpansionTrack.tsx`).

## De referentie-tracks

Deze tracks zijn niet sample-gebonden maar tonen referentie-annotatie (genen en bekende regio's). Ze delen een simpel patroon: SVG-`<rect>`'s over het venster `regionStart..regionEnd`, met een `useQuery` per (`assembly, chrom, regionStart, regionEnd`).

| Track | Endpoint | Toont |
|---|---|---|
| `GeneTrack` | `GET /genes/{assembly}/{chrom}` (+ `GET /panels`) | Genen met exon/intron-structuur; getekend met **D3** op SVG (exon-rects, intron-lijnen, strand-pijl); tooltip vermeldt de genpanels waarin het gen zit |
| `CnvTrack` | `GET /cnvs/{assembly}/{chrom}` | Klinische CNV's als oranje balken; klik navigeert naar de CNV-detailweergave (`/cnv-details/{id}`) |
| `DgvTrack` | `GET /dgv/{assembly}/{chrom}` | DGV-varianten; server kiest `lines`-modus (individuele varianten in banen) of `density`-modus (per-bin-profiel) bij te veel overlap; gains boven, losses onder een middenlijn |
| `BlacklistTrack` | `GET /blacklist/{assembly}/{chrom}` | Blacklist-regio's (onbetrouwbare zones) als balkjes |
| `SegmentalDuplicationTrack` | `GET /segmental-duplications/{assembly}/{chrom}` | Segmentale duplicaties / LCR's als balkjes |

- **Waar in de code:** componenten in `frontend/src/components/visualizations/` (`GeneTrack.tsx`, `CnvTrack.tsx`, `DgvTrack.tsx`, `BlacklistTrack.tsx`, `SegmentalDuplicationTrack.tsx`); backend-routers `genes.py`, `cnvs.py`, `dgv.py`, `blacklist.py`, `segmental_duplications.py`, telkens delegerend naar `reference_metadata_service`. `GeneTrack` is de enige die D3 gebruikt om exons/introns te tekenen; de rest is platte SVG met `<rect>`'s. Het genpanel-overzicht komt van het aparte endpoint `GET /panels`.

## TrackSampling: groot en toch snel — zonder overzicht te verliezen

Het bestand `frontend/src/lib/trackSampling.ts` bevat de constanten en formules die bepalen **hoeveel** punten/segmenten een track maximaal toont, geschaald op de trackbreedte:

- `SMALL_VARIANT_TRACK_RESULT_LIMIT = 10000` — boven dit aantal toont de Small-Variant-track "te veel; zoom in".
- `getTrackVariantLimit`, `getTrackBinLimit`, `getTrackSegmentLimit`, `getApcadPointLimit` — leveren een limiet die ≈ evenredig is met de breedte (met een onder- en bovengrens).
- `getAdaptiveTrackWindow` — kiest een binbreedte zodat er ongeveer één bin per twee pixels is.

Waarom tast dit de **juistheid** niet aan? Omdat het downsamplen alleen op **overzichtsniveau** gebeurt, waar meerdere basenparen tóch op dezelfde pixel vallen: twee stippen op dezelfde pixel voegen geen zichtbare informatie toe. Zodra de gebruiker inzoomt (kleiner venster), stijgt de effectieve resolutie en worden alle records in dat venster weer opgehaald. Bij APCAD is de selectie bovendien **kwaliteits- en signaalgestuurd** (hoogste `qual`, heterozygoot-behoudend) in plaats van willekeurig, zodat het diagnostisch relevante signaal — de autozygositeitsbreuken — behouden blijft. De kritieke, exacte beoordeling gebeurt nooit op deze overzichtstracks maar op de gefilterde variantlijsten (zie hoofdstuk [08-filterpaginas-en-api.md](08-filterpaginas-en-api.md)) en in IGV.

## Circos: genoombrede structurele varianten in een ring (CircosPlot)

De Circos-plot legt alle chromosomen in een cirkel en tekent structurele varianten als bogen ertussen.

**Data.** De pagina `frontend/src/pages/genome/CircosPlotPage.tsx` haalt twee dingen op:
1. de chromosoom-scaffolds via `GET /chromosomes/GRCh38/details` (alle chromosomen mét banden), en
2. de structurele varianten via `GET /families/{familyId}/structural-variants` met `page_size=0` (= alle SV's, geen paginering).

**Tekenen (D3 op SVG).** `frontend/src/components/visualizations/CircosPlot.tsx` gebruikt **D3** intensief: het rekent per chromosoom een hoeksegment uit (evenredig met chromosoomlengte, met tussenruimte), tekent de cytobanden als ring-sectoren (met dezelfde `getStainColor`/`getBandGradientStops`-helpers als het ideogram), en tekent per variant een radiale verbinding: DEL/DUP als dikke bogen (`d3.linkRadial`), INV/BND als gebogen lijnen (kwadratische curves) tussen bron- en doelpositie, INS als klein radiaal streepje. Kleur per SV-type komt uit CSS-variabelen (`--color-variant-del/dup/ins/inv/bnd`). Belangrijk voor performance: de tekencode gebruikt **keyed joins** (per chromosoom/variant), zodat bij het aan/uitzetten van chromosomen alleen de gewijzigde knopen muteren in plaats van de hele SVG opnieuw op te bouwen.

**Regio/interactie.** Klikken op een chromosoom navigeert naar de per-chromosoom-weergave; klikken op een **BND** (translocatie) opent het genome-overzicht met beide betrokken chromosomen geselecteerd.

- **Waar in de code:** component `CircosPlot` en de (lokale) helpers `buildChromosomeOutlinePath`, `buildBandSectorPath`, `buildAcenBandPath` in `CircosPlot.tsx`; datalaadlogica en de klik-navigatie (`handleChromClick`, `handleVariantClick`) in `CircosPlotPage.tsx`.

## IGV: reads inspecteren op basisniveau (IgvViewer + igvLoader + cram.py)

Voor de fijnste inspectie — de individuele *reads* (sequencing-fragmenten) — bedt CoGA de officiële **IGV**-browser in.

**Laden.** `frontend/src/lib/igvLoader.ts` laadt het IGV-bundelscript één keer lui (lazy) in en cachet de belofte (`igvLoadPromise`), zodat het niet bij elke navigatie opnieuw laadt.

**Opzet.** `frontend/src/components/IgvViewer.tsx` vraagt eerst een **manifest** op via `GET /cram/{familyId}/manifest?sample=...`, dat per sample een `{sample_id, format, url, index_url}` teruggeeft, en maakt daar IGV-alignment-tracks van (via `igv.createBrowser`). De pagina `frontend/src/pages/families/FamilyIgvPage.tsx` levert de juiste `sampleIds` (proband eerst, via `sortFamilyMembersProbandFirst`), het genoom (`mapAssemblyToIgvGenome`) en een optionele `locus`.

**Hoe reads veilig geserveerd worden.** De router `backend/app/routers/cram.py` serveert de alignment-bestanden:
- Lokaal draait dit via `FileResponse` (o.a. `GET /cram/{family_id}/{sample_id}.cram` plus de bijhorende `.crai`-index; ook een `.bam`/`.bai`-variant). IGV vraagt met **HTTP range-requests** enkel de bytes op die het voor het zichtbare venster nodig heeft — het hele CRAM-bestand wordt nooit in één keer verstuurd. Er zijn ook `HEAD`-varianten voor bestaanschecks.
- In *remote*-modus (objectopslag/S3) geeft `_serve_alignment` een **302-redirect** naar een kortlevende *presigned URL*; IGV leest de bytes dan (met range-requests) rechtstreeks uit de opslag. Voor header-inspectie opent `_read_alignment_header` het bestand met `pysam`.

- **Waar in de code:** `IgvViewer.tsx` (manifest ophalen + `createBrowser`), `igvLoader.ts` (`loadIgv`), `FamilyIgvPage.tsx`; serveerlogica in `backend/app/routers/cram.py` (`get_cram`, `get_crai`, `get_alignment_manifest`, `_serve_alignment`).

## Pedigree: de stamboom uit de familieleden (Pedigree.tsx)

De stamboom wordt niet apart opgehaald maar **client-side berekend** uit de familiegegevens (leden en relaties) die al met het familierecord geladen zijn — in Postgres o.a. de tabel `family_members`. Talrijke pagina's tonen de stamboom (o.a. `FamilyDetailPage`, `FamilySmallVariantsPage`, `FamilyStructuralVariantsPage`); ze geven `rows`, `members` en `relationships` als props door.

**Opbouw.** `frontend/src/components/visualizations/Pedigree.tsx` krijgt `rows` (PED-achtige rijen: individu `iid`, vader-id `pid`, moeder-id `mid`, geslacht `sex`, fenotype `phen`), `members` en `relationships`. De functie `normalizePedigree` leidt hieruit ouder-kindrelaties, "family units" (ouderparen met kinderen) en partneredges af; `assignGenerations` bepaalt generatieniveaus (met bescherming tegen cykels via een `visiting`-set en een `isAncestor`-check); `layoutPedigree` plaatst iedereen op X/Y-coördinaten (blokken per generatie, ouders gecentreerd boven hun kinderen).

**Tekenen (D3 op SVG).** Mannen worden vierkanten, vrouwen cirkels, onbekend geslacht ruiten; aangedane individuen zijn gevuld, dragers half-gevuld (met conventies per overervingsmodel, bv. XLR-draagster als centrale stip). Verbindingslijnen tekenen partner- en ouder-kindrelaties; consanguïene paren krijgen een dubbele lijn. Voor traceerbaarheid krijgt elk symbool bovendien een **QC-verdict**: het per-sample kwaliteitsoordeel kleurt de omtrek (en eventueel de vulling) van de knoop groen (pass) / amber (warn) / rood (fail), met een dikkere rand bij "fail"; het optionele label wordt een tooltip.

> Let op de nuance uit het projectgeheugen: het vlakke `role`-veld overlaadt 'mother'/'father' ook voor grootouders; de pedigree steunt daarom op de expliciete ouder-id's (`pid`/`mid`) en relaties, niet op één rol-veld.

- **Waar in de code:** `frontend/src/components/visualizations/Pedigree.tsx` (`normalizePedigree`, `assignGenerations`, `layoutPedigree`, de constante `QC_RING_COLORS`, en de D3-tekenlus in de `useEffect`).

## Veiligheid & traceerbaarheid

Visualisaties tonen patiëntgevoelige signalen, dus dezelfde afscherming als de rest van het platform geldt hier onverkort (zie ook [02-beveiliging-rollen-rechten.md](02-beveiliging-rollen-rechten.md)).

- **Alles achter authenticatie.** De referentie-routers `chromosomes.py`, `cnvs.py`, `dgv.py`, `blacklist.py` en `segmental_duplications.py` hangen een router-brede `dependencies=[Depends(get_current_user)]` op — de code-commentaar zegt expliciet dat dit *elk* endpoint gate't, ook toekomstige. `genes.py` en de familie-endpoints (`families_tracks.py`, `small-variants`, `structural-variants`) vragen de `CurrentUser` per request op met `Depends(get_current_user)`.
- **Project-scoping op familiedata.** Sample- en familie-tracks lopen via `build_family_metadata_context` / `build_sample_metadata_context` (en `get_family_record` in `cram.py`), die controleren dat de gebruiker toegang heeft tot die familie/dat project. Een gebruiker kan dus geen tracks van een familie buiten zijn scope ophalen, zelfs niet als hij het `family_id` kent.
- **CRAM-toegang expliciet gecontroleerd.** In `backend/app/routers/cram.py` roept elk read/head-endpoint `_ensure_accessible_alignment_sample` aan: dat haalt via `get_family_record` de toegestane samples op en werpt **404** als het gevraagde `sample_id` niet tot de (voor de gebruiker toegankelijke) familie behoort. In remote-modus zijn de presigned URL's bovendien kortlevend.
- **Uploads zijn admin-only.** Het vullen van BED-tracks (`POST /bed/upload/{sample_id}/{bed_type}` in `bed.py`) vereist `get_current_admin_user`, en registreert het bronbestand via `record_upload_file_obj` — provenance die traceerbaar maakt welk bestand welke track voedde.
- **Provenance in de interval-store.** De ClickHouse-intervaltabel bewaart per rij `source`, `filename` en `metadata_json`; de bijbehorende Postgres-tabel `sample_interval_track_sources` houdt per (sample, track_type, source, filename) het rijaantal en uploadmoment bij (`upsert_interval_track_source`). Zo is voor elke coverage/APCAD-track herleidbaar uit welke upload hij komt.

## Belangrijkste bestanden

| Bestand | Rol |
|---|---|
| `frontend/src/pages/genome/GenomeOverviewPage.tsx` / `GenomeOverviewWorkspace.tsx` | Whole-genome-overzicht: bouwt track-URL's en stapelt de tracks per lid |
| `frontend/src/pages/genome/ChromosomeViewPage.tsx` / `ChromosomeViewWorkspace.tsx` | Per-chromosoom-weergave met detail-tracks en ZoomedIdeogram |
| `frontend/src/pages/genome/CircosPlotPage.tsx` | Laadt scaffolds + SV's voor de Circos-plot |
| `frontend/src/components/visualizations/Ideogram.tsx` / `ZoomedIdeogram.tsx` | Cytoband-tekening (heel chromosoom / uitvergroot venster) |
| `frontend/src/components/visualizations/CircosPlot.tsx` | D3-Circos: chromosoomring + radiale SV-verbindingen |
| `frontend/src/components/visualizations/CoverageSegmentsChart.tsx` / `ApcadChart.tsx` | Canvas-scatter voor coverage en BAF/APCAD |
| `frontend/src/components/visualizations/SvTrack.tsx` / `VariantTrack.tsx` / `SmallVariantTrack.tsx` | Structurele varianten (canvas) en Small Variants (D3 + quadtree) |
| `frontend/src/components/visualizations/GeneTrack.tsx`, `CnvTrack.tsx`, `DgvTrack.tsx`, `BlacklistTrack.tsx`, `SegmentalDuplicationTrack.tsx`, `RepeatExpansionTrack.tsx`, `GenomeRepeatExpansionTrack.tsx` | Referentie- en repeat-tracks |
| `frontend/src/components/visualizations/Pedigree.tsx` | Stamboomlayout en -tekening uit leden/relaties |
| `frontend/src/components/IgvViewer.tsx` / `frontend/src/lib/igvLoader.ts` / `frontend/src/pages/families/FamilyIgvPage.tsx` | Ingebedde IGV-browser (lui geladen, manifest-gestuurd) |
| `frontend/src/lib/ideogram.ts`, `stainColors.ts`, `chromosomes.ts`, `trackSampling.ts`, `colors.ts` | Teken-hulpfuncties: banden/gradients (`getBandGradientStops`, `getAcenDirection`, `collapseBandsForResolution`, `niceTickInterval`), stain-kleuren, chromosoomsortering, downsample-limieten, CSS-kleurvariabelen |
| `backend/app/routers/chromosomes.py` | Chromosoomgroottes en cytobanden (ideogram/circos) |
| `backend/app/routers/families_tracks.py` | Familie-tracks: haplotypes, phased-markers, repeats, track-availability, SV-lengtes |
| `backend/app/routers/bed.py` | BED-tracks (coverage/apcad/segments) ophalen en uploaden |
| `backend/app/routers/genes.py`, `cnvs.py`, `dgv.py`, `blacklist.py`, `segmental_duplications.py` | Referentie-annotatie-endpoints |
| `backend/app/routers/cram.py` | CRAM/BAM veilig serveren (range-requests, toegangscontrole, presigned URLs) |
| `backend/app/services/clickhouse_interval_tracks.py` | Interval-store + server-side APCAD-downsampling (`fetch_apcad_downsampled`) |
