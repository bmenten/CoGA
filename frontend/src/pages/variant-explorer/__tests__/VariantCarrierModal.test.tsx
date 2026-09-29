// The explorer's carrier dialog: which carriers it asks for (genotype, assembly, imputed
// calls), how they are grouped and linked per family, the counts it states, and its states.

import type { ComponentProps } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '../../../test/createTestQueryClient';
import VariantCarrierModal from '../VariantCarrierModal';
import type { GlobalVariantRow, VariantCarriers } from '../types';

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock('../../../lib/api', () => ({
  default: { get: apiGetMock },
}));

const CARRIERS_URL = '/variant-explorer/small-variants/4242/carriers';

const VARIANT: GlobalVariantRow = {
  key: '4242',
  variant_id: '13-32316461-G-A',
  chr: '13',
  pos: 32316461,
  ref: 'G',
  alt: 'A',
  type: 'SNV',
  gene: 'BRCA2',
  gene_symbols: ['BRCA2'],
  impact: 'MODERATE',
  consequence: 'missense_variant',
  effects: ['missense_variant'],
  hgvsc: 'c.7007G>A',
  hgvsp: 'p.Arg2336His',
  clinvar: 'Pathogenic',
  classification: null,
  tags: [],
  total_samples: 3,
  het_samples: 2,
  hom_samples: 1,
  total_families: 2,
};

const CARRIERS: VariantCarriers = {
  key: '4242',
  variant_id: '13-32316461-G-A',
  total_families: 2,
  total_samples: 3,
  het_samples: 2,
  hom_samples: 1,
  truncated: false,
  families: [
    {
      family_id: 'F1',
      family_uuid: 'uuid-f1',
      project_id: 'p1',
      project_name: 'Rare disease',
      carrier_count: 2,
      samples: [
        {
          sample_id: 'S1',
          role: 'proband',
          genotype: '0/1',
          zygosity: 'heterozygous',
          family_id: 'F1',
          family_uuid: 'uuid-f1',
          phenotype_summary: 'affected',
        },
        {
          sample_id: 'S2',
          role: 'mother',
          genotype: '1/1',
          zygosity: 'homozygous',
          family_id: 'F1',
          family_uuid: 'uuid-f1',
          phenotype_summary: null,
        },
      ],
    },
    {
      // A family id that must be encoded as one path segment.
      family_id: 'FAM 2/B',
      family_uuid: 'uuid-f2',
      project_id: 'p2',
      project_name: null,
      carrier_count: 1,
      samples: [
        {
          sample_id: 'S3',
          role: null,
          genotype: '0/1',
          zygosity: 'heterozygous',
          family_id: 'FAM 2/B',
          family_uuid: 'uuid-f2',
        },
      ],
    },
  ],
};

const SINGLE_CARRIER: VariantCarriers = {
  ...CARRIERS,
  total_families: 1,
  total_samples: 1,
  het_samples: 1,
  hom_samples: 0,
  families: [{ ...CARRIERS.families[0], carrier_count: 1, samples: [CARRIERS.families[0].samples[0]] }],
};

const renderModal = (props: Partial<ComponentProps<typeof VariantCarrierModal>> = {}) => {
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <VariantCarrierModal
          variant={VARIANT}
          mode="all"
          assemblyId="asm-38"
          onClose={onClose}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onClose };
};

describe('VariantCarrierModal', () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    apiGetMock.mockResolvedValue({ data: CARRIERS });
  });

  it('lists every carrier by family, each linking to its family page', async () => {
    renderModal();

    const dialog = await screen.findByRole('dialog', { name: 'BRCA2' });
    expect(
      await within(dialog).findByText('3 samples · Het: 2 · Hom: 1 · in 2 families'),
    ).toBeInTheDocument();
    expect(within(dialog).getByText('Carriers')).toBeInTheDocument();
    expect(within(dialog).getByText('13:32316461 G>A · c.7007G>A')).toBeInTheDocument();
    // All genotypes of the explorer's assembly; imputed calls are left out by default.
    expect(apiGetMock).toHaveBeenCalledTimes(1);
    expect(apiGetMock).toHaveBeenCalledWith(CARRIERS_URL, { params: { assembly_id: 'asm-38' } });

    const family = within(dialog).getByRole('link', { name: 'F1' });
    expect(family).toHaveAttribute('href', '/families/F1');
    expect(family.parentElement).toHaveTextContent(/^F1 · Rare disease · 2 carriers$/);
    const encodedFamily = within(dialog).getByRole('link', { name: 'FAM 2/B' });
    expect(encodedFamily).toHaveAttribute('href', '/families/FAM%202%2FB');
    expect(encodedFamily.parentElement).toHaveTextContent(/^FAM 2\/B · 1 carrier$/);

    // Each sample states its zygosity, and its role and phenotype where known.
    const proband = within(dialog).getByRole('link', { name: 'S1' });
    expect(proband).toHaveAttribute('href', '/families/F1');
    expect(proband.closest('li')).toHaveTextContent(/^S1 · heterozygous · proband · affected$/);
    expect(within(dialog).getByRole('link', { name: 'S2' }).closest('li')).toHaveTextContent(
      /^S2 · homozygous · mother$/,
    );
    const relative = within(dialog).getByRole('link', { name: 'S3' });
    expect(relative).toHaveAttribute('href', '/families/FAM%202%2FB');
    expect(relative.closest('li')).toHaveTextContent(/^S3 · heterozygous$/);
    expect(within(dialog).queryByText(/more exist than can be listed/)).not.toBeInTheDocument();
  });

  it.each([
    ['het', 'Heterozygous carriers'],
    ['hom', 'Homozygous carriers'],
  ] as const)('asks only for %s carriers when opened from that count', async (mode, title) => {
    renderModal({ mode });

    const dialog = await screen.findByRole('dialog', { name: 'BRCA2' });
    expect(within(dialog).getByText(title)).toBeInTheDocument();
    await waitFor(() =>
      expect(apiGetMock).toHaveBeenCalledWith(CARRIERS_URL, {
        params: { assembly_id: 'asm-38', genotype: mode },
      }),
    );
  });

  it('lists the carrier families without their samples when opened from the family count', async () => {
    renderModal({ mode: 'families' });

    const dialog = await screen.findByRole('dialog', { name: 'BRCA2' });
    expect(await within(dialog).findByText('2 families')).toBeInTheDocument();
    expect(within(dialog).getByText('Families')).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: 'F1' })).toHaveAttribute('href', '/families/F1');
    expect(within(dialog).getByRole('link', { name: 'FAM 2/B' })).toHaveAttribute(
      'href',
      '/families/FAM%202%2FB',
    );
    expect(within(dialog).queryByRole('link', { name: 'S1' })).not.toBeInTheDocument();
    // A family carries the variant in either genotype, so no genotype is requested.
    expect(apiGetMock).toHaveBeenCalledWith(CARRIERS_URL, { params: { assembly_id: 'asm-38' } });
  });

  it('uses singular wording for one carrier in one family', async () => {
    apiGetMock.mockResolvedValue({ data: SINGLE_CARRIER });
    renderModal();

    const dialog = await screen.findByRole('dialog', { name: 'BRCA2' });
    expect(
      await within(dialog).findByText('1 sample · Het: 1 · Hom: 0 · in 1 family'),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: 'F1' }).parentElement).toHaveTextContent(
      /^F1 · Rare disease · 1 carrier$/,
    );
  });

  it('counts a single family in the singular', async () => {
    apiGetMock.mockResolvedValue({ data: SINGLE_CARRIER });
    renderModal({ mode: 'families' });

    expect(await screen.findByText('1 family')).toBeInTheDocument();
  });

  it('includes imputed calls only when the explorer does, and sends no assembly it does not know', async () => {
    renderModal({ includeImputed: true, assemblyId: undefined });

    await waitFor(() =>
      expect(apiGetMock).toHaveBeenCalledWith(CARRIERS_URL, {
        params: { include_imputed: 'true' },
      }),
    );
  });

  it('warns when more carriers exist than the list can show', async () => {
    apiGetMock.mockResolvedValue({ data: { ...CARRIERS, truncated: true } });
    renderModal();

    expect(
      await screen.findByText(/^Showing the first 3 carriers — more exist than can be listed\./),
    ).toBeInTheDocument();
    // The listed carriers are still shown beneath the warning.
    expect(screen.getByRole('link', { name: 'S1' })).toBeInTheDocument();
  });

  it('says no carriers were found in the accessible projects when none come back', async () => {
    apiGetMock.mockResolvedValue({
      data: {
        ...CARRIERS,
        total_families: 0,
        total_samples: 0,
        het_samples: 0,
        hom_samples: 0,
        families: [],
      },
    });
    renderModal();

    expect(
      await screen.findByText('No carriers found in your accessible projects.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Het: \d+/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('shows that carriers are loading, not that there are none', async () => {
    apiGetMock.mockReturnValue(new Promise(() => {}));
    renderModal();

    expect(await screen.findByText('Loading carriers…')).toBeInTheDocument();
    expect(screen.queryByText(/No carriers found/)).not.toBeInTheDocument();
  });

  it('reports a failed request as a failure, never as "no carriers"', async () => {
    apiGetMock.mockRejectedValue(new Error('Request failed with status code 500'));
    renderModal();

    expect(await screen.findByText('Failed to load carriers.')).toBeInTheDocument();
    expect(screen.queryByText(/No carriers found/)).not.toBeInTheDocument();
    expect(screen.queryByText('Loading carriers…')).not.toBeInTheDocument();
  });

  it('closes on Escape and from its Close button', async () => {
    const { onClose } = renderModal();
    await screen.findByRole('dialog', { name: 'BRCA2' });

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('names an intergenic variant as such and leaves out a missing HGVS.c', async () => {
    renderModal({ variant: { ...VARIANT, gene: null, gene_symbols: [], hgvsc: null } });

    const dialog = await screen.findByRole('dialog', { name: 'Intergenic variant' });
    expect(within(dialog).getByText('13:32316461 G>A')).toBeInTheDocument();
  });
});
