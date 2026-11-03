// The embryos the PGT analysis leaves uncalled because their markers at the ROI mix both of a
// parent's homologs (a possible trisomy): the genome views read no risk state from their
// haplotype blocks, with the family page's warning, so the tracks and the family page give the
// same call. Before this test the hook's reading of the analysis ran in no test.
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '../../test/createTestQueryClient';
import { pgtAnalysis, pgtEmbryo } from '../../test/pgtAnalysis';
import { mixedHomologsWarning } from '../embryoSegregation';

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../api', () => ({ default: apiMock }));

import { useMixedHomologEmbryos } from '../useMixedHomologEmbryos';

const renderMixed = (familyId: string, enabled = true) => {
  const client = createTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useMixedHomologEmbryos(familyId, enabled), { wrapper });
};

// Lets a query's answer, or its failure, reach the hook.
const settled = () => act(async () => new Promise((resolve) => setTimeout(resolve, 10)));

const TRI_QC = {
  informative_sites: 200,
  mendel_errors: 0,
  mendel_rate: 0,
  paternal_markers: 120,
  maternal_markers: 80,
  paternal_markers_left: 60,
  paternal_markers_right: 60,
  maternal_markers_left: 40,
  maternal_markers_right: 40,
  paternal_discordance: 0.01,
  maternal_discordance: 0.475,
};

describe('useMixedHomologEmbryos', () => {
  beforeEach(() => {
    apiMock.get.mockReset();
  });

  it("names each embryo whose markers mix a parent's homologs, with the family page's warning, and no other", async () => {
    apiMock.get.mockResolvedValue({
      data: pgtAnalysis([
        pgtEmbryo({ sample: 'EMB1', call: 'affected_or_at_risk' }),
        pgtEmbryo({ sample: 'EMB2', call: 'uninformative', qc: TRI_QC, mixed_homologs: ['maternal'] }),
      ]),
    });

    const { result } = renderMixed('F/1');

    await waitFor(() => expect(result.current.size).toBe(1));
    expect(result.current.get('EMB2')).toBe(
      mixedHomologsWarning([{ side: 'maternal', discordance: 0.475, markers: 80 }], 0.1),
    );
    expect(result.current.has('EMB1')).toBe(false);
    // The family ID is one encoded path segment.
    expect(apiMock.get).toHaveBeenCalledWith('/families/F%2F1/pgt-analysis');
  });

  it('asks for nothing while disabled or without a family, and names no embryo', async () => {
    const disabled = renderMixed('F1', false);
    const noFamily = renderMixed('', true);

    await settled();
    expect(apiMock.get).not.toHaveBeenCalled();
    expect(disabled.result.current.size).toBe(0);
    expect(noFamily.result.current.size).toBe(0);
  });

  it('names no embryo while the analysis fails or holds no embryos', async () => {
    apiMock.get.mockRejectedValueOnce(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));
    const failed = renderMixed('F1');
    await waitFor(() => expect(apiMock.get).toHaveBeenCalledTimes(1));
    await settled();
    expect(failed.result.current.size).toBe(0);

    // A family without an ROI: the analysis carries no embryo list to read.
    apiMock.get.mockResolvedValueOnce({ data: { ...pgtAnalysis([]), embryos: null } });
    const empty = renderMixed('F2');
    await waitFor(() => expect(apiMock.get).toHaveBeenCalledTimes(2));
    await settled();
    expect(empty.result.current.size).toBe(0);
  });
});
