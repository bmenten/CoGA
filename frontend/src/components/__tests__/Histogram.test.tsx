import { render, screen } from '@testing-library/react';
import Histogram, { labelledBins } from '../visualizations/Histogram';

test('handles large datasets without stack overflow', () => {
  const data = Array.from({ length: 70000 }, (_, i) => i);
  const { container } = render(<Histogram data={data} />);
  // If rendering fails, this test will throw before reaching these assertions; every value is
  // counted, and drawn.
  expect(screen.getByRole('img', { name: /^Histogram: 70,000 values in \d+ bins;/ })).toBeInTheDocument();
  expect(container.querySelectorAll('rect').length).toBeGreaterThan(0);
});

test('is a named image that says what it counts and where the peak is (#529)', () => {
  render(
    <Histogram
      data={[5, 50, 60, 70, 500]}
      binEdges={[0, 10, 100, 1000]}
      binLabels={['0-10', '10-100', '100-1k']}
      subject="SV lengths"
    />,
  );

  expect(
    screen.getByRole('img', {
      name: 'Histogram of SV lengths: 5 values in 3 bins; tallest bin 10-100 with 3',
    }),
  ).toBeInTheDocument();
});

test('an empty histogram says so in text, not as an unlabelled graphic (#529)', () => {
  render(<Histogram data={[]} subject="SV lengths" />);

  expect(screen.getByText('No data available for this view.')).toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

// #602 — each bin keeps its own bar and its own label.
// The x axis is the one translated to the bottom of the plot; the y axis has ticks too.
const xTicks = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('g[transform^="translate(0,"] .tick text')).map(
    (tick) => tick.textContent,
  );

test('equal-width bins narrower than 1 are labelled apart, one bar each', () => {
  // 20 bins over a range of 2 are 0.1 wide: rounded to whole numbers every label repeated,
  // and d3 folded the repeats into one band, drawing the bars on top of each other.
  const { container } = render(<Histogram data={[0, 0.5, 1, 1.5, 2]} bins={20} logScale={false} />);

  const bars = Array.from(container.querySelectorAll('rect')).map((rect) => rect.getAttribute('x'));
  expect(bars).toHaveLength(20);
  expect(new Set(bars).size).toBe(20);
  const ticks = xTicks(container);
  expect(ticks.slice(0, 4)).toEqual(['0.0', '0.1', '0.2', '0.3']);
  expect(new Set(ticks).size).toBe(20);
});

test('whole-number bins keep their whole-number labels', () => {
  const { container } = render(<Histogram data={[0, 100]} bins={4} logScale={false} />);

  const ticks = xTicks(container);
  expect(ticks).toEqual(['0', '25', '50', '75']);
});

test('bins given the same label are still drawn side by side', () => {
  const { container } = render(
    <Histogram data={[1, 5, 50]} binEdges={[0, 2, 10, 100]} binLabels={['small', 'small', 'large']} />,
  );

  const bars = Array.from(container.querySelectorAll('rect')).map((rect) => rect.getAttribute('x'));
  expect(bars).toHaveLength(3);
  expect(new Set(bars).size).toBe(3);
});

// The variant summary's call-quality histograms come binned by the server (PROD-1).
test('draws counts the server binned as they are, one bar per label, named by their sum', () => {
  const { container } = render(
    <Histogram data={[]} counts={[3, 0, 7]} binLabels={['0', '10', '20+']} subject="GQ" logScale={false} />,
  );

  expect(container.querySelectorAll('rect')).toHaveLength(3);
  expect(xTicks(container)).toEqual(['0', '10', '20+']);
  expect(screen.getByRole('img')).toHaveAccessibleName(
    'Histogram of GQ: 10 values in 3 bins; tallest bin 20+ with 7',
  );
});

test('server-binned counts that are all zero read as no data', () => {
  render(<Histogram data={[]} counts={[0, 0]} binLabels={['0', '10']} />);
  expect(screen.getByText('No data available for this view.')).toBeInTheDocument();
});

// The allele balance's 20 bins of "0.05" ran their labels into each other.
test('labels every other bin when a label is wider than a bin, keeping the last', () => {
  const labels = Array.from({ length: 20 }, (_, i) => (i * 0.05).toFixed(2)).map((label, i) =>
    i === 19 ? `${label}+` : label,
  );
  const { container } = render(
    <Histogram data={[]} counts={labels.map(() => 1)} binLabels={labels} logScale={false} />,
  );

  // Every bin keeps its bar; only the labels are thinned.
  expect(container.querySelectorAll('rect')).toHaveLength(20);
  expect(xTicks(container)).toEqual([
    '0.00', '0.10', '0.20', '0.30', '0.40', '0.50', '0.60', '0.70', '0.80', '0.95+',
  ]);
});

test('labels every bin whose label fits', () => {
  // The SV length bins: "10k-100k" is wide, but its neighbours leave it room.
  expect(
    labelledBins(['0', '1-10', '10-100', '100-1k', '1k-10k', '10k-100k', '100k-1M', '>1M'], 350 / 8.1),
  ).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
  // The 42 depth bins in 350 pixels: every third, and the open-ended last one.
  const depth = [
    ...Array.from({ length: 30 }, (_, i) => String(i * 2)),
    ...Array.from({ length: 8 }, (_, i) => String(60 + i * 5)),
    '100',
    '125',
    '150',
    '200+',
  ];
  expect(labelledBins(depth, 350 / 42.1)).toEqual([0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 41]);
  expect(labelledBins([], 10)).toEqual([]);
  expect(labelledBins(['only'], 10)).toEqual([0]);
});
