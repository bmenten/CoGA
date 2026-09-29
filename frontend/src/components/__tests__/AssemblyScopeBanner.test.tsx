// "Not validated for clinical use" banner — TF-06 H12 / REQ-TRACE-009 (#515).

import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import AssemblyScopeBanner from '../AssemblyScopeBanner';

describe('AssemblyScopeBanner', () => {
  it('labels a family on an assembly outside the validated scope', () => {
    render(<AssemblyScopeBanner assemblyName="T2T-CHM13v2.0" assemblyValidated={false} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(/Not validated for clinical use/);
    expect(banner).toHaveTextContent(/T2T-CHM13v2\.0 is outside the reference assemblies/);
    expect(banner).toHaveTextContent(/research use only.*cannot be signed out/);
  });

  it('says so when no assembly is linked', () => {
    render(<AssemblyScopeBanner assemblyValidated={false} />);

    expect(screen.getByRole('alert')).toHaveTextContent(/No reference assembly is linked/);
  });

  it('shows nothing for a validated assembly, or while the scope is unknown', () => {
    const { container, rerender } = render(
      <AssemblyScopeBanner assemblyName="GRCh38" assemblyValidated />,
    );
    expect(container).toBeEmptyDOMElement();

    // Still loading the project: no flash of the warning.
    rerender(<AssemblyScopeBanner assemblyName="GRCh38" />);
    expect(container).toBeEmptyDOMElement();
  });

  // #608 — when the reference could not be loaded the scope is unknown for good, not
  // "still loading": the banner says it is unconfirmed, with a retry.
  it('says the scope is unconfirmed when the reference could not be loaded', () => {
    const onRetry = vi.fn();
    render(<AssemblyScopeBanner unavailable onRetry={onRetry} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(/Validated scope not confirmed/);
    expect(banner).toHaveTextContent(/not known whether it is inside the assemblies CoGA is validated on/);
    fireEvent.click(within(banner).getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
