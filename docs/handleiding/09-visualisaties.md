# 9. Visualisaties (chromosome, genome, circos, IGV)

Dit hoofdstuk beschrijft hoe CoGA genomische data visueel toont: het **genoomoverzicht**, de **weergave per chromosoom**, de **Circos-plot** en de ingebedde **IGV-browser**, met de onderliggende *tracks*, het *ideogram* en de *stamboom*. Per weergave staat welk endpoint de data levert, hoe er getekend wordt en hoe de getoonde regio bepaald wordt. De uitleg voor gebruikers staat in de gebruikersgids in de app (`frontend/src/content/docs/user-guide/visualization.md`).

Enkele begrippen:

- **Track:** één horizontale strook die één soort gegeven over een genomisch bereik toont.
- **SVG** is een vectorformaat (scherpe vormen); **canvas** is een pixelvlak (sneller bij heel veel punten); **D3** is een JavaScript-bibliotheek die data op SVG of canvas tekent.
- **Downsampling:** uit een grote dataset een representatieve kleinere selectie kiezen, zodat het tekenen snel blijft.

## De twee hoofdpagina's

**Genoomoverzicht** (`GenomeOverviewPage.tsx` met `GenomeOverviewWorkspace.tsx`). Per geselecteerd familielid toont het een genoombrede track voor dekking, APCAD, structurele varianten, haplotypes en repeat-expansies, met onderaan een ideogram per chromosoom. De chromosomen liggen achter elkaar op één as. Een selectie door te slepen opent die regio in de weergave per chromosoom.

**Weergave per chromosoom** (`ChromosomeViewPage.tsx` met `ChromosomeViewWorkspace.tsx`). Bovenaan staat het volledige ideogram met het getoonde venster in het rood; daaronder per lid de sample-tracks (dekking, APCAD, SV's, small variants, haplotypes, repeats) en onderaan de referentietracks (genen, klinische CNV's, DGV, blacklist, segmentale duplicaties) en een uitvergroot ideogram van het venster. Er zijn knoppen om te zoomen en te schuiven, en een veld om naar een gen of locus te springen.

Welke tracks een sample heeft, vraagt de frontend eerst op (`GET /api/families/{family_id}/track-availability`), zodat er geen lege strook verschijnt voor data die de familie niet heeft.

**Waar in de code:** `frontend/src/pages/genome/`; de beschikbaarheid in `backend/app/routers/families_tracks.py`.

## Het ideogram

Het ideogram is de gestreepte chromosoomtekening. De cytobanden komen uit Postgres (`GET /api/chromosomes/{assembly}/{chrom}`) en worden in SVG getekend, met een kleur per kleuringscode (bv. `gpos100`, `acen` voor het centromeer). Het volledige ideogram laat de gebruiker een bereik selecteren; het uitvergrote ideogram toont alleen het getoonde venster. Op het genoomoverzicht, waar een chromosoom maar enkele pixels breed is, worden de banden per pixelgroep samengevoegd tot de overheersende kleuring.

**Waar in de code:** `frontend/src/components/visualizations/Ideogram.tsx` en `ZoomedIdeogram.tsx`; de hulpfuncties in `frontend/src/lib/ideogram.ts` en `stainColors.ts`.

## De sample-tracks

- **Dekking en segmenten.** De log-ratio van de dekking verschijnt als stippen per bin, met de CNV-segmenten als lijnen erover, op een canvas. De drempels voor de kleur (winst, verlies) zijn **per browser** instelbaar; de legende toont ze altijd en markeert afwijkende waarden, omdat een andere werkplek dezelfde data anders kan kleuren. Data: `GET /api/bed/{sample_id}/coverage/batch` en `…/segments/batch`, uit de interval-tabel in ClickHouse.
- **APCAD.** Een spreidingsdiagram van de B-allelfrequentie, met twee homozygote banden en een heterozygote middenband: het signaal waarmee ouderlijke haplotypes te onderscheiden zijn. Omdat er miljoenen markers per sample zijn, kiest de server zelf een selectie: alleen informatieve markers die de kwaliteitsfilter doorstonden, de beste eerst, verdeeld zodat alle banden zichtbaar blijven. Data: `GET /api/bed/{sample_id}/apcad/batch` en `…/apcad_pcf/batch`.
- **Small variants.** Eén teken per variant van het sample (`GET /api/families/{familyId}/small-variants` in track-modus, met een bovengrens). Elk teken draagt zijn klasse in vorm én kleur: een rode ruit voor ClinVar (waarschijnlijk) pathogeen, een oranje driehoek voor HIGH-impact, een holle blauwe vierkant voor ClinVar (waarschijnlijk) benigne, een groene stip voor MODERATE en een grijze stip voor de rest. Een reviewtag tekent een ring in de kleur van de tag rond het teken. Is het sample een kind met beschikbare ouders, dan splitst de track in rijen naar ouderlijke oorsprong (vaderlijk, onbepaald, moederlijk).
- **Structurele varianten.** Het overzicht gebruikt `SvTrack` (canvas, met een berekende hover), de weergave per chromosoom `VariantTrack` (SVG-vormen met eigen muisafhandeling). Beide halen `GET /api/families/{familyId}/structural-variants` op. DEL en DUP zijn balken, INV een omlijnde balk, INS een verticale streep en BND een driehoek.
- **Haplotypes.** Op het overzicht `GenomeHaplotypeTrack`, per chromosoom `HaplotypePhasedTrack`, met data uit `GET /api/families/{family_id}/haplotypes` en `…/phased-markers`. De ruwe gefaseerde markers worden per marker gekleurd, zonder groeperen; de haplotypetrack is de opgekuiste versie (`docs/haplotype-segregation-analysis.md`). De markeroverlay verschijnt alleen als beide ouders aanwezig zijn en de gebruiker ze aanzet. Het risicohaplotype wordt afgeleid in de regio van interesse; op het overzicht wordt het alleen op het chromosoom van die regio getekend, omdat de labels van de homologen per chromosoom gelden. Zonder regio van interesse tekent het overzicht geen risico en toont het de status "not assessed".
- **Repeat-expansies.** Per locus een teken in de kleur van de status (normaal, intermediair, pathogeen), uit `GET /api/families/{familyId}/repeat-expansions/sample/{sampleId}`.

**Waar in de code:** `frontend/src/components/visualizations/` (`CoverageSegmentsChart.tsx`, `ApcadChart.tsx`, `SmallVariantTrack.tsx`, `SvTrack.tsx`, `VariantTrack.tsx`, de haplotype- en repeattracks); de tekens van de small variants in `frontend/src/lib/smallVariantMarks.ts`; de APCAD-selectie in `fetch_apcad_downsampled` (`backend/app/services/clickhouse_interval_tracks.py`).

## De referentietracks

Deze tracks hangen niet aan een sample en tonen referentieannotatie over het getoonde venster:

| Track | Endpoint | Toont |
| --- | --- | --- |
| `GeneTrack` | `GET /api/genes/{assembly}/{chrom}` | Genen met exonen en intronen (D3); de tooltip noemt de panels waarin het gen zit |
| `CnvTrack` | `GET /api/cnvs/{assembly}/{chrom}` | Klinische CNV's; een klik opent de detailpagina |
| `DgvTrack` | `GET /api/dgv/{assembly}/{chrom}` | DGV-varianten; bij te veel overlap toont de server een dichtheidsprofiel in plaats van losse varianten |
| `BlacklistTrack` · `SegmentalDuplicationTrack` | `GET /api/blacklist/…` · `GET /api/segmental-duplications/…` | Onbetrouwbare zones en segmentale duplicaties |

**Waar in de code:** de componenten in `frontend/src/components/visualizations/`; de routers in `backend/app/routers/`.

## Groot en toch juist: downsampling

Hoeveel punten of segmenten een track hoogstens toont, hangt af van haar breedte in pixels (`frontend/src/lib/trackSampling.ts`). Dat tast de juistheid niet aan: er wordt alleen samengevat op overzichtsniveau, waar meerdere posities toch op dezelfde pixel vallen. Wie inzoomt, krijgt alle records van het kleinere venster. Bij APCAD is de selectie bovendien op kwaliteit en signaal gestuurd, niet willekeurig. De exacte beoordeling gebeurt nooit op deze tracks, maar op de gefilterde variantlijsten (hoofdstuk 8) en in IGV.

## Circos

De Circos-plot legt alle chromosomen in een cirkel en tekent structurele varianten als bogen ertussen, met D3 op SVG. De pagina haalt de chromosomen met hun banden op (altijd die van GRCh38) en alle SV's van de familie. Een klik op een chromosoom opent de weergave per chromosoom; een klik op een translocatie (BND) opent het genoomoverzicht met beide chromosomen.

**Waar in de code:** `frontend/src/pages/genome/CircosPlotPage.tsx` en `frontend/src/components/visualizations/CircosPlot.tsx`.

## IGV: reads op basisniveau

Voor de fijnste controle, de afzonderlijke reads, bedt CoGA de **IGV**-browser in. `IgvViewer.tsx` vraagt een lijst op van de alignmentbestanden van de gekozen samples (`GET /api/cram/{familyId}/manifest`) en, apart, de signaalbestanden van de CNV-caller (diepte, allelfractie en kopieaantal: `GET /api/signal-tracks/{familyId}/manifest`). Mislukt die tweede vraag, dan laden de reads toch en meldt de pagina dat de signaaltracks ontbreken.

De backend serveert de bestanden met dezelfde toegangscontrole als de rest: elk endpoint van `cram.py` en `signal_tracks.py` controleert dat het sample tot een familie hoort die de gebruiker mag zien, en geeft anders `404`. IGV vraagt met *range requests* alleen de bytes die het nodig heeft. Staan de bestanden in objectopslag, dan stuurt de backend IGV door naar een kortlevende, ondertekende URL.

**Waar in de code:** `frontend/src/components/IgvViewer.tsx` en `frontend/src/pages/families/FamilyIgvPage.tsx`; `backend/app/routers/cram.py` en `signal_tracks.py`.

## De stamboom

De stamboom wordt niet apart opgehaald maar in de browser berekend uit de leden en relaties die met de familie meekomen. De tekening volgt de ouder-id's en de relaties, niet het platte rolveld (dat ook voor grootouders `mother`/`father` zegt). Mannen zijn vierkanten, vrouwen cirkels, onbekend geslacht een ruit; aangedane leden zijn gevuld en dragers half gevuld; bloedverwante partners krijgen een dubbele lijn. Het QC-oordeel per sample verschijnt als een ring die niet alleen door kleur verschilt: een dunne ring met ✓ voor *pass*, een gestreepte met ! voor *warn* en een dikke met ✕ voor *fail*.

**Waar in de code:** `frontend/src/components/visualizations/Pedigree.tsx`.

## Fouten en grenzen

Een track, een pagina of IGV toont een mislukte vraag altijd als fout, nooit als lege data. Mislukt de vraag naar de beschikbare tracks of naar de chromosoomlengtes, dan meldt de werkruimte "Could not load … — this is not an empty result", met een knop om het opnieuw te proberen. Tijdens het schuiven blijft het vorige beeld alleen staan zolang het nieuwe venster laadt; mislukt de vraag, dan toont de track de fout boven een leeg venster, nooit de data van het vorige venster. Een venster zonder breedte vraagt niets op en heet "no region in view". Heeft een venster meer varianten of SV's dan een track kan tekenen, dan zegt de track dat ("Too many … Zoom in or apply filters.") in plaats van een deel te tekenen; op het overzicht en in Circos gebeurt dat ook boven de grens van de backend. In de viewer (tracknamen, chromosoomlijsten, kop) heet het mitochondrion altijd `chrM`, hoe de data het ook schrijft.

## Veiligheid en traceerbaarheid

- **Alles achter een login.** De referentierouters (`chromosomes`, `cnvs`, `dgv`, `blacklist`, `segmental-duplications`) leggen de login op al hun endpoints; de andere routers vragen hem per endpoint.
- **Projectgebonden toegang.** Familie- en sampletracks lopen via het toegangscheckpoint (hoofdstuk 2). Wie een `family_id` kent maar er geen toegang toe heeft, krijgt de tracks niet.
- **Uploads alleen voor beheerders.** Een BED-track uploaden (`POST /api/bed/upload/…`) vraagt `get_current_admin_user`, en het bronbestand wordt geregistreerd.
- **Herkomst van de tracks.** Elke rij in de interval-tabel draagt haar bron en bestandsnaam; `sample_interval_track_sources` houdt per sample, soort, bron en bestand het aantal rijen en het uploadmoment bij.

## Belangrijkste bestanden

| Bestand | Rol |
| --- | --- |
| `frontend/src/pages/genome/GenomeOverviewPage.tsx` · `GenomeOverviewWorkspace.tsx` | Genoomoverzicht |
| `frontend/src/pages/genome/ChromosomeViewPage.tsx` · `ChromosomeViewWorkspace.tsx` | Weergave per chromosoom |
| `frontend/src/pages/genome/CircosPlotPage.tsx` · `frontend/src/components/visualizations/CircosPlot.tsx` | Circos |
| `frontend/src/components/visualizations/` | Alle tracks, het ideogram en de stamboom |
| `frontend/src/lib/smallVariantMarks.ts` · `trackSampling.ts` | Tekens van small variants; grenzen per track |
| `frontend/src/components/IgvViewer.tsx` | De ingebedde IGV-browser |
| `backend/app/routers/families_tracks.py` · `bed.py` | Familietracks en interval-tracks |
| `backend/app/routers/cram.py` · `signal_tracks.py` | Alignment- en signaalbestanden, met toegangscontrole |
| `backend/app/services/clickhouse_interval_tracks.py` | De interval-opslag en de APCAD-selectie |
