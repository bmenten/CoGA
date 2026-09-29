import { render, screen, fireEvent } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import GenomeOverviewWorkspace from '../GenomeOverviewWorkspace';

vi.mock('../../../components/visualizations/CoverageSegmentsChart', () => ({
  default: () => <div data-testid="coverage-chart" />,
}));

vi.mock('../../../components/visualizations/ApcadChart', () => ({
  default: () => <div data-testid="apcad-chart" />,
}));

vi.mock('../../../components/visualizations/SvTrack', () => ({
  default: () => <div data-testid="sv-track" />,
}));

vi.mock('../../../components/visualizations/GenomeHaplotypeTrack', () => ({
  default: () => <div data-testid="genome-haplotype-track" />,
}));

vi.mock('../../../components/visualizations/GenomeRepeatExpansionTrack', () => ({
  default: () => <div data-testid="genome-repeat-track" />,
}));

vi.mock('../../../components/visualizations/VizLoadingOverlay', () => ({
  default: ({ message }: { message?: string }) => <div>{message || 'Loading'}</div>,
}));

vi.mock('../../../components/visualizations/Ideogram', () => ({
  default: ({ chrom, onRegionSelect }: { chrom: string; onRegionSelect?: (start: number, end: number) => void }) => (
    <button
      type="button"
      onClick={(event) => {
        if (event.shiftKey) {
          onRegionSelect?.(120, 180);
          return;
        }
      }}
    >
      Ideogram {chrom}
    </button>
  ),
}));

vi.mock('../ViewerMemberSection', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../ViewerTrackBlock', () => ({
  default: ({ children, label }: { children: React.ReactNode; label: string }) => (
    <section>
      <h2>{label}</h2>
      {children}
    </section>
  ),
}));

const MEMBER = {
  sample_id: 'PROBAND',
  role: 'proband',
  affected: true,
  sex: 'male',
} as const;

/** A workspace whose selected sample mounts no track: the empty or failed states. */
const renderWithoutTracks = (overrides: Partial<ComponentProps<typeof GenomeOverviewWorkspace>> = {}) =>
  render(
    <MemoryRouter>
      <GenomeOverviewWorkspace
        familyId="F1"
        familyDisplayId="F1"
        speciesName="Homo sapiens"
        assemblyVersion="p14"
        assembly="GRCh38"
        projectId="p1"
        trackAreaRef={{ current: null }}
        backDest="/families/F1/structural-variants"
        visibleRoi={null}
        genomeRoiRange={null}
        navigateToChromosome={vi.fn()}
        familyMembers={[MEMBER]}
        visibleMembers={[MEMBER]}
        membersWithData={[]}
        trackVisibility={{
          coverage: true,
          segments: false,
          apcad: false,
          sv: false,
          haplotypes: false,
          repeatExpansions: false,
        }}
        availability={{}}
        variantFilters={{}}
        sampleFilterMap={{}}
        urlMaps={{
          coverageTrackUrls: () => ({ coverageUrls: [], segmentsUrls: [] }),
          apcad: {},
          apcadPcf: {},
          haplotypes: {},
          sv: {},
        }}
        layout={{ chroms: ['1'], offsets: { '1': 0 }, lengths: { '1': 1000 }, total: 1000 }}
        trackWidth={1200}
        trackHeight={120}
        svTrackHeight={80}
        showViewerLoading={false}
        {...overrides}
      />
    </MemoryRouter>,
  );

describe('GenomeOverviewWorkspace', () => {
  // #607 — which tracks a sample has decides which are mounted: a failed availability
  // request is not a sample without data.
  it('says the track availability failed, not that the samples have no data', () => {
    const retry = vi.fn();
    renderWithoutTracks({ availabilityFailure: { error: new Error('Network Error'), retry } });

    expect(screen.getByText(/Could not load which tracks each sample has — this is not an empty result/)).toBeInTheDocument();
    expect(screen.queryByText('No data for selected samples')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('says a sample without tracks has no data once availability is known', () => {
    renderWithoutTracks();

    expect(screen.getByText('No data for selected samples')).toBeInTheDocument();
  });

  it('keeps whole-chromosome clicks and supports region jumps from chromosome ideograms', () => {
    const navigateToChromosome = vi.fn();

    render(
      <MemoryRouter>
        <GenomeOverviewWorkspace
          familyId="F1"
          familyDisplayId="F1"
          speciesName="Homo sapiens"
          assemblyVersion="p14"
          assembly="GRCh38"
          projectId="p1"
          trackAreaRef={{ current: null }}
          backDest="/families/F1/structural-variants"
          visibleRoi={null}
          genomeRoiRange={null}
          navigateToChromosome={navigateToChromosome}
          familyMembers={[
            {
              sample_id: 'PROBAND',
              role: 'proband',
              affected: true,
              sex: 'male',
            },
          ]}
          visibleMembers={[
            {
              sample_id: 'PROBAND',
              role: 'proband',
              affected: true,
              sex: 'male',
            },
          ]}
          membersWithData={[
            {
              sample_id: 'PROBAND',
              role: 'proband',
              affected: true,
              sex: 'male',
            },
          ]}
          trackVisibility={{
            coverage: true,
            segments: false,
            apcad: false,
            sv: false,
            haplotypes: false,
            repeatExpansions: false,
          }}
          availability={{
            PROBAND: {
              coverage: true,
              coverageSources: ['hificnv'],
              segmentsSources: [],
              segments: false,
              apcadSources: [],
              apcad: false,
              apcadPcf: false,
              haplotypes: false,
              sv: false,
              repeatExpansions: false,
            },
          }}
          variantFilters={{}}
          sampleFilterMap={{}}
          urlMaps={{
            coverageTrackUrls: (_sampleId: string, source: string) => ({
              coverageUrls: [`http://test/coverage?source=${source}`],
              segmentsUrls: [`http://test/segments?source=${source}`],
            }),
            apcad: {},
            apcadPcf: {},
            haplotypes: { PROBAND: ['http://test/haplotype'] },
            sv: { PROBAND: 'http://test/sv' },
          }}
          layout={{
            chroms: ['1'],
            offsets: { '1': 0 },
            lengths: { '1': 1000 },
            total: 1000,
          }}
          trackWidth={1200}
          trackHeight={120}
          svTrackHeight={80}
          showViewerLoading={false}
        />
      </MemoryRouter>,
    );

    // The test id carries the caller: a sample can have three coverage tracks and
    // each selection surface has to be addressable on its own.
    const coverageSurface = screen.getByTestId('genome-region-select-coverage-PROBAND-hificnv');
    Object.defineProperty(coverageSurface, 'getBoundingClientRect', {
      value: () => ({
        left: 0,
        top: 0,
        width: 1200,
        height: 120,
        right: 1200,
        bottom: 120,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }),
    });

    fireEvent.mouseDown(coverageSurface, { clientX: 120 });
    fireEvent.mouseMove(coverageSurface, { clientX: 240 });
    fireEvent.mouseUp(coverageSurface, { clientX: 240 });
    expect(navigateToChromosome).toHaveBeenCalledWith('1', {
      start: 100,
      end: 200,
    });

    fireEvent.click(screen.getByText('Ideogram 1'), { shiftKey: true });
    expect(navigateToChromosome).toHaveBeenCalledWith('1', {
      start: 120,
      end: 180,
    });

    fireEvent.click(screen.getByText('1'));
    expect(navigateToChromosome).toHaveBeenCalledWith('1');

    // The keyboard opens a chromosome too (#529).
    navigateToChromosome.mockClear();
    const chromosome = screen.getByRole('button', { name: 'Open chromosome 1' });
    expect(chromosome).toHaveAttribute('tabindex', '0');
    fireEvent.keyDown(chromosome, { key: 'Enter' });
    fireEvent.keyDown(chromosome, { key: ' ' });
    fireEvent.keyDown(chromosome, { key: 'a' });
    expect(navigateToChromosome.mock.calls).toEqual([['1'], ['1']]);
  });

  it('draws one labelled coverage track per CNV caller', () => {
    render(
      <MemoryRouter>
        <GenomeOverviewWorkspace
          familyId="F1"
          familyDisplayId="F1"
          speciesName="Homo sapiens"
          assemblyVersion="p14"
          assembly="GRCh38"
          projectId="p1"
          trackAreaRef={{ current: null }}
          backDest="/families/F1/structural-variants"
          visibleRoi={null}
          genomeRoiRange={null}
          navigateToChromosome={vi.fn()}
          familyMembers={[MEMBER]}
          visibleMembers={[MEMBER]}
          membersWithData={[MEMBER]}
          inheritanceModel="dominant"
          trackVisibility={{
            coverage: true,
            segments: true,
            apcad: false,
            haplotypes: false,
            sv: false,
            repeatExpansions: false,
          }}
          availability={{
            PROBAND: {
              coverage: true,
              // Deliberately out of display order: the view must not depend on
              // what order the availability query returned.
              coverageSources: ['qdnaseq', 'hificnv', 'wisecondorx'],
              segmentsSources: ['hificnv'],
              segments: true,
              apcadSources: [],
              apcad: false,
              apcadPcf: false,
              haplotypes: false,
              sv: false,
              repeatExpansions: false,
            },
          }}
          variantFilters={{}}
          sampleFilterMap={{}}
          urlMaps={{
            coverageTrackUrls: (_sampleId: string, source: string) => ({
              coverageUrls: [`http://test/coverage?source=${source}`],
              segmentsUrls: [`http://test/segments?source=${source}`],
            }),
            apcad: {},
            apcadPcf: {},
            haplotypes: {},
            sv: {},
          }}
          layout={{
            chroms: ['1'],
            offsets: { '1': 0 },
            lengths: { '1': 1000 },
            total: 1000,
          }}
          trackWidth={1200}
          trackHeight={120}
          svTrackHeight={80}
          showViewerLoading={false}
        />
      </MemoryRouter>,
    );

    // Three independent measurements, three tracks -- merging them would average
    // three answers into one that is none of them.
    const labels = screen
      .getAllByRole('heading')
      .map((node) => node.textContent)
      .filter((text) => text?.startsWith('Coverage'));
    // Display order, not the order the availability query returned: the same caller
    // must sit in the same row for every family member.
    expect(labels).toEqual([
      'Coverage \u00b7 HiFiCNV',
      'Coverage \u00b7 WisecondorX',
      'Coverage \u00b7 QDNAseq',
    ]);
    expect(screen.getAllByTestId('coverage-chart')).toHaveLength(3);
    expect(screen.getByTestId('genome-region-select-coverage-PROBAND-qdnaseq')).toBeInTheDocument();
  });
});
