import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '../../test/createTestQueryClient';
import {
  assemblyDisplayRank,
  compareAssemblyNames,
  formatResolvedReferenceLabel,
  grcBuild,
  humanAssemblyOf,
  isT2tChm13,
  mapAssemblyToIgvGenome,
  useFamilyReference,
} from '../reference';

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
    assemblyValidated?: boolean;
    projectId?: string;
    isLoading: boolean;
    isError: boolean;
    hasLinkedProject: boolean;
  };

describe('useFamilyReference', () => {
  beforeEach(() => {
    apiGetMock.mockReset();
  });

  // #608 — a failed catalogue leaves the reference unknown, and says so: it is not "not
  // linked", and not a validation scope that was checked.
  it('reports a failed project catalogue, and loads it again on retry', async () => {
    apiGetMock.mockRejectedValueOnce(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));
    apiGetMock.mockResolvedValue({
      data: [
        {
          _id: 'p1',
          name: 'T2T project',
          species_name: 'Homo sapiens',
          assembly_name: 'T2T-CHM13v2.0',
          assembly_validated: false,
          families: [],
          samples: [],
        },
      ],
    });
    const RetryProbe = () => {
      const reference = useFamilyReference(['p1']);
      return (
        <>
          <output data-testid="reference">{JSON.stringify(reference)}</output>
          <button type="button" onClick={reference.retry}>
            Retry
          </button>
        </>
      );
    };

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <RetryProbe />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(readReference().isError).toBe(true));
    expect(readReference()).toMatchObject({ hasLinkedProject: true, isLoading: false });
    expect(readReference().assemblyValidated).toBeUndefined();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(readReference().assemblyValidated).toBe(false));
    expect(readReference().isError).toBe(false);
  });

  it('names a reference that could not be loaded as such, not as unlinked', () => {
    expect(formatResolvedReferenceLabel({ isError: true }, 'Not linked')).toBe('Reference could not be loaded');
    expect(formatResolvedReferenceLabel({ assemblyName: 'GRCh38', isError: false })).toBe('GRCh38');
    expect(formatResolvedReferenceLabel({}, 'Not linked')).toBe('Not linked');
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

  it('carries whether the linked assembly is inside the validated scope (#515)', async () => {
    apiGetMock.mockResolvedValue({
      data: [
        {
          _id: 'p1',
          name: 'T2T project',
          species_name: 'Homo sapiens',
          assembly_name: 'T2T-CHM13v2.0',
          assembly_validated: false,
          families: [],
          samples: [],
        },
        {
          _id: 'p2',
          name: 'GRCh38 project',
          species_name: 'Homo sapiens',
          assembly_name: 'GRCh38',
          assembly_validated: true,
          families: [],
          samples: [],
        },
      ],
    });

    const queryClient = createTestQueryClient();
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <ReferenceProbe projectIds={['p1']} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(readReference().projectId).toBe('p1'));
    expect(readReference().assemblyValidated).toBe(false);

    rerender(
      <QueryClientProvider client={queryClient}>
        <ReferenceProbe projectIds={['p2']} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(readReference().projectId).toBe('p2'));
    expect(readReference().assemblyValidated).toBe(true);
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
      isError: false,
    });
    expect(readReference().projectId).toBeUndefined();
    expect(readReference().assemblyName).toBeUndefined();
    // Unknown, not "off scope": no project means nothing to judge yet.
    expect(readReference().assemblyValidated).toBeUndefined();
    expect(apiGetMock).not.toHaveBeenCalled();
  });
});

// The UCSC reference import names T2T-CHM13v2.0 as UCSC's description does, `T2T CHM13v2.0`
// with a space; NCBI writes `T2T-CHM13v2.0`, its ClickHouse tables `T2T_CHM13v2.0`, and UCSC
// calls it `hs1`. Only the hyphen was recognised, and IGV opened hg38 for any other name: a
// T2T family's reads and variants were drawn on another reference.
const T2T_NAMES = ['T2T CHM13v2.0', 'T2T-CHM13v2.0', 'T2T_CHM13v2.0', 'T2T-CHM13', 'CHM13v2.0', 'hs1', 'HS1'];
const UNKNOWN_NAMES = ['T2T-YAO', 'T2T-CHM13v1.0', 'GRCz11', 'GRCh38_no_alt', 'hs2'];

describe('isT2tChm13', () => {
  it('reads T2T-CHM13 under each of its names, as the backend does', () => {
    expect(T2T_NAMES.map((name) => isT2tChm13(name))).toEqual(T2T_NAMES.map(() => true));
    expect(isT2tChm13(' T2T CHM13v2.0 ')).toBe(true);
  });

  it('reads no other assembly, nor another CHM13 release, as T2T-CHM13', () => {
    const others = ['GRCh38', 'hg38', 'GRCh37', 'hg19', ...UNKNOWN_NAMES, 'T2T', ''];
    expect(others.map((name) => isT2tChm13(name))).toEqual(others.map(() => false));
    expect(isT2tChm13(undefined)).toBe(false);
    expect(isT2tChm13(null)).toBe(false);
  });
});

describe('mapAssemblyToIgvGenome', () => {
  it('opens hs1 for T2T-CHM13 under each of its names', () => {
    expect(T2T_NAMES.map((name) => mapAssemblyToIgvGenome(name))).toEqual(T2T_NAMES.map(() => 'hs1'));
  });

  it('opens the genomes of GRCh38, GRCh37 and the other known assemblies as before', () => {
    const grch38 = ['GRCh38', 'hg38', 'GRCh38.p14', 'grch38'];
    expect(grch38.map((name) => mapAssemblyToIgvGenome(name))).toEqual(grch38.map(() => 'hg38'));
    expect(['GRCh37', 'hg19'].map((name) => mapAssemblyToIgvGenome(name))).toEqual(['hg19', 'hg19']);
    expect(['GRCm39', 'GRCm38', 'EquCab3.0'].map((name) => mapAssemblyToIgvGenome(name))).toEqual([
      'mm39',
      'mm10',
      'equCab3',
    ]);
  });

  it('opens the GRCh37 genome for a patched GRCh37 name, which it took for GRCh38', () => {
    expect(mapAssemblyToIgvGenome('GRCh37.p13')).toBe('hg19');
  });

  it('opens no genome, never hg38, for an assembly it has none for', () => {
    expect(UNKNOWN_NAMES.map((name) => mapAssemblyToIgvGenome(name))).toEqual(UNKNOWN_NAMES.map(() => null));
    expect(mapAssemblyToIgvGenome('')).toBeNull();
    expect(mapAssemblyToIgvGenome(undefined)).toBeNull();
  });
});

// GRCh38 and GRCh37 under their GRC and UCSC names, in any case and with or without a patch
// suffix, as the backend reads them (`sex_chromosomes.grc_build`). The variant list's UCSC and
// gnomAD links read them by a part of the name or version, so an assembly whose version merely
// held "hg38" got GRCh38's links; the panel list put only the exact name GRCh38 first.
const GRCH38_NAMES = ['GRCh38', 'hg38', 'GRCh38.p14', 'grch38', 'HG38', ' GRCh38 '];
const GRCH37_NAMES = ['GRCh37', 'hg19', 'GRCh37.p13', 'HG19'];

describe('grcBuild', () => {
  it('reads GRCh38 and GRCh37 under each of their names, as the backend does', () => {
    expect(GRCH38_NAMES.map((name) => grcBuild(name))).toEqual(GRCH38_NAMES.map(() => 'GRCh38'));
    expect(GRCH37_NAMES.map((name) => grcBuild(name))).toEqual(GRCH37_NAMES.map(() => 'GRCh37'));
  });

  it('reads no build from another assembly, nor from a name that merely holds one', () => {
    const others = [...T2T_NAMES, ...UNKNOWN_NAMES, 'GRCh37-lite', 'hg38-liftover', 'hg18', 'GRCm38', ''];
    expect(others.map((name) => grcBuild(name))).toEqual(others.map(() => null));
    expect(grcBuild(undefined)).toBeNull();
    expect(grcBuild(null)).toBeNull();
  });
});

describe('humanAssemblyOf', () => {
  it('reads the assembly by its name, else by its version', () => {
    expect(humanAssemblyOf('hg38', 'hg38')).toBe('GRCh38');
    expect(humanAssemblyOf('GRCh37', 'p13')).toBe('GRCh37');
    expect(humanAssemblyOf('T2T CHM13v2.0', 'hs1')).toBe('T2T-CHM13');
    expect(humanAssemblyOf('Homo_sapiens_assembly38', 'hg38')).toBe('GRCh38');
    expect(humanAssemblyOf(undefined, 'hs1')).toBe('T2T-CHM13');
  });

  it('lets the name decide over the version, and reads no part of either', () => {
    expect(humanAssemblyOf('GRCh37', 'hg38')).toBe('GRCh37');
    expect(humanAssemblyOf('T2T CHM13v2.0', 'GRCh38')).toBe('T2T-CHM13');
    expect(humanAssemblyOf('CHM13v1.1', 'hg38-liftover')).toBeNull();
    expect(humanAssemblyOf('GRCh38_no_alt', '')).toBeNull();
    expect(humanAssemblyOf(undefined, undefined)).toBeNull();
  });
});

describe('assemblyDisplayRank and compareAssemblyNames', () => {
  it('rank GRCh38, then T2T-CHM13, then GRCh37 under any of their names, then the rest, as the backend does', () => {
    expect(GRCH38_NAMES.map((name) => assemblyDisplayRank(name))).toEqual(GRCH38_NAMES.map(() => 0));
    expect(T2T_NAMES.map((name) => assemblyDisplayRank(name))).toEqual(T2T_NAMES.map(() => 1));
    expect(GRCH37_NAMES.map((name) => assemblyDisplayRank(name))).toEqual(GRCH37_NAMES.map(() => 2));
    const others = [...UNKNOWN_NAMES, 'CHM13v1.1', 'GRCm39', ''];
    expect(others.map((name) => assemblyDisplayRank(name))).toEqual(others.map(() => 9));
  });

  it('sort the rest by name, as the backend does', () => {
    const names = ['GRCm39', 'hg19', 'CHM13v1.1', 'T2T CHM13v2.0', 'equCab3', 'hg38'];
    // The backend sorts ties by the name's characters: upper case before lower case.
    expect([...names].sort(compareAssemblyNames)).toEqual([
      'hg38',
      'T2T CHM13v2.0',
      'hg19',
      'CHM13v1.1',
      'GRCm39',
      'equCab3',
    ]);
  });
});
