import React from 'react';

type Stain = 'gneg' | 'gpos25' | 'gpos50' | 'gpos75' | 'gpos100' | 'acen';

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

const CENTROMERE = 45_200_000;
// The band the detail below zooms into: 8q24.21.
const REGION: [number, number] = [126_300_000, 130_400_000];

const LEFT = 10;
const RIGHT = 350;
const IDEOGRAM_TOP = 14;
const IDEOGRAM_HEIGHT = 11;

const toX = (position: number) => LEFT + (position / CHR8_LENGTH) * (RIGHT - LEFT);
const round = (value: number) => Math.round(value * 100) / 100;

// The chromosome's outline: two arms with rounded ends, pinched at the centromere.
const chromosomeOutline = (() => {
  const top = IDEOGRAM_TOP;
  const bottom = IDEOGRAM_TOP + IDEOGRAM_HEIGHT;
  const radius = IDEOGRAM_HEIGHT / 2;
  const acenStart = round(toX(43_200_000));
  const acenEnd = round(toX(47_200_000));
  const centre = round(toX(CENTROMERE));
  return [
    `M${LEFT + radius},${top}`,
    `H${acenStart} L${centre},${top + 3.5} L${acenEnd},${top} H${RIGHT - radius}`,
    `A${radius} ${radius} 0 0 1 ${RIGHT - radius},${bottom}`,
    `H${acenEnd} L${centre},${bottom - 3.5} L${acenStart},${bottom} H${LEFT + radius}`,
    `A${radius} ${radius} 0 0 1 ${LEFT + radius},${top} Z`,
  ].join(' ');
})();

// Read depth across the zoomed region: even coverage with a heterozygous deletion, where it
// halves, over two exons of the gene below.
const COVERAGE_BASE = 74;
const DELETION: [number, number] = [150, 206];
const coverage = (() => {
  const points: Array<[number, number]> = [];
  for (let x = 16; x <= 344; x += 8) {
    const noise = 1.8 * Math.sin(x * 0.09 + 0.5) + 1.2 * Math.sin(x * 0.23 + 2) + 0.7 * Math.sin(x * 0.61);
    const inDeletion = x > DELETION[0] && x < DELETION[1];
    points.push([x, round((inDeletion ? 63 : 52) + noise * (inDeletion ? 0.5 : 1))]);
  }
  return points;
})();
// A smooth trace through the points: a quadratic curve to each midpoint.
const coverageLine = coverage
  .map(([x, y], index) => {
    if (index === 0) return `M${x},${y}`;
    const next = coverage[index + 1];
    return next ? `Q${x},${y} ${round((x + next[0]) / 2)},${round((y + next[1]) / 2)}` : `L${x},${y}`;
  })
  .join(' ');
const coverageArea = `${coverageLine} L344,${COVERAGE_BASE} L16,${COVERAGE_BASE} Z`;

// The gene, on the forward strand: exons as [x, width, untranslated part]. The first exon
// starts and the last one ends untranslated, drawn thinner.
const GENE_Y = 132;
const GENE_START = 26;
const GENE_END = 334;
const EXONS: Array<[number, number, 'start' | 'end' | null]> = [
  [26, 12, 'start'],
  [72, 9, null],
  [116, 12, null],
  [160, 8, null],
  [186, 11, null],
  [234, 10, null],
  [274, 8, null],
  [312, 22, 'end'],
];
const INTRON_ARROWS = [50, 96, 142, 216, 258, 294];

// Called variants, flagged by their classification: [x, flag top, class]. A flag's pole
// stands on the exon it falls in, or on the intron line.
type VariantClass = 'pathogenic' | 'vus' | 'benign';
const VARIANTS: Array<[number, number, VariantClass]> = [
  [77, 90, 'pathogenic'],
  [121, 99, 'vus'],
  [254, 104, 'benign'],
  [278, 95, 'vus'],
  [326, 106, 'benign'],
];
const poleFoot = (x: number) =>
  EXONS.some(([start, width]) => x >= start && x <= start + width) ? GENE_Y - 5 : GENE_Y;
// A flag waving from the top of its pole, 13 wide and 8 high.
const flagPath = (x: number, top: number) =>
  `M${x},${top} c4,-2 8,2 13,0 v8 c-5,2 -9,-2 -13,0 Z`;

/**
 * A genome-browser motif: chromosome 8's real G-banded ideogram with band 8q24.21 selected,
 * and below it that region as CoGA shows it: a coverage track with a deletion, variants
 * flagged by class, and a gene with its exons. Drawn with classes only (no inline styles),
 * and hidden from assistive technology: it says nothing the text beside it does not.
 */
const GenomeMotif: React.FC = () => {
  const regionStart = toX(REGION[0]);
  const regionEnd = toX(REGION[1]);
  return (
    <svg
      className="auth-motif"
      viewBox="0 0 360 150"
      aria-hidden="true"
      focusable="false"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <clipPath id="auth-motif-chromosome">
          <path d={chromosomeOutline} />
        </clipPath>
        <clipPath id="auth-motif-deletion">
          <rect x={DELETION[0]} y="38" width={DELETION[1] - DELETION[0]} height={COVERAGE_BASE - 38} />
        </clipPath>
      </defs>

      <text className="auth-motif-label" x={LEFT} y="9">
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
        x={round(regionStart - 1)}
        y={IDEOGRAM_TOP - 2}
        width={round(regionEnd - regionStart + 2)}
        height={IDEOGRAM_HEIGHT + 4}
        rx="1.5"
      />
      <text className="auth-motif-label auth-motif-label--region" x={round((regionStart + regionEnd) / 2)} y="9">
        8q24.21
      </text>

      <path
        className="auth-motif-zoom"
        d={`M${round(regionStart - 1)},${IDEOGRAM_TOP + IDEOGRAM_HEIGHT + 2} L${LEFT},38 M${round(regionEnd + 1)},${IDEOGRAM_TOP + IDEOGRAM_HEIGHT + 2} L${RIGHT},38`}
      />
      <rect className="auth-motif-panel" x={LEFT} y="38" width={RIGHT - LEFT} height="108" rx="4" />

      <path className="auth-motif-coverage-area" d={coverageArea} />
      <path className="auth-motif-deletion-area" d={coverageArea} clipPath="url(#auth-motif-deletion)" />
      <path className="auth-motif-coverage" d={coverageLine} />
      <rect
        className="auth-motif-deletion"
        x={DELETION[0]}
        y={COVERAGE_BASE + 3}
        width={DELETION[1] - DELETION[0]}
        height="3"
        rx="1"
      />

      <line className="auth-motif-gene" x1={GENE_START} y1={GENE_Y} x2={GENE_END} y2={GENE_Y} />
      {INTRON_ARROWS.map((x) => (
        <path key={x} className="auth-motif-strand" d={`M${x - 1.5},${GENE_Y - 2.5} L${x + 1.5},${GENE_Y} L${x - 1.5},${GENE_Y + 2.5}`} />
      ))}
      {EXONS.map(([x, width, untranslated]) => {
        const utr = untranslated ? Math.min(6, width / 2) : 0;
        const codingX = untranslated === 'start' ? x + utr : x;
        return (
          <g key={x}>
            {untranslated && (
              <rect
                className="auth-motif-exon"
                x={untranslated === 'start' ? x : x + width - utr}
                y={GENE_Y - 3}
                width={utr}
                height="6"
              />
            )}
            <rect className="auth-motif-exon" x={codingX} y={GENE_Y - 5} width={width - utr} height="10" />
          </g>
        );
      })}

      {VARIANTS.map(([x, top, variantClass]) => (
        <g key={x} className={`auth-motif-flag auth-motif-flag--${variantClass}`}>
          <line className="auth-motif-pole" x1={x} y1={poleFoot(x)} x2={x} y2={top - 1} />
          <path className="auth-motif-pennant" d={flagPath(x, top)} />
          <circle className="auth-motif-pennant" cx={x} cy={poleFoot(x)} r="1.4" />
        </g>
      ))}
    </svg>
  );
};

// The name in sequence-track letters: C, G and A are three of DNA's four bases, each in its
// base colour, as a genome browser shows the reference sequence.
const Wordmark: React.FC = () => (
  <span className="auth-wordmark">
    <span className="sr-only">CoGA</span>
    <span className="auth-wordmark-letters" aria-hidden="true">
      <span className="auth-wordmark-base auth-wordmark-base--c">C</span>
      <span className="auth-wordmark-o">o</span>
      <span className="auth-wordmark-base auth-wordmark-base--g">G</span>
      <span className="auth-wordmark-base auth-wordmark-base--a">A</span>
    </span>
  </span>
);

/** What CoGA is, briefly, beside the sign-in and sign-up forms. Not a landing page. */
const AuthIntro: React.FC = () => (
  <div className="auth-intro">
    <GenomeMotif />
    <p className="auth-intro-title">
      <Wordmark />
      <span className="auth-intro-name">Comprehensive Genomic Analysis</span>
    </p>
    <p className="auth-intro-copy">
      A family-based genome browser for clinical genomics: filter, view and interpret a family&apos;s
      variants together, from the sequencing data to the signed-out analysis.
    </p>
    <ul className="auth-intro-points">
      <li>
        <strong>Built around the family</strong> – a proband with parents and siblings, or a couple
        with embryos or a pregnancy.
      </li>
      <li>
        <strong>Short and long reads</strong> – small variants, SVs, CNVs, repeat expansions,
        haplotypes and mtDNA in one view.
      </li>
      <li>
        <strong>Traceable</strong> – every action is audited, and a signed-out analysis is frozen.
      </li>
    </ul>
    <p className="auth-intro-note">
      PGT · monogenic NIPT · carrier screening · rare-disorder diagnostics · mitochondrial disease
    </p>
  </div>
);

export default AuthIntro;
