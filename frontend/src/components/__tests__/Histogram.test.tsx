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
