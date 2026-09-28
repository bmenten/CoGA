import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AcmgClassificationModal from '../AcmgClassificationModal';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import type { SmallVariant, SmallVariantReviewSavePayload } from '../smallVariantSearch';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn() },
}));

const mockedGet = api.get as unknown as ReturnType<typeof vi.fn>;

// LOF variant, absent from gnomAD, in a haploinsufficient gene → PVS1 + PM2.
const variant: SmallVariant = {
  _id: 'chr1-100-A-T',
  chr: 'chr1',
  start: 100,
  end: 100,
  type: 'SNV',
  gene: 'GENEX',
  gene_id: 'ENSG0001',
  effect: 'stop_gained',
  lof: 'HC',
  hgvsp: 'p.Arg100Ter',
  genotypes: [],
};

function renderModal(
  onSave: (payload: SmallVariantReviewSavePayload) => Promise<void> = vi.fn(async () => undefined),
  shown: SmallVariant = variant,
) {
  const client = createTestQueryClient();
  render(
    <QueryClientProvider client={client}>
      <AcmgClassificationModal variant={shown} familyId="F1" onClose={vi.fn()} onSave={onSave} />
    </QueryClientProvider>,
  );
  return onSave;
}

describe('AcmgClassificationModal', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedGet.mockImplementation((url: string) => {
      if (url.includes('/hpo')) {
        return Promise.resolve({ data: [] });
      }
      return Promise.resolve({
        data: {
          extra: {
            clingen_dosage_assertions: [
              { haploinsufficiency: 'Sufficient evidence for dosage pathogenicity' },
            ],
            gencc_assertions: [],
            hpo_terms: [],
          },
        },
      });
    });
  });

  it('warns before an unreadable stored classification is overwritten (#514)', async () => {
    renderModal(undefined, {
      ...variant,
      review: { variant_id: variant._id, tags: [], tag_metadata: {}, acmg: null, acmg_unreadable: true },
    });

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /The ACMG classification stored for this variant could not be read.*Saving replaces it/,
    );
  });

  it('shows no unreadable warning for a variant never classified', async () => {
    renderModal();

    await screen.findByRole('checkbox', { name: /PVS1/ });
    expect(screen.queryByText(/could not be read/)).not.toBeInTheDocument();
  });

  it('auto-applies supported criteria and flags contraindicated ones', async () => {
    renderModal();

    await screen.findByText('PVS1');
    // PVS1 (LOF + sufficient haploinsufficiency) and PM2 (absent) auto-apply:
    // 8 + 1 = 9 → Likely Pathogenic.
    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: /PVS1/ })).toBeChecked(),
    );
    expect(screen.getByText('Likely Pathogenic - class 4')).toBeInTheDocument();

    // BA1 is ruled out by the rare/absent frequency — flagged n/a, not checked.
    const ba1 = screen.getByText('BA1').closest('.acmg-criterion') as HTMLElement;
    expect(ba1).toHaveClass('acmg-criterion--na');
    expect(within(ba1).getByLabelText('Not applicable')).toBeInTheDocument();

    // External resource links are rendered in the header.
    expect(screen.getByRole('link', { name: 'ClinVar' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'DECIPHER' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /PubMed/ })).toBeInTheDocument();
  });

  it('recomputes the class when a strength is changed', async () => {
    renderModal();
    const user = userEvent.setup();

    await screen.findByText('PVS1');
    await waitFor(() => expect(screen.getByText('Likely Pathogenic - class 4')).toBeInTheDocument());

    // Bump PM2 supporting (1) → moderate (2): 8 + 2 = 10 → Pathogenic.
    await user.selectOptions(screen.getByLabelText('PM2 strength'), 'moderate');
    expect(screen.getByText('Pathogenic - class 5')).toBeInTheDocument();
  });

  it('emits an ACMG payload and the matching class tag on save', async () => {
    const onSave = renderModal();
    const user = userEvent.setup();

    await screen.findByText('PVS1');
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /PVS1/ })).toBeChecked());
    await user.click(screen.getByRole('button', { name: /save classification/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(onSave).mock.calls[0][0] as SmallVariantReviewSavePayload;
    // PVS1 (8) + PM2 (1) = 9 → Likely Pathogenic (class 4).
    expect(payload.classification).toBe('Likely Pathogenic - class 4');
    expect(payload.tags).toContain('acmg_class_4');
    expect(payload.acmg?.point_total).toBe(9);
    expect(payload.acmg?.criteria.some((c) => c.code === 'PVS1' && c.accepted)).toBe(true);
  });

  it('emits the VUS sub-tier tag for a variant that lands in the VUS band', async () => {
    // Missense, absent from gnomAD → only PM2 (1 pt) auto-applies → VUS cold.
    const vusVariant: SmallVariant = {
      _id: 'chr2-200-C-G',
      chr: 'chr2',
      start: 200,
      end: 200,
      type: 'SNV',
      gene: 'GENEY',
      gene_id: 'ENSG0002',
      effect: 'missense_variant',
      hgvsp: 'p.Ala67Gly',
      genotypes: [],
    };
    const onSave: (payload: SmallVariantReviewSavePayload) => Promise<void> = vi.fn(async () => undefined);
    const client = createTestQueryClient();
    render(
      <QueryClientProvider client={client}>
        <AcmgClassificationModal variant={vusVariant} familyId="F1" onClose={vi.fn()} onSave={onSave} />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();

    await screen.findByText('PM2');
    await waitFor(() => expect(screen.getByText('VUS - class 3')).toBeInTheDocument());
    expect(screen.getByText('Cold VUS')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /save classification/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(onSave).mock.calls[0][0] as SmallVariantReviewSavePayload;
    expect(payload.tags).toContain('acmg_class_3');
    expect(payload.tags).toContain('acmg_vus_cold');
    expect(payload.acmg?.vus_tier).toBe('cold');
  });

  it('preserves in-progress analyst edits when late gene/HPO context resolves (#337)', async () => {
    // Keep the HPO query pending so it resolves AFTER the analyst edits — the late
    // re-seed must NOT overwrite their work.
    let resolveHpo: (value: { data: unknown }) => void = () => {};
    const hpoPromise = new Promise<{ data: unknown }>((resolve) => {
      resolveHpo = resolve;
    });
    mockedGet.mockReset();
    mockedGet.mockImplementation((url: string) => {
      if (url.includes('/hpo')) return hpoPromise;
      return Promise.resolve({
        data: {
          extra: {
            clingen_dosage_assertions: [
              { haploinsufficiency: 'Sufficient evidence for dosage pathogenicity' },
            ],
            gencc_assertions: [],
            hpo_terms: [],
          },
        },
      });
    });

    renderModal();
    const user = userEvent.setup();

    // Gene profile resolved -> PVS1 auto-applies; HPO is still pending.
    const pvs1 = await screen.findByRole('checkbox', { name: /PVS1/ });
    await waitFor(() => expect(pvs1).toBeChecked());
    expect(screen.getByRole('link', { name: /PubMed \(gene\)/ })).toBeInTheDocument();

    // Analyst un-accepts PVS1 before the HPO query resolves.
    await user.click(pvs1);
    expect(pvs1).not.toBeChecked();

    // HPO resolves late (a proband 'present' term), re-running the seeding effect.
    // Before #337 this re-applied the auto-suggestions and re-checked PVS1.
    resolveHpo({
      data: [{ status: 'present', hpo_id: 'HP:0001250', label: 'Seizure', sample_id: 'S1' }],
    });

    // The PubMed link gains HPO scope, proving the seeding effect re-ran...
    await screen.findByRole('link', { name: /PubMed \(gene \+ HPO\)/ });
    // ...and the analyst's edit survived it.
    expect(screen.getByRole('checkbox', { name: /PVS1/ })).not.toBeChecked();
  });
});
