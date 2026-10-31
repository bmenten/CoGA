import React from 'react';

type Stain = 'gneg' | 'gpos25' | 'gpos50' | 'gpos75' | 'gpos100' | 'acen';
type Point = [number, number];

// Chromosome 8's G-bands in GRCh38, as CoGA's reference data stores them: [start, end, stain].
// The page is shown before sign-in, so it cannot read them from the API.
const CHR8_LENGTH = 145_138_636;
const CHR8_BANDS: Array<[number, number, Stain]> = [
  [0, 2_300_000, 'gneg'],
  [2_300_000, 6_300_000, 'gpos75'],
  [6_300_000, 12_800_000, 'gneg'],
  [12_800_000, 19_200_000, 'gpos100'],
  [19_200_000, 23_500_000, 'gneg'],
  [23_500_000, 27_500_000, 'gpos50'],
  [27_500_000, 29_000_000, 'gneg'],
  [29_000_000, 36_700_000, 'gpos75'],
  [36_700_000, 38_500_000, 'gneg'],
  [38_500_000, 39_900_000, 'gpos25'],
  [39_900_000, 43_200_000, 'gneg'],
  [43_200_000, 45_200_000, 'acen'],
  [45_200_000, 47_200_000, 'acen'],
  [47_200_000, 51_300_000, 'gneg'],
  [51_300_000, 51_700_000, 'gpos75'],
  [51_700_000, 54_600_000, 'gneg'],
  [54_600_000, 60_600_000, 'gpos50'],
  [60_600_000, 61_300_000, 'gneg'],
  [61_300_000, 65_100_000, 'gpos50'],
  [65_100_000, 67_100_000, 'gneg'],
  [67_100_000, 69_600_000, 'gpos50'],
  [69_600_000, 72_000_000, 'gneg'],
  [72_000_000, 74_600_000, 'gpos100'],
  [74_600_000, 74_700_000, 'gneg'],
  [74_700_000, 83_500_000, 'gpos75'],
  [83_500_000, 85_900_000, 'gneg'],
  [85_900_000, 92_300_000, 'gpos100'],
  [92_300_000, 97_900_000, 'gneg'],
  [97_900_000, 100_500_000, 'gpos25'],
  [100_500_000, 105_100_000, 'gneg'],
  [105_100_000, 109_500_000, 'gpos75'],
  [109_500_000, 111_100_000, 'gneg'],
  [111_100_000, 116_700_000, 'gpos100'],
  [116_700_000, 118_300_000, 'gneg'],
  [118_300_000, 121_500_000, 'gpos50'],
  [121_500_000, 126_300_000, 'gneg'],
  [126_300_000, 130_400_000, 'gpos50'],
  [130_400_000, 135_400_000, 'gneg'],
  [135_400_000, 138_900_000, 'gpos75'],
  [138_900_000, CHR8_LENGTH, 'gneg'],
];

const CENTROMERE: [number, number, number] = [43_200_000, 45_200_000, 47_200_000];
// The band the detail below zooms into: 8q24.21.
const REGION: [number, number] = [126_300_000, 130_400_000];

const LEFT = 10;
const RIGHT = 710;
const IDEOGRAM_TOP = 16;
const IDEOGRAM_HEIGHT = 14;
const PANEL_TOP = 48;
const PANEL_BOTTOM = 206;

const toX = (position: number) => LEFT + (position / CHR8_LENGTH) * (RIGHT - LEFT);
const round = (value: number) => Math.round(value * 100) / 100;

// The chromosome's outline: two arms with rounded ends, pinched at the centromere.
const chromosomeOutline = (() => {
  const top = IDEOGRAM_TOP;
  const bottom = IDEOGRAM_TOP + IDEOGRAM_HEIGHT;
  const radius = IDEOGRAM_HEIGHT / 2;
  const [acenStart, centre, acenEnd] = CENTROMERE.map((position) => round(toX(position)));
  return [
    `M${LEFT + radius},${top}`,
    `H${acenStart} L${centre},${top + 4.5} L${acenEnd},${top} H${RIGHT - radius}`,
    `A${radius} ${radius} 0 0 1 ${RIGHT - radius},${bottom}`,
    `H${acenEnd} L${centre},${bottom - 4.5} L${acenStart},${bottom} H${LEFT + radius}`,
    `A${radius} ${radius} 0 0 1 ${LEFT + radius},${top} Z`,
  ].join(' ');
})();

// Read depth across the zoomed region as CoGA's coverage track draws it: a dot per bin,
// coloured by the segment it falls in, and a line for each segment. A deletion over two exons
// of the first gene halves the depth, a duplication over two exons of the second raises it by
// half, and a small inversion leaves it flat: only the crossed call marks it.
const COVERAGE_START = 16;
const COVERAGE_END = 704;
const BIN = 4;
// The depth of two, one and three copies, as y.
const LEVEL = { neutral: 86, loss: 104, gain: 68 } as const;
type Call = keyof typeof LEVEL;
const SEGMENTS: Array<[number, number, Call]> = [
  [COVERAGE_START, 196, 'neutral'],
  [196, 262, 'loss'],
  [262, 466, 'neutral'],
  [466, 544, 'gain'],
  [544, COVERAGE_END, 'neutral'],
];
const DELETION: [number, number] = [196, 262];
const DUPLICATION: [number, number] = [466, 544];
const INVERSION: [number, number] = [598, 642];
const SV_ROW = 120;
const SV_CALLS: Array<['del' | 'dup' | 'inv', [number, number]]> = [
  ['del', DELETION],
  ['dup', DUPLICATION],
  ['inv', INVERSION],
];
// A bin's scatter around its segment's depth: a fixed pseudo-random value in [-1, 1].
const scatter = (bin: number) => {
  const value = Math.sin(bin * 12.9898) * 43758.5453;
  return (value - Math.floor(value)) * 2 - 1;
};
const coverageBins = (() => {
  const bins: Array<[number, number, Call]> = [];
  for (let x = COVERAGE_START + BIN / 2, bin = 0; x < COVERAGE_END; x += BIN, bin += 1) {
    const [, , call] = SEGMENTS.find(([start, end]) => x >= start && x < end) ?? SEGMENTS[0];
    bins.push([x, round(LEVEL[call] + scatter(bin) * (call === 'loss' ? 3.5 : 4.5)), call]);
  }
  return bins;
})();

// Two genes side by side, the first on the forward strand and the second on the reverse:
// exons as [x, width], the outer ends of the first and last exons untranslated (thinner).
const GENE_Y = 186;
type Gene = { exons: Point[]; strand: 1 | -1 };
const GENES: Gene[] = [
  {
    strand: 1,
    exons: [[34, 16], [80, 10], [124, 14], [170, 9], [206, 12], [238, 10], [284, 9], [312, 20]],
  },
  {
    strand: -1,
    exons: [[392, 18], [440, 9], [474, 12], [522, 11], [566, 14], [612, 9], [650, 11], [674, 18]],
  },
];
const UTR = 8;
// One strand arrow in the middle of each intron long enough to hold it.
const strandArrows = (gene: Gene) =>
  gene.exons.slice(1).flatMap(([start], index) => {
    const [previousStart, previousWidth] = gene.exons[index];
    const gap = start - (previousStart + previousWidth);
    return gap >= 14 ? [round(previousStart + previousWidth + gap / 2)] : [];
  });
const inExon = (x: number) =>
  GENES.some((gene) => gene.exons.some(([start, width]) => x >= start && x <= start + width));

// Called variants: [x, marker height, classification, kind]. A circle is a single-nucleotide
// variant and a diamond an indel; each stands on its exon, or on the intron line.
type VariantClass = 'pathogenic' | 'vus' | 'benign';
const VARIANTS: Array<[number, number, VariantClass, 'snv' | 'indel']> = [
  [85, 160, 'pathogenic', 'snv'],
  [131, 166, 'vus', 'indel'],
  [289, 163, 'benign', 'snv'],
  [444, 163, 'vus', 'snv'],
  [527, 158, 'pathogenic', 'indel'],
  [586, 168, 'benign', 'snv'],
  [655, 164, 'benign', 'indel'],
];

/**
 * A genome-browser motif: chromosome 8's real G-banded ideogram with band 8q24.21 selected,
 * and below it that region as CoGA shows it: a coverage track with a deletion, a
 * duplication and an inversion and their calls, variants marked by class, and two genes with
 * their exons. Drawn with classes only (no inline styles), and hidden from assistive
 * technology: it says nothing the text beside it does not.
 */
const GenomeMotif: React.FC = () => {
  const regionStart = round(toX(REGION[0]) - 1);
  const regionEnd = round(toX(REGION[1]) + 1);
  const regionBottom = IDEOGRAM_TOP + IDEOGRAM_HEIGHT + 2;
  return (
    <svg
      className="auth-motif"
      viewBox={`0 0 720 ${PANEL_BOTTOM + 4}`}
      aria-hidden="true"
      focusable="false"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <clipPath id="auth-motif-chromosome">
          <path d={chromosomeOutline} />
        </clipPath>
      </defs>

      <text className="auth-motif-label" x={LEFT} y="11">
        chr8
      </text>
      <g clipPath="url(#auth-motif-chromosome)">
        {CHR8_BANDS.map(([start, end, stain]) => (
          <rect
            key={start}
            className={`auth-motif-band auth-motif-band--${stain}`}
            x={round(toX(start))}
            y={IDEOGRAM_TOP}
            width={round(toX(end) - toX(start))}
            height={IDEOGRAM_HEIGHT}
          />
        ))}
      </g>
      <path className="auth-motif-outline" d={chromosomeOutline} />
      <rect
        className="auth-motif-region"
        x={regionStart}
        y={IDEOGRAM_TOP - 2}
        width={round(regionEnd - regionStart)}
        height={IDEOGRAM_HEIGHT + 4}
        rx="2"
      />
      <text className="auth-motif-label auth-motif-label--region" x={round((regionStart + regionEnd) / 2)} y="11">
        8q24.21
      </text>

      <path
        className="auth-motif-zoom"
        d={`M${regionStart},${regionBottom} L${LEFT},${PANEL_TOP} M${regionEnd},${regionBottom} L${RIGHT},${PANEL_TOP}`}
      />
      <rect
        className="auth-motif-panel"
        x={LEFT}
        y={PANEL_TOP}
        width={RIGHT - LEFT}
        height={PANEL_BOTTOM - PANEL_TOP}
        rx="5"
      />

      {coverageBins.map(([x, y, call]) => (
        <circle key={x} className={`auth-motif-bin auth-motif-bin--${call}`} cx={x} cy={y} r="1.6" />
      ))}
      {SEGMENTS.map(([start, end, call]) => (
        <line
          key={start}
          className={`auth-motif-segment auth-motif-segment--${call}`}
          x1={start}
          y1={LEVEL[call]}
          x2={end}
          y2={LEVEL[call]}
        />
      ))}

      {/* Each call under the coverage, with a dashed line from the dots down to it at either end. */}
      {SV_CALLS.map(([type, [start, end]]) => {
        const top = (type === 'dup' ? LEVEL.gain : LEVEL.neutral) - 7;
        return (
          <g key={type}>
            <path
              className="auth-motif-breakpoint"
              d={`M${start},${top} V${SV_ROW} M${end},${top} V${SV_ROW}`}
            />
            <rect className={`auth-motif-call auth-motif-call--${type}`} x={start} y={SV_ROW} width={end - start} height="5" rx="1.5" />
          </g>
        );
      })}
      <path
        className="auth-motif-call-cross"
        d={`M${INVERSION[0]},${SV_ROW} L${INVERSION[1]},${SV_ROW + 5} M${INVERSION[0]},${SV_ROW + 5} L${INVERSION[1]},${SV_ROW}`}
      />

      {GENES.map((gene) => {
        const [firstStart] = gene.exons[0];
        const [lastStart, lastWidth] = gene.exons[gene.exons.length - 1];
        return (
          <g key={firstStart}>
            <line className="auth-motif-gene" x1={firstStart} y1={GENE_Y} x2={lastStart + lastWidth} y2={GENE_Y} />
            {strandArrows(gene).map((x) => (
              <path
                key={x}
                className="auth-motif-strand"
                d={`M${x - 2.5 * gene.strand},${GENE_Y - 3.5} L${x + 2.5 * gene.strand},${GENE_Y} L${x - 2.5 * gene.strand},${GENE_Y + 3.5}`}
              />
            ))}
            {gene.exons.map(([x, width], index) => {
              const first = index === 0;
              const last = index === gene.exons.length - 1;
              const codingX = first ? x + UTR : x;
              const codingWidth = width - (first || last ? UTR : 0);
              return (
                <g key={x}>
                  {(first || last) && (
                    <rect className="auth-motif-exon" x={first ? x : x + width - UTR} y={GENE_Y - 4} width={UTR} height="8" />
                  )}
                  <rect className="auth-motif-exon" x={codingX} y={GENE_Y - 7} width={codingWidth} height="14" />
                </g>
              );
            })}
          </g>
        );
      })}

      {VARIANTS.map(([x, y, variantClass, kind]) => (
        <g key={x} className={`auth-motif-variant auth-motif-variant--${variantClass}`}>
          <line className="auth-motif-stem" x1={x} y1={inExon(x) ? GENE_Y - 7 : GENE_Y} x2={x} y2={y} />
          {kind === 'snv' ? (
            <circle className="auth-motif-marker" cx={x} cy={y} r="5.5" />
          ) : (
            <path className="auth-motif-marker" d={`M${x},${y - 7} L${x + 7},${y} L${x},${y + 7} L${x - 7},${y} Z`} />
          )}
        </g>
      ))}
    </svg>
  );
};

// The logo's letters, each in its colour; the A, as in the logo, has no crossbar. Under them,
// as in the logo, a single strand: an even sine wave whose three crests carry base pairs, the
// first under the C, the last under the A, and its colour turns at each trough.
const WAVE = { start: 3, end: 201, middle: 70, amplitude: 6, firstCrest: 28, period: 73 } as const;
const wavePath = (() => {
  const points: string[] = [];
  for (let x: number = WAVE.start; x <= WAVE.end; x += 1) {
    const phase = ((x - WAVE.firstCrest) / WAVE.period) * 2 * Math.PI;
    points.push(`${x},${round(WAVE.middle - WAVE.amplitude * Math.cos(phase))}`);
  }
  return `M${points.join(' L')}`;
})();
const WORDMARK_BASES: Array<['c' | 'g' | 'a', number]> = [
  ['c', WAVE.firstCrest],
  ['g', WAVE.firstCrest + WAVE.period],
  ['a', WAVE.firstCrest + 2 * WAVE.period],
];
const Wordmark: React.FC = () => (
  <span className="auth-wordmark">
    <span className="sr-only">CoGA</span>
    <svg className="auth-wordmark-logo" viewBox="0 0 204 86" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="auth-wordmark-strand" x1="0" x2="1" y1="0" y2="0">
          <stop className="auth-wordmark-stop--c" offset="0.27" />
          <stop className="auth-wordmark-stop--g" offset="0.36" />
          <stop className="auth-wordmark-stop--g" offset="0.64" />
          <stop className="auth-wordmark-stop--a" offset="0.73" />
        </linearGradient>
      </defs>
      <text className="auth-wordmark-letter auth-wordmark-letter--c" x="28" y="54">
        C
      </text>
      <text className="auth-wordmark-letter auth-wordmark-letter--o" x="76" y="54">
        o
      </text>
      <text className="auth-wordmark-letter auth-wordmark-letter--g" x="124" y="54">
        G
      </text>
      <path className="auth-wordmark-a" d="M154,53 L174,15 L194,53" />
      <path className="auth-wordmark-strand" d={wavePath} />
      {WORDMARK_BASES.flatMap(([letter, x]) =>
        [-5, 0, 5].map((offset) => (
          <line
            key={`${letter}${offset}`}
            className={`auth-wordmark-pair auth-wordmark-letter--${letter}`}
            x1={x + offset}
            y1={WAVE.middle - WAVE.amplitude + 5}
            x2={x + offset}
            y2={WAVE.middle + WAVE.amplitude + 1}
          />
        )),
      )}
    </svg>
  </span>
);

/** What CoGA is, beside the sign-in and sign-up forms. Not a landing page. */
const AuthIntro: React.FC = () => (
  <div className="auth-intro">
    <GenomeMotif />
    <div className="auth-intro-title">
      <Wordmark />
      <p className="auth-intro-name">
        <span className="auth-wordmark-letter--c">C</span>omprehensive{' '}
        <span className="auth-wordmark-letter--g">G</span>enomic{' '}
        <span className="auth-wordmark-letter--a">A</span>nalysis
      </p>
    </div>
    <p className="auth-intro-copy">
      A family-based genome browser for clinical genomics. CoGA starts from the variant files the
      sequencing pipelines have called and annotated, and brings a family&apos;s data together so
      that it can be filtered, viewed, interpreted and signed out in one place.
    </p>
    <ul className="auth-intro-points">
      <li>
        <strong>Built around the family</strong>
        The pedigree, phenotypes and sample QC of a proband with parents and siblings, or of a
        couple with embryos or a pregnancy, with de novo, segregation and compound-heterozygous
        views.
      </li>
      <li>
        <strong>Short and long reads</strong>
        Small variants, SVs, CNVs, repeat expansions, haplotypes and mtDNA, beside coverage,
        ideograms and gene models in one genome view.
      </li>
      <li>
        <strong>Interpretation support</strong>
        Filter presets, gnomAD frequencies, ClinVar, ACMG/AMP evidence and review tags with each
        variant, and the reads in IGV.
      </li>
      <li>
        <strong>Traceable</strong>
        Every query and action is audited. Signing out closes the analysis and freezes what it
        showed; the report in the patient record refers to that version.
      </li>
    </ul>
    <p className="auth-intro-note">
      PGT · monogenic NIPT · carrier screening · rare-disorder diagnostics · mitochondrial disease
    </p>
  </div>
);

export default AuthIntro;
