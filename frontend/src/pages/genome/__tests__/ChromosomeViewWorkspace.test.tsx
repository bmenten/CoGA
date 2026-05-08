import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, type Mock } from 'vitest';
import ChromosomeViewWorkspace from '../ChromosomeViewWorkspace';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    defaults: {
      baseURL: 'http://test-api',
    },
  },
}));

vi.mock('../../../components/visualizations/CoverageSegmentsChart', () => ({
  default: () => <div data-testid="coverage-chart" />,
}));

vi.mock('../../../components/visualizations/ApcadChart', () => ({
  default: () => <div data-testid="apcad-chart" />,
}));

vi.mock('../../../components/visualizations/Ideogram', () => ({
  default: () => <div data-testid="ideogram" />,
}));

vi.mock('../../../components/visualizations/ZoomedIdeogram', () => ({
  default: () => <div data-testid="zoomed-ideogram" />,
}));

vi.mock('../../../components/visualizations/VariantTrack', () => ({
  default: () => <div data-testid="variant-track" />,
}));

vi.mock('../../../components/visualizations/HaplotypeTrack', () => ({
  default: () => <div data-testid="haplotype-track" />,
}));

vi.mock('../../../components/visualizations/GeneTrack', () => ({
  default: () => <div data-testid="gene-track" />,
}));

vi.mock('../../../components/visualizations/BlacklistTrack', () => ({
  default: () => <div data-testid="blacklist-track" />,
}));

vi.mock('../../../components/visualizations/CnvTrack', () => ({
  default: () => <div data-testid="cnv-track" />,
}));

vi.mock('../../../components/visualizations/SmallVariantTrack', () => ({
  default: () => <div data-testid="small-variant-track" />,
}));

vi.mock('../../../components/visualizations/RepeatExpansionTrack', () => ({
  default: () => <div data-testid="repeat-expansion-track" />,
}));

vi.mock('../../../components/visualizations/VizLoadingOverlay', () => ({
  default: ({ message }: { message?: string }) => <div>{message || 'Loading'}</div>,
}));

vi.mock('../ViewerMemberSection', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../ViewerTrackBlock', () => ({
  default: ({
    children,
    label,
  }: {
    children: React.ReactNode;
    label: string;
  }) => (
    <section>
      <h2>{label}</h2>
      {children}
    </section>
  ),
}));

const renderWorkspace = (onJumpToRegion = vi.fn()) => {
  const queryClient = createTestQueryClient();

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ChromosomeViewWorkspace
          familyId="F1"
          familyDisplayId="F1"
          chrom="1"
          speciesName="Homo sapiens"
          assemblyName="GRCh38"
          assemblyVersion="p14"
          assembly="GRCh38"
          assemblyId="asm1"
          projectId="p1"
          trackAreaRef={{ current: null }}
          region={{ start: 0, end: 1_000_000 }}
          trackWidth={1200}
          backDest="/families/F1/structural-variants"
          genomeViewHref="/families/F1/genome"
          igvHref="/families/F1/igv"
          chromInfoSize={248_956_422}
          visibleRoi={null}
          chromosomeRoiRange={null}
          regionRoiRange={null}
          onChromChange={vi.fn()}
          onRegionStartChange={vi.fn()}
          onRegionEndChange={vi.fn()}
          onResetRange={vi.fn()}
          onPan={vi.fn()}
          onZoom={vi.fn()}
          onRegionSelect={vi.fn()}
          onRoiZoom={vi.fn()}
          onJumpToRegion={onJumpToRegion}
          visibleMembers={[]}
          membersWithData={[]}
          availability={{}}
          trackVisibility={{
            coverage: false,
            apcad: false,
            variants: false,
            smallVariants: false,
            haplotypes: false,
            repeatExpansions: false,
          }}
          variantFilters={{}}
          sampleFilterMap={{}}
          detailWindow={5000}
          binLimit={500}
          segmentLimit={500}
          showViewerLoading={false}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('ChromosomeViewWorkspace', () => {
  it('jumps to a resolved gene window', async () => {
    const onJumpToRegion = vi.fn();
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/genes/search') {
        return Promise.resolve({
          data: [
            {
              symbol: 'BRCA1',
              gene_id: 'gene1',
              chr: '17',
              start: 43044295,
              end: 43125482,
              transcript_count: 1,
              assembly_count: 1,
            },
          ],
        });
      }
      if (url === '/genes/profile') {
        return Promise.resolve({
          data: {
            symbol: 'BRCA1',
            chr: '17',
            start: 10000,
            end: 11000,
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderWorkspace(onJumpToRegion);

    fireEvent.change(screen.getByLabelText('Jump to gene or locus'), {
      target: { value: 'BRCA1' },
    });

    await waitFor(() =>
      expect(api.get as unknown as Mock).toHaveBeenCalledWith('/genes/search', {
        params: { q: 'BRCA1' },
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Go' }));

    await waitFor(() =>
      expect(api.get as unknown as Mock).toHaveBeenCalledWith('/genes/profile', {
        params: {
          symbol: 'BRCA1',
          assembly_id: 'asm1',
          family_id: 'F1',
          project_id: 'p1',
        },
      }),
    );

    await waitFor(() =>
      expect(onJumpToRegion).toHaveBeenCalledWith('17', {
        start: 5000,
        end: 16000,
      }),
    );
  });

  it('parses a direct locus jump without calling the gene profile endpoint', async () => {
    const onJumpToRegion = vi.fn();
    (api.get as unknown as Mock).mockResolvedValue({ data: [] });

    renderWorkspace(onJumpToRegion);

    fireEvent.change(screen.getByLabelText('Jump to gene or locus'), {
      target: { value: 'chr5:10,000-20,000' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Go' }));

    await waitFor(() =>
      expect(onJumpToRegion).toHaveBeenCalledWith('5', {
        start: 10000,
        end: 20000,
      }),
    );

    expect(api.get as unknown as Mock).not.toHaveBeenCalledWith(
      '/genes/profile',
      expect.anything(),
    );
  });
});
