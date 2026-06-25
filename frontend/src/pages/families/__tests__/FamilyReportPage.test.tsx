import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import FamilyReportPage from '../FamilyReportPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({
  default: apiMock,
}));

vi.mock('../../../lib/reference', () => ({
  useFamilyReference: () => ({
    speciesName: 'Homo sapiens',
    assemblyName: 'GRCh38',
    assemblyVersion: 'p14',
    projectId: undefined,
    isLoading: false,
  }),
  formatResolvedReferenceLabel: ({ assemblyName }: { assemblyName?: string }) =>
    assemblyName || 'Not linked',
}));

const reportVariant = {
  _id: 'v1',
  chr: 'chr17',
  start: 43000000,
  end: 43000000,
  type: 'snv',
  ref: 'A',
  alt: 'G',
  gene: 'BRCA1',
  effect: 'missense_variant',
  hgvsc: 'c.123A>G',
  hgvsp: 'p.Lys41Arg',
  clinvar: 'Pathogenic',
  gnomad_af: 0,
  cadd_phred: 28.1,
  genotypes: [
    { sample: 'PROBAND', gt: '0/1' },
    { sample: 'FATHER', gt: '0/0' },
  ],
  review: {
    variant_id: 'v1',
    tags: ['report', 'acmg_class_4'],
    tag_metadata: {},
    note: 'Strong candidate for the reported phenotype.',
    acmg: {
      criteria: [
        { code: 'PM2', strength: 'moderate', accepted: true, auto_suggested: true },
        { code: 'PP3', strength: 'supporting', accepted: true, auto_suggested: true },
      ],
      point_total: 3,
      classification: 'Likely Pathogenic - class 4',
    },
  },
};

const mockApi = () => {
  apiMock.get.mockImplementation((url: string) => {
    if (url === '/families/F1') {
      return Promise.resolve({
        data: {
          family_id: 'F1',
          members: [
            { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
            { sample_id: 'FATHER', role: 'father', affected: false, sex: 'male' },
          ],
          projects: [],
        },
      });
    }
    if (url.startsWith('/families/F1/small-variants')) {
      return Promise.resolve({ data: { variants: [reportVariant], total: 1 } });
    }
    if (url === '/genes/profile') {
      return Promise.resolve({
        data: {
          symbol: 'BRCA1',
          display_name: 'BRCA1 DNA repair associated',
          summary: 'BRCA1 is a tumour suppressor involved in DNA double-strand break repair.',
          panels: [{ panel_id: 'p1', name: 'Hereditary cancer' }],
          extra: {
            hpo_terms: [{ hpo_id: 'HP:0003002', label: 'Breast carcinoma' }],
            gencc_assertions: [
              { disease_title: 'Breast-ovarian cancer, familial', moi_title: 'Autosomal dominant' },
            ],
          },
        },
      });
    }
    if (url === '/families/F1/hpo') {
      return Promise.resolve({
        data: [
          { sample_id: 'PROBAND', hpo_id: 'HP:0003002', label: 'Breast carcinoma', status: 'present' },
        ],
      });
    }
    if (url === '/families/F1/annotation-manifest') {
      return Promise.resolve({
        data: {
          family_id: 'F1',
          assembly: 'GRCh38',
          source: 'manual',
          recorded_at: null,
          recorded_by: null,
          modules: [
            { key: 'assembly', label: 'Reference assembly', version: 'GRCh38', detail: '2013-12-01', layer: 'reference' },
            { key: 'clinvar', label: 'ClinVar', version: '2026-05', detail: null, layer: 'pipeline' },
          ],
        },
      });
    }
    return Promise.resolve({ data: {} });
  });
};

const renderPage = () =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={['/families/F1/report']}>
        <Routes>
          <Route path="/families/:familyId/report" element={<FamilyReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('FamilyReportPage', () => {
  it('drafts a report for each reported variant with description, criteria, gene and HPO', async () => {
    mockApi();
    renderPage();

    expect(await screen.findByRole('heading', { name: /BRCA1 c\.123A>G/ })).toBeInTheDocument();

    // Variant description prose mentions zygosity, consequence and absence from gnomAD.
    expect(screen.getAllByText(/heterozygous/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/absent from gnomAD/).length).toBeGreaterThan(0);

    // ACMG motivation writes out the accepted criteria.
    expect(screen.getByText('PM2')).toBeInTheDocument();
    expect(screen.getByText('PP3')).toBeInTheDocument();
    expect(screen.getAllByText(/Likely Pathogenic - class 4/).length).toBeGreaterThan(0);

    // Gene description and HPO overlap.
    expect(screen.getByText(/tumour suppressor/)).toBeInTheDocument();
    expect(screen.getByText(/phenotype overlaps/)).toBeInTheDocument();
    expect(screen.getByText(/Breast carcinoma \(HP:0003002\)/)).toBeInTheDocument();

    // Analyst note surfaced.
    expect(screen.getByText(/Strong candidate for the reported phenotype\./)).toBeInTheDocument();

    // Provenance footer: a generation timestamp + the annotation/reference versions.
    expect(await screen.findByText(/Report generated .* UTC/)).toBeInTheDocument();
    expect(screen.getByText(/ClinVar 2026-05/)).toBeInTheDocument();
    expect(screen.getByText(/Reference assembly GRCh38 \(2013-12-01\)/)).toBeInTheDocument();
  });

  it('shows an empty state when no variants are tagged for reporting', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    expect(await screen.findByText(/No variants are currently tagged for reporting/)).toBeInTheDocument();
  });

  it('warns when a classification’s evidence has drifted since it was made', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url === '/families/F1/classification-drift') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            checked: 2,
            drifted_count: 1,
            drifted: [
              {
                variant_id: '1-100-A-G',
                acmg_class: 'acmg_class_4',
                classified_by: 'alice',
                classified_at: null,
                status: 'drifted',
                annotation_version_from: 'v1',
                annotation_version_to: 'v2',
                clinvar_from: 'Uncertain significance',
                clinvar_to: 'Pathogenic',
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    const alert = await screen.findByRole('alert');
    expect(alert).toBeInTheDocument();
    expect(screen.getByText('1-100-A-G')).toBeInTheDocument();
    expect(
      screen.getByText(/ClinVar Uncertain significance → Pathogenic/),
    ).toBeInTheDocument();
    expect(screen.getByText(/classified by alice/)).toBeInTheDocument();
  });

  it('renders the immutable clinical audit trail', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url === '/families/F1/clinical-audit') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            events: [
              {
                id: 'e1',
                created_at: '2026-06-25T09:04:00Z',
                variant_id: '1-100-A-G',
                actor: 'alice',
                action: 'classification',
                summary: 'Classification unclassified → VUS (class 3)',
                before: null,
                after: null,
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    expect(await screen.findByText('Classification audit trail')).toBeInTheDocument();
    expect(
      screen.getByText(/Classification unclassified → VUS \(class 3\)/),
    ).toBeInTheDocument();
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText(/2026-06-25 09:04 UTC/)).toBeInTheDocument();
  });

  it('shows the frozen sign-out record when the case is signed out', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url === '/families/F1/report/sign-outs') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            latest: {
              version: 2,
              signed_out_by: 'bjorn',
              signed_out_at: '2026-06-25T10:00:00Z',
              content_hash: 'abc123def456',
            },
            signouts: [],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    expect(await screen.findByText(/Signed out — version 2 by/)).toBeInTheDocument();
    expect(screen.getByText(/abc123def456/)).toBeInTheDocument();
    // Once signed out, the action becomes an amendment.
    expect(screen.getByRole('button', { name: /Amend sign-out/ })).toBeInTheDocument();
  });
});
