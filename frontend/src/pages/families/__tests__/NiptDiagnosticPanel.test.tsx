import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import NiptDiagnosticPanel from '../NiptDiagnosticPanel';
import api from '../../../lib/api';
import type { NiptDiagnosticPanelStateOut } from '../../../lib/apiSchema.generated';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import type { GenePanel } from '../smallVariantSearch';

vi.mock('../../../lib/api', () => ({
  default: { put: vi.fn() },
}));

const mockedPut = api.put as unknown as ReturnType<typeof vi.fn>;

const PANELS: GenePanel[] = [
  { _id: 'panel-skeletal', name: 'Skeletal dysplasia', source: 'local' },
  { _id: 'panel-exome', name: 'Clinical exome', source: 'local' },
];

const withPanel: NiptDiagnosticPanelStateOut = {
  analysis: 'monogenic_nipt:ZW1',
  panel: {
    panel_id: 'panel-skeletal',
    name: 'Skeletal dysplasia',
    version: 4,
    source: 'local',
    gene_count: 112,
    external_version: null,
    set_at: '2026-11-25T10:00:00Z',
  },
};

function renderCard(
  state: NiptDiagnosticPanelStateOut | undefined,
  { readOnly = false, listPanelId = '' }: { readOnly?: boolean; listPanelId?: string } = {},
) {
  const onShowPanel = vi.fn();
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <NiptDiagnosticPanel
        familyId="NIPTFAM1"
        analysisParams={{ analysis: 'monogenic_nipt:ZW1' }}
        analysisKey="monogenic_nipt:ZW1"
        state={state}
        loadFailed={false}
        panels={PANELS}
        readOnly={readOnly}
        listPanelId={listPanelId}
        onShowPanel={onShowPanel}
      />
    </QueryClientProvider>,
  );
  return onShowPanel;
}

describe('NiptDiagnosticPanel', () => {
  beforeEach(() => {
    mockedPut.mockReset();
  });

  it('names the panel the analysis is read within, with its version and gene count', () => {
    renderCard(withPanel);
    expect(screen.getByText('Skeletal dysplasia (version 4, 112 genes)')).toBeInTheDocument();
    expect(screen.getByText(/The sign-out freezes this version/)).toBeInTheDocument();
  });

  it('says when none is set: the sign-out then asks for a reason', () => {
    renderCard({ analysis: 'monogenic_nipt:ZW1', panel: null });
    expect(screen.getByText(/No diagnostic panel is set/)).toBeInTheDocument();
    expect(screen.getByText(/the sign-out asks for a reason/)).toBeInTheDocument();
  });

  it('sets a panel for the pregnancy named', async () => {
    mockedPut.mockResolvedValue({ data: withPanel });
    renderCard({ analysis: 'monogenic_nipt:ZW1', panel: null });
    const set = screen.getByRole('button', { name: 'Set as diagnostic panel' });
    expect(set).toBeDisabled();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Diagnostic gene panel' }), 'panel-skeletal');
    await userEvent.click(set);
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith(
        '/families/NIPTFAM1/nipt/diagnostic-panel',
        { panel_id: 'panel-skeletal' },
        { params: { analysis: 'monogenic_nipt:ZW1' } },
      ),
    );
  });

  it('clears it, and offers only the other panels to change to', async () => {
    mockedPut.mockResolvedValue({ data: { analysis: 'monogenic_nipt:ZW1', panel: null } });
    renderCard(withPanel);
    const options = screen.getAllByRole('option').map((option) => option.textContent);
    expect(options).toEqual(['Choose a gene panel…', 'Clinical exome']);
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith(
        '/families/NIPTFAM1/nipt/diagnostic-panel',
        { panel_id: null },
        { params: { analysis: 'monogenic_nipt:ZW1' } },
      ),
    );
  });

  it('says why a change failed', async () => {
    mockedPut.mockRejectedValue(new Error('Panel not found'));
    renderCard({ analysis: 'monogenic_nipt:ZW1', panel: null });
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Diagnostic gene panel' }), 'panel-exome');
    await userEvent.click(screen.getByRole('button', { name: 'Set as diagnostic panel' }));
    expect(await screen.findByText(/The diagnostic panel was not changed/)).toBeInTheDocument();
  });

  it('offers to show its variants when the list applies another panel', async () => {
    const onShowPanel = renderCard(withPanel, { listPanelId: 'panel-exome' });
    await userEvent.click(screen.getByRole('button', { name: 'Show its variants below' }));
    expect(onShowPanel).toHaveBeenCalledWith('panel-skeletal');
  });

  it('gives a reader no controls', () => {
    renderCard(withPanel, { readOnly: true, listPanelId: 'panel-skeletal' });
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
