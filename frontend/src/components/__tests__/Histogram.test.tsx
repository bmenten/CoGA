import { render } from '@testing-library/react';
import Histogram from '../visualizations/Histogram';

test('handles large datasets without stack overflow', () => {
  const data = Array.from({ length: 70000 }, (_, i) => i);
  const { container } = render(<Histogram data={data} />);
  // If rendering fails, this test will throw before reaching this assertion.
  expect(container.querySelector('svg')).not.toBeNull();
});
