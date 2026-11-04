import React, { useId, useState } from 'react';
import { scaleLinear } from 'd3';
import type {
  AncestryReferenceOut,
  AncestrySampleOut,
} from '../../lib/apiSchema.generated';

interface Props {
  reference: AncestryReferenceOut;
  samples: AncestrySampleOut[];
}

// The reference populations in the Okabe-Ito palette, told apart with any colour vision; they
// are data colours of this plot alone, not theme colours.
const COLORS: Record<string, string> = {
  AFR: '#009e73',
  AMR: '#e69f00',
  EAS: '#0072b2',
  EUR: '#cc79a7',
  SAS: '#d55e00',
};
const WIDTH = 1000;
const HEIGHT = 530;
// A family individual's diamond, larger than a reference dot so it stays visible in a cluster.
const FAMILY_RADIUS = 5;
// On the plot itself, so a screenshot or a print of it says it too.
const EXPLORATORY_MARK = 'Exploratory — not for clinical use';
const MARGIN = { left: 76, right: 44, top: 30, bottom: 66 };

const AncestryPcaPlot: React.FC<Props> = ({ reference, samples }) => {
  const id = useId();
  const [xAxis, setXAxis] = useState(0);
  const [yAxis, setYAxis] = useState(1);
  const [hidden, setHidden] = useState<string[]>([]);
  const [focus, setFocus] = useState('');
  const [zoom, setZoom] = useState(1);
  const projected = samples.filter(
    (sample) => sample.status === 'projected' && sample.pcs !== null
  );
  const all = [
    ...reference.points.map((point) => point.pcs),
    ...projected.map((sample) => sample.pcs!),
  ];
  const focused = projected.find((sample) => sample.sample_id === focus);
  const bounds = (axis: number) => {
    const values = all.map((pcs) => pcs[axis]);
    const low = Math.min(...values);
    const high = Math.max(...values);
    const span = high > low ? high - low : 2;
    const center = focused?.pcs?.[axis] ?? (low + high) / 2;
    return [center - (span * 0.59) / zoom, center + (span * 0.59) / zoom];
  };
  const x = scaleLinear()
    .domain(bounds(xAxis))
    .range([MARGIN.left, WIDTH - MARGIN.right]);
  const y = scaleLinear()
    .domain(bounds(yAxis))
    .range([HEIGHT - MARGIN.bottom, MARGIN.top]);
  const label = (axis: number) =>
    `PC${axis + 1} (${(reference.explained_variance[axis] * 100).toFixed(2)}%)`;
  const reset = () => {
    setFocus('');
    setZoom(1);
  };
  const clipId = `${id}-clip`;
  return (
    <div className="ancestry-pca space-y-3">
      <div className="ancestry-controls">
        <label htmlFor={`${id}-x`}>
          Horizontal axis
          <select
            id={`${id}-x`}
            value={xAxis}
            onChange={(e) => setXAxis(Number(e.target.value))}
          >
            {reference.explained_variance.map((_, i) => (
              <option key={i} value={i} disabled={i === yAxis}>
                {label(i)}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`${id}-y`}>
          Vertical axis
          <select
            id={`${id}-y`}
            value={yAxis}
            onChange={(e) => setYAxis(Number(e.target.value))}
          >
            {reference.explained_variance.map((_, i) => (
              <option key={i} value={i} disabled={i === xAxis}>
                {label(i)}
              </option>
            ))}
          </select>
        </label>
        {projected.length > 0 && (
          <label htmlFor={`${id}-focus`}>
            Center view
            <select
              id={`${id}-focus`}
              value={focus}
              onChange={(e) => {
                setFocus(e.target.value);
                setZoom(e.target.value ? 3 : 1);
              }}
            >
              <option value="">All reference populations</option>
              {projected.map((sample) => (
                <option key={sample.sample_id} value={sample.sample_id}>
                  {sample.sample_id}
                </option>
              ))}
            </select>
          </label>
        )}
        <label htmlFor={`${id}-zoom`}>
          Zoom {zoom.toFixed(1)}×
          <input
            id={`${id}-zoom`}
            type="range"
            min="1"
            max="12"
            step="0.25"
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
          />
        </label>
        <button type="button" className="button-secondary" onClick={reset}>
          Reset view
        </button>
      </div>
      <fieldset className="ancestry-legend">
        <legend className="sr-only">Reference populations shown</legend>
        {Object.entries(reference.populations).map(([code, name]) => (
          <label key={code}>
            <input
              type="checkbox"
              checked={!hidden.includes(code)}
              onChange={() =>
                setHidden((previous) =>
                  previous.includes(code)
                    ? previous.filter((item) => item !== code)
                    : [...previous, code]
                )
              }
            />
            <span
              className="ancestry-swatch"
              style={{ backgroundColor: COLORS[code] || '#777' }}
            />
            {code} · {name}
          </label>
        ))}
        <span className="ancestry-individual-key">◇ Family individuals</span>
      </fieldset>
      <div className="ancestry-chart-scroll">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-labelledby={`${id}-title ${id}-description`}
          className="ancestry-chart"
        >
          <title id={`${id}-title`}>
            Ancestry PCA ({EXPLORATORY_MARK}): {projected.length} family
            individual(s) and {reference.sample_count.toLocaleString()}{' '}
            reference individuals
          </title>
          <desc id={`${id}-description`}>
            Public reference individuals are colored by population. Family
            individuals are labeled diamonds, each in a dashed ellipse two
            standard errors wide: how far it moves when its markers are drawn
            again. Horizontal {label(xAxis)};
            vertical {label(yAxis)}. Percentages show variance explained, not
            ancestry proportions. Hover a point for its coordinates.
          </desc>
          <defs>
            <clipPath id={clipId}>
              <rect
                x={MARGIN.left}
                y={MARGIN.top}
                width={WIDTH - MARGIN.left - MARGIN.right}
                height={HEIGHT - MARGIN.top - MARGIN.bottom}
              />
            </clipPath>
          </defs>
          {x.ticks(7).map((tick) => (
            <g key={`x${tick}`}>
              <line
                className="ancestry-grid"
                x1={x(tick)}
                x2={x(tick)}
                y1={MARGIN.top}
                y2={HEIGHT - MARGIN.bottom}
              />
              <text
                x={x(tick)}
                y={HEIGHT - MARGIN.bottom + 24}
                textAnchor="middle"
              >
                {tick.toFixed(1)}
              </text>
            </g>
          ))}
          {y.ticks(7).map((tick) => (
            <g key={`y${tick}`}>
              <line
                className="ancestry-grid"
                x1={MARGIN.left}
                x2={WIDTH - MARGIN.right}
                y1={y(tick)}
                y2={y(tick)}
              />
              <text x={MARGIN.left - 12} y={y(tick) + 4} textAnchor="end">
                {tick.toFixed(1)}
              </text>
            </g>
          ))}
          <text
            x={MARGIN.left}
            y={MARGIN.top - 10}
            className="ancestry-exploratory-mark"
          >
            {EXPLORATORY_MARK}
          </text>
          <text x={WIDTH / 2} y={HEIGHT - 15} textAnchor="middle">
            {label(xAxis)}
          </text>
          <text
            transform={`translate(18 ${HEIGHT / 2}) rotate(-90)`}
            textAnchor="middle"
          >
            {label(yAxis)}
          </text>
          <g clipPath={`url(#${clipId})`}>
            {reference.points
              .filter((point) => !hidden.includes(point.population))
              .map((point) => (
                <circle
                  key={point.id}
                  cx={x(point.pcs[xAxis])}
                  cy={y(point.pcs[yAxis])}
                  r="2.7"
                  fill={COLORS[point.population] || '#777'}
                  opacity="0.52"
                  data-population={point.population}
                >
                  <title>
                    {point.id} · {reference.populations[point.population]} (
                    {point.population}); PC{xAxis + 1}:{' '}
                    {point.pcs[xAxis].toFixed(3)}; PC{yAxis + 1}:{' '}
                    {point.pcs[yAxis].toFixed(3)}
                  </title>
                </circle>
              ))}
            {projected.map((sample, i) => {
              const cx = x(sample.pcs![xAxis]);
              const cy = y(sample.pcs![yAxis]);
              const se = sample.pcs_se;
              // Two standard errors on each axis shown, in the plot's own scale.
              const rx = se ? Math.abs(x(sample.pcs![xAxis] + 2 * se[xAxis]) - cx) : 0;
              const ry = se ? Math.abs(y(sample.pcs![yAxis] + 2 * se[yAxis]) - cy) : 0;
              return (
                <g
                  key={sample.sample_id}
                  className="ancestry-family-point"
                  data-sample={sample.sample_id}
                >
                  <title>
                    {sample.sample_id}; PC{xAxis + 1}:{' '}
                    {sample.pcs![xAxis].toFixed(3)}; PC{yAxis + 1}:{' '}
                    {sample.pcs![yAxis].toFixed(3)}
                    {se
                      ? ` (± ${se[xAxis].toFixed(3)}; ± ${se[yAxis].toFixed(3)} SE)`
                      : ''}
                    ; usable markers: {(sample.call_rate * 100).toFixed(1)}%
                  </title>
                  {se ? (
                    <ellipse
                      cx={cx}
                      cy={cy}
                      rx={rx}
                      ry={ry}
                      className="ancestry-uncertainty"
                      data-uncertainty={sample.sample_id}
                    />
                  ) : null}
                  <circle
                    cx={cx}
                    cy={cy}
                    r="9"
                    fill="transparent"
                    stroke="none"
                  />
                  <path
                    d={`M ${cx} ${cy - FAMILY_RADIUS} L ${cx + FAMILY_RADIUS} ${cy} L ${cx} ${cy + FAMILY_RADIUS} L ${cx - FAMILY_RADIUS} ${cy} Z`}
                    className={
                      focus === sample.sample_id ? 'ancestry-point-focused' : ''
                    }
                  />
                  <text
                    x={cx + 8}
                    y={cy - 6 + (i % 3) * 12}
                    className="ancestry-point-label"
                  >
                    {sample.sample_id}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>
      <p className="text-muted">
        {reference.sample_count.toLocaleString()} public reference individuals ·{' '}
        {reference.variant_count.toLocaleString()} SNPs. Axis percentages are
        the share of reference variation each axis explains, not ancestry
        proportions.
      </p>
    </div>
  );
};

export default AncestryPcaPlot;
