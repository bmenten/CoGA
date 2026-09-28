// "Not validated for clinical use" banner — TF-06 H12 / REQ-TRACE-009 (#515).

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

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
});
