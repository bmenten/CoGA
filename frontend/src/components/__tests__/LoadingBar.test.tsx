import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import LoadingBar from '../LoadingBar';
import PageState from '../PageState';

describe('LoadingBar', () => {
  it('renders an accessible status with the animated flyer', () => {
    const { container } = render(<LoadingBar label="Loading variants" />);
    expect(screen.getByRole('status', { name: 'Loading variants' })).toBeInTheDocument();
    expect(container.querySelector('.loading-bar-flyer')).toBeTruthy();
  });
});

describe('PageState loading mode', () => {
  it('shows the animated bar and spinner when loading', () => {
    const { container } = render(<PageState loading title="Loading small variants" />);
    expect(container.querySelector('.loading-bar')).toBeTruthy();
    expect(container.querySelector('.page-state-spinner')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Loading small variants' })).toBeInTheDocument();
  });

  it('omits the loader when not loading', () => {
    const { container } = render(<PageState title="Ready" />);
    expect(container.querySelector('.loading-bar')).toBeNull();
    expect(container.querySelector('.page-state-spinner')).toBeNull();
  });
});
