import React from 'react';

// Light, medium and dark bands of a chromosome ideogram, as [x, width, shade]. Decorative.
const BANDS: Array<[number, number, 'light' | 'mid' | 'dark']> = [
  [14, 10, 'mid'],
  [34, 6, 'dark'],
  [48, 12, 'light'],
  [70, 8, 'dark'],
  [86, 5, 'mid'],
  [132, 9, 'dark'],
  [150, 6, 'mid'],
  [166, 12, 'dark'],
  [186, 7, 'light'],
  [202, 10, 'mid'],
  [220, 6, 'dark'],
];

// Variant positions under the ideogram: a lollipop's x and its stalk's top.
const VARIANTS: Array<[number, number]> = [
  [42, 40],
  [76, 46],
  [118, 38],
  [171, 44],
  [207, 41],
];

/**
 * A small genome-browser motif: an ideogram, a coverage trace with a dip, variant lollipops and
 * a gene with its exons. Drawn with classes only (no inline styles), and hidden from assistive
 * technology: it says nothing the text beside it does not.
 */
const GenomeMotif: React.FC = () => (
  <svg
    className="auth-motif"
    viewBox="0 0 240 80"
    aria-hidden="true"
    focusable="false"
    xmlns="http://www.w3.org/2000/svg"
  >
    <defs>
      <clipPath id="auth-motif-ideogram">
        <rect x="4" y="6" width="104" height="10" rx="5" />
        <rect x="112" y="6" width="124" height="10" rx="5" />
      </clipPath>
    </defs>
    <g clipPath="url(#auth-motif-ideogram)">
      <rect className="auth-motif-band auth-motif-band--light" x="4" y="6" width="232" height="10" />
      {BANDS.map(([x, width, shade]) => (
        <rect key={x} className={`auth-motif-band auth-motif-band--${shade}`} x={x} y="6" width={width} height="10" />
      ))}
    </g>
    <rect className="auth-motif-outline" x="4" y="6" width="104" height="10" rx="5" />
    <rect className="auth-motif-outline" x="112" y="6" width="124" height="10" rx="5" />
    <polyline
      className="auth-motif-coverage"
      points="4,30 20,28 36,31 52,29 68,30 84,28 96,30 104,36 112,37 120,36 128,30 144,29 160,31 176,28 192,30 208,29 224,31 236,29"
    />
    {VARIANTS.map(([x, top]) => (
      <g key={x}>
        <line className="auth-motif-stalk" x1={x} y1="62" x2={x} y2={top + 3} />
        <circle className="auth-motif-variant" cx={x} cy={top} r="2.5" />
      </g>
    ))}
    <line className="auth-motif-gene" x1="20" y1="68" x2="220" y2="68" />
    <rect className="auth-motif-exon" x="30" y="64" width="12" height="8" />
    <rect className="auth-motif-exon" x="70" y="64" width="9" height="8" />
    <rect className="auth-motif-exon" x="112" y="64" width="14" height="8" />
    <rect className="auth-motif-exon" x="164" y="64" width="10" height="8" />
    <rect className="auth-motif-exon" x="200" y="64" width="12" height="8" />
  </svg>
);

/** What CoGA is, in three lines, beside the sign-in and sign-up forms. Not a landing page. */
const AuthIntro: React.FC = () => (
  <div className="auth-intro">
    <GenomeMotif />
    <p className="auth-intro-title">
      <span className="auth-intro-brand">CoGA</span> – Comprehensive Genomic Analysis
    </p>
    <p className="auth-intro-copy">
      A unified platform for comprehensive genomic variant analysis and interpretation.
    </p>
    <p className="auth-intro-note">
      Short- and long-read data, from small variants to SVs, CNVs and repeats, in one family view.
    </p>
  </div>
);

export default AuthIntro;
