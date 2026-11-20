# 9. Visualisations (chromosome, genome, circos, IGV)

CoGA shows genomic data in four views: the **genome overview**, the **chromosome view**, the **Circos plot** and the embedded **IGV browser**. They rely on *tracks*, the *ideogram* and the *pedigree*. For each view this chapter gives the endpoint that supplies the data, how it is drawn and how the region shown is determined. The explanation for users is in the user guide in the app (`frontend/src/content/docs/user-guide/visualization.md`).

A few concepts:

- **Track:** one horizontal strip that shows one kind of data over a genomic range.
- **SVG** is a vector format (sharp shapes); **canvas** is a pixel surface (faster with very many points); **D3** is a JavaScript library that draws data on SVG or canvas.
- **Downsampling:** choosing a representative smaller selection from a large dataset, so that drawing stays fast.

## The two main pages

**Genome overview** (`GenomeOverviewPage.tsx` with `GenomeOverviewWorkspace.tsx`). For each selected family member it shows genome-wide tracks for coverage, APCAD, structural variants, haplotypes and repeat expansions, with an ideogram per chromosome at the bottom. Chromosomes 1–22, X and Y lie one after the other on one axis. A dragged selection opens that region in the chromosome view.

The mitochondrion is missing from the overview, as it is from the Circos plot: 16.6 kb of a 3.1 Gb genome is about 0.006 pixel wide on a 1200-pixel track. It is therefore not on the axis, in the chromosome strip or in the sidebar's *Chromosomes* list, and the overview does not request a track for it. The chromosome view's drop-down list and the mtDNA page do show it (the owner's decision, 2026-10-28).

**Chromosome view** (`ChromosomeViewPage.tsx` with `ChromosomeViewWorkspace.tsx`). At the top is the full ideogram, with the window shown in red. Below it come the sample tracks of each member (coverage, APCAD, SVs, small variants, haplotypes, repeats). At the bottom are the reference tracks (genes, clinical CNVs, DGV, blacklist, segmental duplications) and a zoomed ideogram of the window. Buttons zoom and pan; a field jumps to a gene or locus.

If the family has a region of interest (ROI), it lies across the tracks in both views as a light red band with edges (`RoiMarkerOverlay.tsx`).

**Which tracks a member gets.** The frontend first asks which tracks a sample has (`GET /api/families/{family_id}/track-availability`), so that no empty strip appears for data the family does not have. With a gene panel, availability follows the lists of chapter 8:

- **SVs.** If the view is opened from the SV page with a panel, a member gets an SV track if the panel keeps one of their SVs, by the rule of the SV list: the annotation names a gene of the panel, or the SV overlaps a panel region.
- **Small variants.** A member gets a track if the small-variant list with that panel shows a variant of theirs in the window: a gene of the panel in the annotation (the row's gene symbols or the gene index) or a position in a panel region, however large the panel. Availability builds that condition with the list's own function: `_small_variant_present_sample_names` in `clickhouse_family_variants.py` calls `_small_query_filter_parts`. The regions go to ClickHouse once, as a parameter; a join on 64 kb windows matches them to the family's own rows. A panel without genes and without regions for the family's assembly keeps nothing in the list and therefore offers no track.

    The query is one SELECT over the family's rows, which for each row takes the members with an ALT allele from the calls. With the Mendeliome it takes 0.04 s over a 10 Mb window (measured on ClickHouse 26.8; measurements in `docs/variant-ranking-cache.md`, *A panel of any size*).

**Where in the code:** `frontend/src/pages/genome/`; availability in `backend/app/routers/families_tracks.py`.

## The ideogram

The ideogram is the striped chromosome drawing. The cytobands come from Postgres (`GET /api/chromosomes/{assembly}/{chrom}`) and are drawn in SVG, with a colour per stain code (e.g. `gpos100`, `acen` for the centromere). On the full ideogram the user selects a range; the zoomed ideogram shows only the window shown. On the genome overview a chromosome is only a few pixels wide; there the bands are merged per pixel group into the predominant stain.

**Where in the code:** `frontend/src/components/visualizations/Ideogram.tsx` and `ZoomedIdeogram.tsx`; the helper functions in `frontend/src/lib/ideogram.ts` and `stainColors.ts`.

## The sample tracks

- **Coverage and segments.** A canvas shows the log ratio of the coverage as dots per bin, with the CNV segments as lines over them. The colour thresholds (gain, loss) are configurable **per browser**. The legend always shows them and marks values that differ from the defaults, because another workstation may colour the same data differently.

    The data come from the interval table in ClickHouse: the overview requests all chromosomes at once (`GET /api/bed/{sample_id}/coverage/batch` and `…/segments/batch`), the chromosome view one chromosome (`GET /api/bed/{sample_id}/coverage` and `…/segments`). Each CNV caller gets its own track; with coverage and segments the views name the caller (`source`). If the sample has coverage from several callers in the requested area, a coverage request without a caller is refused (400). The read routes answer in JSON, also without `format`; a request for the text format gets 422.

    `GET /api/families/{family_id}/track-availability` names the callers, with for each coverage track the conversion (`coverage_normalizations`) and the content (`coverage_contents`: `read_depth`, `log2_ratio` or `unknown`, by the rule of the depth readers, chapter 6). Each track goes on the axis of its content (`coverageScale` in `frontend/src/lib/coverageSources.ts`, the prop `scale` of `CoverageSegmentsChart`):

    - a **log2 ratio** on the log2-ratio axis, with the colours for gain and loss and the caller's segments;
    - the **maternal plasma of a conventional NIPT** (ccnv), a log2 ratio too, on a NIPT axis of its own (±0.3): a fetal trisomy raises the plasma by only log2(1 + FF/2), some +0.08 at a 12 % fetal fraction, which the colour thresholds (±0.35) would never colour. Each segment and its bins take the colour of ccnv's call (gain, loss, or purple for a call ccnv calls discordant with the fetal fraction), and dashed lines show where a fetal trisomy and monosomy lie at ccnv's effective fetal fraction, on chromosomes 1–22 and X (`frontend/src/lib/ccnvTracks.ts`). The track's header shows that fetal fraction, the fetal sex and the sex-chromosome call (`ccnv` in the track availability), and every estimate on hover;
    - **read depth** (a coverage BED declared as read depth, or HiFiCNV's depth, stored as read because the file gave no usable autosomal median) on a depth axis from 0 to a round number above the 99th percentile of the values shown, without those colours or segments, which are log2 ratios;
    - values of an **unknown kind** (a coverage BED without a declaration, a conversion the viewer does not know) on their own range, without an axis.

    The track's header says why: in a neutral style for HiFiCNV's depth, in the warning colour for unknown values (`coverageScaleNote`). A segments read delivers each segment with the values its caller gives beside it (`details`: for ccnv the call, z-score, bins, expected ratio, implied fetal fraction, discordance and origin, for WisecondorX the z-score); the views show them over the segment under the pointer.

- **APCAD.** A scatter plot of the B-allele frequency, with two homozygous bands and a heterozygous middle band: the signal that distinguishes parental haplotypes. Data: `GET /api/bed/{sample_id}/apcad` and `…/apcad_pcf`, on the overview their `…/batch` variant.

    A sample has millions of markers, so the server chooses a selection: only informative markers that passed the quality filter, spread over the chromosomes according to the number of stored sites, so that each chromosome keeps its own density per Mb. Within a chromosome the best come first, spread so that all bands stay visible. A ranking on quality over the whole genome would draw a chromosome denser than its sites are: joint variant calling scores a chromosome's markers higher where one sample of the family has extra reads there (e.g. an embryo with a trisomy), and does so for all embryos.

    If a track has not a single marker with a parental origin (HiFiCNV's minor-allele fraction, from a bigWig), it shows its unphased markers, on an axis from 0 to 0.5. An embryo from the PGT pipeline with its APCAD table shows the sites that that run read (parents homozygous for opposite alleles): for a euploid embryo a middle band around 0.5 per parent, without the homozygous bands of a trio VCF.

- **Parental origin** (`ParentalOriginChart.tsx`). For an embryo with the PGT pipeline's segregation table, in both views; off by default, turned on with the tick box *Parental origin (aneuploidy)*. Data: `GET /api/bed/{sample_id}/apcad_segregation` and `…/batch`, read-only.

    Drawn like the pipeline's own figures, in two panels: *Mother heterozygous* (group 2: mother heterozygous, father homozygous) and *Father heterozygous* (group 3). Each site sits at the embryo's allele fraction (0 to 1), coloured by the other parent's homozygous allele: amber for the reference allele (2B, 3B), the heterozygous parent's colour for the alternative allele (2A, 3A). The server stores each site at the fraction of the allele that the homozygous parent lacks, with its group (`group`); the chart calculates the allele fraction back from that. In the genome overview all chromosomes are side by side, so that an aneuploid chromosome stands out among the others.

    How to read a panel:

    - bands at 0, ½ and 1: one homologue from that parent (disomy);
    - only ⅓ and ⅔, nothing at 0 or 1: both homologues (heterodisomy);
    - 0, ⅓, ⅔ and 1: two copies of one homologue (isodisomy), or in a trisomy the panel of the parent who gave one copy of the three.

    Read against the centromere, heterodisomy at the centromere points to meiosis I; isodisomy at the centromere that becomes heterodisomic beyond a crossover, to meiosis II; isodisomy over the whole chromosome, to a mitotic error. CoGA draws the sites and makes no classification from them.

- **Small variants.** One mark per variant of the sample (`GET /api/families/{familyId}/small-variants` in track mode, with an upper limit). The marks (`frontend/src/lib/smallVariantMarks.ts`):

    - a variant that ClinVar calls (likely) pathogenic: a larger red diamond, whatever its impact;
    - every other variant: a dot 6 px wide, whose colour gives the class: green for ClinVar (likely) benign, then by impact orange for HIGH, yellow for MODERATE and grey for LOW, MODIFIER or none;
    - a review tag: a ring in the tag's colour around the mark.

    The dots differ only by their colour; the tooltip names the impact and the ClinVar class, and the track's accessible name counts the diamonds and the HIGH variants. The most salient mark lies on top: grey first, then green, yellow, orange and finally the diamonds.

    If the sample is not a father or mother and the family has at least one parent, the track splits into rows by parental origin (paternal, undetermined, maternal): with both parents by their genotypes, otherwise by the child's phase.

    Up to a window of 250 kb the track draws every variant. In a wider window it uses `salient_only` to request only the variants it draws as salient: ClinVar (likely) pathogenic, or HIGH or MODERATE impact on a transcript and not ClinVar (likely) benign (`_small_salient_condition` in `clickhouse_variant_queries.py`, a filter of `SmallVariantQueryFilters` like the others, with its case in the parity test). A WGS trio has about 1.5 variants per kb per member: a 2 Mb window would otherwise read thousands of variants per member with their full annotation, only to draw them as grey dots. The track's header says so (`SMALL_VARIANT_ALL_MARKS_MAX_SPAN` and `smallVariantTrackScope` in `frontend/src/lib/trackSampling.ts`).

- **Structural variants.** The overview uses `SvTrack` (canvas, with a computed hover), the chromosome view `VariantTrack` (SVG shapes with their own mouse handling). Both fetch `GET /api/families/{familyId}/structural-variants`, in track mode for the track's sample. Every SV that the SV list shows is drawn, in one of six rows: DEL, DUP, INV, INS, BND and, at the bottom, *Other*.

    - An SV that removes a copy goes under DEL; one that adds a copy, under DUP. The backend says for each SV which of the two it is (`copy_change`), with the rule of the SV ranking and the second hit (`sv_copy_change.py`), from the call of the track's sample. So a `DEL:ME` goes under DEL and a `DUP:TANDEM` under DUP.
    - A `CNV` goes under DEL or DUP by its copy number; a male has one copy on chrX and chrY outside the PARs. A CNV without a stored copy number counts as a loss, as in the ranking.
    - Every other SV goes under its type without subtype (an `INS:ME` under INS), a `TRA` under BND. A type that names no row (e.g. `CPX`) and a CNV that changes no copy go in the grey *Other* row.

    DEL, DUP and *Other* are bars, INV an outlined bar, INS a vertical stroke and BND a triangle. The tooltip names the type as stored, for a CNV with the copy change with which it was drawn, and for each call the copy number (CN) if the call has one. It writes the chromosome as the header names it (`chr1`), however it is stored.

    The overview places an SV on its chromosome as the axis names it (`normalizeChrom` in `frontend/src/lib/chromosomes.ts`), however the VCF wrote it (`chrX` or `X`). The chromosome view of chrM shows an SV on the mitochondrion, such as the mitochondrial SVs (callset `mito_sv`, stored as `M`); the overview leaves out the mitochondrion and therefore does not draw or count such an SV.

- **Haplotypes.** On the overview `GenomeHaplotypeTrack`, per chromosome `HaplotypePhasedTrack`, with data from `GET /api/families/{family_id}/haplotypes` and `…/phased-markers`; the overview requests the haplotypes of all chromosomes with `…/haplotypes/batch`. The import stores a block's name without `chr`; the overview places a block, like an SV, on its chromosome as the axis names it.

    - **The raw phased markers** are coloured per marker, without grouping; the haplotype track is the cleaned-up version (`docs/haplotype-segregation-analysis.md`). The marker overlay appears only if both parents are present, the user turns it on and the window is 5 Mb or smaller (`PHASED_MARKER_MAX_SPAN`); in a wider window the track's header says that the markers appear on zooming in.
    - **A child's header** names, next to the track name, its informative sites in the window, or, above a Mendelian error ratio of 2 %, those errors in red (`HaplotypeMarkerNote.tsx`). The header and the track read the same markers, from one request (`usePhasedMarkers`).
    - **The risk haplotype** is derived in the region of interest. Its lane gets an edge in the risk colour at the top and bottom, solid for affected and dashed for carrier, and in the chromosome view a light tint of that colour behind the band, which keeps its homologue colour (`drawHaplotypeRiskLane` in `frontend/src/lib/haplotypeCanvas.ts`). On the overview it is drawn only on the chromosome of that region, because the labels of the homologues apply per chromosome. Without a region of interest the overview draws no risk and shows the status "not assessed".
    - **A corrected phase.** A continuous line with a small triangle (in the warning colour) marks where CoGA corrected a parent's phase. There (almost) all the couple's children switched haplotype at the same time on that parent's side: more likely a phase switch in the parent than crossovers. From that point on, the haplotype blocks, the lineage colouring and the raw markers read that parent's phase swapped; the genotypes themselves stay as they were called. If such a correction lies in or just next to the ROI, each embryo shows the warning **phase corrected**.

- **Repeat expansions.** One mark per locus in the colour of the status, from `GET /api/families/{familyId}/repeat-expansions/sample/{sampleId}`. The statuses are normal, review, not assessed, intermediate, premutation and pathogenic; any other status is coloured as unknown. *Not assessed* is a count at a locus without thresholds, outside the benign range that the catalogue gives for it. The status is that of the repeat page: the call re-assessed against the catalogue row that currently classifies it, with the remark for two different chrX alleles in a male. On the overview a locus, like an SV, lies on its chromosome as the axis names it. The chromosome view of chrM shows a locus on the mitochondrion, however it is stored (`chrM` or `M`); the overview leaves out the mitochondrion.

    - **A corrected repeat count.** If QA has recorded a count correction for a locus (chapter 8), the track draws the status of the corrected count. The tooltip names the corrected counts with TRGT's counts in brackets, and the correction (`allele_repeat_counts`, `allele_trgt_repeat_counts`, `count_offset`).
    - **A count close to a boundary.** The tooltip names every count within 3 repeats of a boundary, with the boundary, why, and what is known about TRGT's count for the gene (`count_unit_warnings` in the same response; chapter 8). The track's accessible name counts such loci (*1 near a boundary*).

**Aneuploidy and large SVs in PGT: the analyst assesses.** On each embryo's segment, copy-number (coverage) and APCAD tracks the analyst assesses aneuploidy and structural variants larger than 10 Mb themselves. CoGA shows those tracks, but has no automatic aneuploidy detection (`TF-09a`, REQ-PGT-008). The limits of that assessment (mosaicism above 35 %, SVs above 10 Mb) are still to be established by the clinical validation (`TF-10` §3.2).

**Where in the code:** `frontend/src/components/visualizations/` (`CoverageSegmentsChart.tsx`, `ApcadChart.tsx`, `SmallVariantTrack.tsx`, `SvTrack.tsx`, `VariantTrack.tsx`, the haplotype and repeat tracks); the small-variant marks in `frontend/src/lib/smallVariantMarks.ts`; the APCAD selection in `fetch_apcad_downsampled` (`backend/app/services/clickhouse_interval_tracks.py`).

## The reference tracks

These tracks are not tied to a sample and show reference annotation over the window shown:

| Track | Endpoint | Shows |
| --- | --- | --- |
| `GeneTrack` | `GET /api/genes/{assembly}/{chrom}` | Genes with exons and introns (D3), one transcript per gene: the MANE Select transcript (GENCODE, or HGNC's MANE accessions for a RefSeq reference), otherwise Ensembl's canonical transcript (GENCODE's tag `Ensembl_canonical`), otherwise the longest. Chevrons along the introns point in the direction of transcription (› on the + strand, ‹ on the − strand). The tooltip names the strand, the transcript, above an exon or intron its number counted from the start of the transcript (*Exon 3 / 12*), and the panels the gene is in |
| `CnvTrack` | `GET /api/cnvs/{assembly}/{chrom}` | Clinical CNVs; a click opens the detail page |
| `DgvTrack` | `GET /api/dgv/{assembly}/{chrom}` | DGV variants; with too much overlap the server shows a density profile instead of individual variants |
| `BlacklistTrack` · `SegmentalDuplicationTrack` | `GET /api/blacklist/{assembly}/{chrom}` · `GET /api/segmental-duplications/{assembly}/{chrom}` | Unreliable regions and segmental duplications |

**Where in the code:** the components in `frontend/src/components/visualizations/`; the routers in `backend/app/routers/`.

## Large and still correct: downsampling

How many points or segments a track shows at most depends on its width in pixels (`frontend/src/lib/trackSampling.ts`). That does not affect correctness: CoGA summarises only at overview level, where several positions fall on the same pixel anyway. Zooming in gives more detail, and all records of the window as soon as their number falls within the track's limit. For APCAD, quality and signal drive the selection, not chance. The exact assessment never happens on these tracks, but on the filtered variant lists (chapter 8) and in IGV.

## Circos

The Circos plot lays the chromosomes out in a circle and draws structural variants as arcs between them, with D3 on SVG. Each SV is drawn as in the SV tracks, with the calls of the whole family: a copy loss as a deletion, a copy gain as a duplication, a `TRA` as a breakend. An SV that names no row of the tracks is thin and grey (*Other* in the legend).

The page takes the assembly of the family's project (`useFamilyReference`, like the other views). It fetches the chromosomes of that assembly, with their lengths and bands (`GET /api/chromosomes/{assembly}/details`), and, as soon as the assembly is known, also all the family's SVs. It draws the chromosomes 1–22, X and Y that the assembly has, each at its own length; it leaves out contigs and the mitochondrion. An SV lies on its chromosome however the name is stored (`chrX`, `X` or `x`, via `normalizeChrom`). An SV's tooltip writes the chromosomes as the chromosome view names them (`chr1:10 → chr2:30`).

If the page cannot draw the assembly, it says why and draws nothing. That happens with:

- a family without a linked project;
- a family or project list that did not load (with a button to try again);
- an assembly without chromosome lengths;
- an assembly that has none of the chromosomes 1–22, X and Y;
- an assembly with chromosomes that the plot cannot place (chr23 and higher, W, Z). The page names those chromosomes: without them the plot would pass for the whole genome.

A click on a chromosome opens the chromosome view; a click on a translocation (BND or TRA) opens the genome overview with both chromosomes.

**Where in the code:** `frontend/src/pages/genome/CircosPlotPage.tsx` and `frontend/src/components/visualizations/CircosPlot.tsx`.

## IGV: reads at base level

For the finest check, the individual reads, CoGA embeds the **IGV** browser. `IgvViewer.tsx` requests two lists: the alignment files of all family members, the proband first (`GET /api/cram/{familyId}/manifest`), and separately the CNV caller's signal files (depth, allele fraction and copy number: `GET /api/signal-tracks/{familyId}/manifest`). If that second request fails, the reads still load and the page reports that the signal tracks are missing. A sample with an alignment of more than one analysis, such as short-read and then long-read WGS of the same DNA, gets a track of each: the manifest gives each alignment with its analysis (`analysis_type`, also as `?analysis=` in the addresses), and the track carries the sample's name with that analysis (`alignmentTrackName` in `lib/alignmentTracks.ts`). In a family of several analyses every track names its analysis, also when only one alignment of the sample can be shown; one that no import recorded is named *analysis not recorded*.

The backend serves the files with the same access control as the rest: every endpoint of `cram.py` and `signal_tracks.py` checks that the sample belongs to a family the user may see, and otherwise gives `404`. With *range requests* IGV requests only the bytes it needs. IGV never gets the address of a bucket or share: every byte comes via the backend.

Every request carries the token the session has at that moment: each track's `oauthToken` function asks `sessionAccessToken` from `lib/api.ts` for it, like the API client. A Microsoft token lives about an hour; a token that IGV read only once, on opening, would leave a long session without tracks.

Where the backend looks for a file:

- **The package folder.** First at the path that the import recorded for the sample, in the package folder read by the import of the analysis that recorded it. A package is read where it lies, so also on a share or bucket mounted as an import folder (`/data/share`, `/data/imports`), and in a folder named differently from the family. That package folder counts only if, with symbolic links followed, it lies under a `FAMILY_IMPORT_ROOTS` folder and is not that folder itself (`local_family_package_folder` in `services/family_package_source.py`). The file must lie in the package folder, and a recorded CRAM/BAM or index must have that file type's extension. A changed database row can thus at most point to another file of that kind in the family's package, never to another family's package.
- **The family's own folders.** A CRAM is combined only with its own index. A recorded alignment whose file is gone is missing: the backend does not look for it elsewhere, where it could find another analysis's reads. Only for a sample whose alignment no import recorded does the backend look in `data/<family>/` and `data/families/<family>/`: the reads at their fixed places (`<sample>.cram` with `<sample>.cram.crai`). The signal files are looked for there at the recorded path. A path that leads out of such a folder is not looked up: a sample ID such as `../F2/S2`, or a changed database row with `..` or an absolute path to somewhere else. A file that, via a symbolic link, lies in another family's folder or outside the data folder is not listed and not served.

IGV draws the reads and variants on the genome of the assembly from the family's project (`mapAssemblyToIgvGenome` in `frontend/src/lib/reference.ts`):

- `hg38` for GRCh38 or hg38;
- `hg19` for GRCh37 or hg19 (in upper or lower case, with or without a patch suffix: the rule of `grc_build` in the backend);
- `hs1` for T2T-CHM13v2.0 under any of its names (`isT2tChm13`, chapter 4);
- a few others (`mm39`, `mm10`, `equCab3`).

If CoGA knows no IGV genome for the assembly, the page opens none and says so ("No IGV genome for …"): on the genome of another assembly the reads would be in the wrong place.

**Where in the code:** `frontend/src/components/IgvViewer.tsx` and `frontend/src/pages/families/FamilyIgvPage.tsx`, with the genome in `frontend/src/lib/reference.ts`; `backend/app/routers/cram.py` and `signal_tracks.py`, with the family's own folders in `backend/app/core/family_folders.py`.

## The pedigree

The pedigree is not fetched separately, but computed in the browser from the members and relationships that come with the family. The drawing follows the parent IDs and the relationships, not the flat role field (which also says `mother`/`father` for grandparents).

- Males are squares, females circles, unknown sex a diamond; affected members are filled, carriers half filled; consanguineous partners get a double line.
- A member related to another member in an unknown degree (a relationship `relative`, such as a PGT index of whom only the side is known) stands next to that member in that member's generation, connected by a dotted arc with a question mark.
- Two samples of one person (a relationship `same_individual`, such as the plasmas of a monogenic and a conventional NIPT of one pregnancy under two sample IDs) are one symbol, drawn under the sample with the most links in the pedigree (on a tie the first member) and labelled with all IDs (*PLASMA1 / PLASMA2*). Every other link to either sample runs to that symbol. The symbol draws what either sample records (sex, affected and carrier status, parents): one sample's unknown value never hides the other's known one. Its QC ring is the worst of the samples, the HPO badge shows when any of them has HPO terms, and the accessible name says how many members are drawn with several samples. The members table keeps a row per sample.
- The QC verdict per sample is a ring that differs not only by colour: a thin ring with ✓ for *pass*, a dashed one with ! for *warn* and a thick one with ✕ for *fail*.

**Where in the code:** `frontend/src/components/visualizations/Pedigree.tsx`.

## Errors and limits

A track, a page or IGV always shows a failed request as an error, never as empty data.

- If the request for the available tracks or for the chromosome lengths fails, the workspace reports "Could not load … — this is not an empty result", with a button to try again.
- While panning, the previous image stays only while the new window loads. If the request fails, the track shows the error above an empty window, never the data of the previous window.
- A window without width requests nothing and is called "no region in view".
- If a window has more variants or SVs than a track can draw, the track says so ("Too many … Zoom in or apply filters.") instead of drawing part of them; on the overview and in Circos this also happens above the backend's limit.

**Chromosome names.** In the track names, the chromosome lists and the header the mitochondrion is called `chrM`, however the data write it. The chromosome view's *Chromosome* drop-down list names each chromosome like the header (`chr1`, `chrX`, `chrM`); the genome overview's chromosome strip writes them without `chr` (`1`, `X`). The ideograms follow the same rule; their accessible name writes the chromosome without `chr` (*Chromosome M ideogram*). A band of the mitochondrion, which has no cytobands, is called `chrM` in the tooltip and in the zoomed ideogram's accessible name, just like a band without a name of its own. The repeat tracks read a locus's chromosome as the API reads it (`MT`, `M`, `chrM` and `chrMT` are one chromosome). The *ROI marker review* page also writes the ROI's chromosome this way (`chr1`, `chrX`): in the explanation at the top, the window shown, the title of each site and the tooltip. It requests the markers and the blocks with the name as the family stores it.

**An interrupted drag.** Sometimes a drag cannot end with the release of the mouse button: the track on which it started disappears during the drag, the pointer is cancelled (`pointercancel`), or the window loses focus and with it the release. The gesture then breaks off without moving the window, and removes its offset or selection band from the tracks.

## Security and traceability

- **Everything behind a login.** The reference routers (`chromosomes`, `cnvs`, `dgv`, `blacklist`, `segmental-duplications`) require login on all their endpoints; the other routers require it per endpoint.
- **Project-scoped access.** Family and sample tracks go through the access checkpoint (chapter 2). Someone who knows a `family_id` but has no access to it does not get the tracks.
- **Uploads for administrators only.** Uploading a BED track (`POST /api/bed/upload/{sample_id}/{bed_type}`) requires `get_current_admin_user`, and the source file is registered.
- **Provenance of the tracks.** Each row in the interval table carries its source and file name. `sample_interval_track_sources` keeps, per sample, kind, source and file, the number of rows and the upload time, and also the conversion if the import converts a track's values (`normalization`): for HiFiCNV the copy number and the depth become a log2 ratio. If a depth file gives no usable autosomal median, the depth stays as read and the import records that as `none`. A coverage BED records what it declared to contain (`content`: `read_depth`, `log2_ratio`, or `unknown` without a declaration; chapter 6).

## Key files

| File | Role |
| --- | --- |
| `frontend/src/pages/genome/GenomeOverviewPage.tsx` · `GenomeOverviewWorkspace.tsx` | Genome overview |
| `frontend/src/pages/genome/ChromosomeViewPage.tsx` · `ChromosomeViewWorkspace.tsx` | Chromosome view |
| `frontend/src/pages/genome/CircosPlotPage.tsx` · `frontend/src/components/visualizations/CircosPlot.tsx` | Circos |
| `frontend/src/components/visualizations/` | All tracks, the ideogram, the ROI band and the pedigree |
| `frontend/src/lib/smallVariantMarks.ts` · `trackSampling.ts` | Small-variant marks; limits per track |
| `frontend/src/components/visualizations/svTypes.ts` | The row and the colour with which the SV tracks and the Circos plot draw an SV |
| `frontend/src/components/IgvViewer.tsx` | The embedded IGV browser |
| `backend/app/routers/families_tracks.py` · `bed.py` | Family tracks and interval tracks |
| `backend/app/routers/cram.py` · `signal_tracks.py` | Alignment and signal files, with access control |
| `backend/app/services/clickhouse_interval_tracks.py` | The interval storage and the APCAD selection |
