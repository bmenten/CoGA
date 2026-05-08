import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '../../test/createTestQueryClient';
import { useFamilyReference } from '../reference';

const apiGetMock = vi.hoisted(() => vi.fn());

vi.mock('../api', () => ({
  default: {
    get: apiGetMock,
  },
}));

const ReferenceProbe = ({
  projectIds,
  preferredProjectId,
}: {
  projectIds?: string[];
  preferredProjectId?: string;
}) => {
  const reference = useFamilyReference(projectIds, preferredProjectId);
  return <output data-testid="reference">{JSON.stringify(reference)}</output>;
};

const readReference = () =>
  JSON.parse(screen.getByTestId('reference').textContent || '{}') as {
    speciesName?: string;
    assemblyName?: string;
    assemblyVersion?: string;
    assemblyId?: string;
    projectId?: string;
    isLoading: boolean;
    hasLinkedProject: boolean;
  };

describe('useFamilyReference', () => {
  beforeEach(() => {
    apiGetMock.mockReset();
  });

  it('only resolves preferred projects that are linked to the family', async () => {
    apiGetMock.mockResolvedValue({
      data: [
        {
          _id: 'p1',
          name: 'Linked project',
          species_name: 'Homo sapiens',
          assembly_name: 'GRCh38',
          assembly_version: 'p14',
          families: [],
          samples: [],
        },
        {
          _id: 'p2',
          name: 'Unlinked project',
          species_name: 'Mus musculus',
          assembly_name: 'GRCm39',
          assembly_version: 'v1',
          families: [],
          samples: [],
        },
      ],
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ReferenceProbe projectIds={['p1']} preferredProjectId="p2" />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(readReference().projectId).toBe('p1'));
    expect(readReference()).toMatchObject({
      speciesName: 'Homo sapiens',
      assemblyName: 'GRCh38',
      assemblyVersion: 'p14',
      projectId: 'p1',
      hasLinkedProject: true,
      isLoading: false,
    });
  });

  it('returns an explicit unlinked state when the family has no linked projects', () => {
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <ReferenceProbe />
      </QueryClientProvider>,
    );

    expect(readReference()).toMatchObject({
      assemblyVersion: '',
      hasLinkedProject: false,
      isLoading: false,
    });
    expect(readReference().projectId).toBeUndefined();
    expect(readReference().assemblyName).toBeUndefined();
    expect(apiGetMock).not.toHaveBeenCalled();
  });
});
