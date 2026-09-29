import { render, screen } from '@testing-library/react';
import Histogram from '../visualizations/Histogram';

test('handles large datasets without stack overflow', () => {
  const data = Array.from({ length: 70000 }, (_, i) => i);
  const { container } = render(<Histogram data={data} />);
  // If rendering fails, this test will throw before reaching this assertion.
  expect(container.querySelector('svg')).not.toBeNull();
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
