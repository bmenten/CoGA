// The SV table's column picker (#526): one checkbox per column, named for a person, and a
// click toggles that column.
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import StructuralVariantColumnControls from '../StructuralVariantColumnControls';

test('offers each column by name, checked as shown, and toggles the one clicked', () => {
  const onToggleColumn = vi.fn();
  render(
    <StructuralVariantColumnControls
      visible={{ gene: true, read_support: false, remote_chr: true, control_af: false, cytoband: true }}
      onToggleColumn={onToggleColumn}
    />,
  );

  // Known keys get their label; others are capitalised.
  expect(screen.getByLabelText('Gene')).toBeChecked();
  expect(screen.getByLabelText('Read support')).not.toBeChecked();
  expect(screen.getByLabelText('Remote chr')).toBeChecked();
  expect(screen.getByLabelText('Control AF')).not.toBeChecked();
  expect(screen.getByLabelText('Band')).toBeChecked();

  fireEvent.click(screen.getByLabelText('Read support'));
  expect(onToggleColumn).toHaveBeenCalledWith('read_support');
});
