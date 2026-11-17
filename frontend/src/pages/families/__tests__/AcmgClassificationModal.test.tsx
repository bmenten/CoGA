import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AcmgClassificationModal from '../AcmgClassificationModal';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import { tagDefinition } from '../../../test/tagDefinitions';
import { genotype, review, smallVariant } from '../../../test/variants';
import type {
  SmallVariant,
  SmallVariantReviewSavePayload,
  SmallVariantTagDefinition,
} from '../smallVariantSearch';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn() },
}));

const mockedGet = api.get as unknown as ReturnType<typeof vi.fn>;

// LOF variant, very rare in gnomAD, in a haploinsufficient gene → PVS1 + PM2. (Without a gnomAD
// frequency PM2 is only offered: the annotation does not say whether gnomAD was annotated.)
const variant: SmallVariant = smallVariant({
  _id: 'chr1-100-A-T',
  chr: 'chr1',
  start: 100,
  end: 100,
  type: 'SNV',
  gene: 'GENEX',
  gene_id: 'ENSG0001',
  effect: 'stop_gained',
  lof: 'HC',
  gnomad_af: 1e-6,
  hgvsp: 'p.Arg100Ter',
  genotypes: [],
});

function renderModal(
  onSave: (payload: SmallVariantReviewSavePayload) => Promise<void> = vi.fn(async () => undefined),
  shown: SmallVariant = variant,
  tagDefinitions?: SmallVariantTagDefinition[],
) {
  const client = createTestQueryClient();
  render(
    <QueryClientProvider client={client}>
      <AcmgClassificationModal
        variant={shown}
        familyId="F1"
        onClose={vi.fn()}
        onSave={onSave}
        tagDefinitions={tagDefinitions}
      />
    </QueryClientProvider>,
  );
  return onSave;
}

// The Sample QC with no checks and no phenotype score for the gene: parentage is not
// confirmed and the de novo strength is not assessed.
const NO_SAMPLE_QC_CHECKS = { relatedness_checks: [], mendelian_checks: [] };
const NO_PHENOTYPE_SCORE = { symbol: 'GENEX', score: null, hpo_count: 0, matched: [] };

describe('AcmgClassificationModal', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedGet.mockImplementation((url: string) => {
      if (url.includes('/hpo')) {
        return Promise.resolve({ data: [] });
      }
      if (url.includes('/qc/sample-integrity')) return Promise.resolve({ data: NO_SAMPLE_QC_CHECKS });
      if (url.includes('/gene-phenotype-score')) return Promise.resolve({ data: NO_PHENOTYPE_SCORE });
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

  it('warns before an unreadable stored classification is overwritten', async () => {
    renderModal(undefined, {
      ...variant,
      review: review({ variant_id: variant._id, acmg: null, acmg_unreadable: true }),
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

  it("says which ALT of a multi-allelic record the pre-evaluation reads", async () => {
    renderModal(undefined, { ...variant, alt: 'T,C', alt_allele: 'T' });
    await screen.findByText('PVS1');
    expect(
      screen.getByText('Multi-allelic record: the pre-evaluation reads the annotation of ALT T.'),
    ).toBeInTheDocument();
  });

  it('auto-applies supported criteria and flags contraindicated ones', async () => {
    renderModal();

    await screen.findByText('PVS1');
    // PVS1 (LOF + sufficient haploinsufficiency) and PM2 (very rare) auto-apply:
    // 8 + 1 = 9 → likely pathogenic.
    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: /PVS1/ })).toBeChecked(),
    );
    expect(screen.getByText('Likely pathogenic - class 4')).toBeInTheDocument();

    // BA1 is ruled out by the rare frequency — flagged n/a, not checked.
    const ba1 = screen.getByText('BA1').closest('.acmg-criterion') as HTMLElement;
    expect(ba1).toHaveClass('acmg-criterion--na');
    expect(within(ba1).getByLabelText('Not applicable')).toBeInTheDocument();

    // External resource links are rendered in the header.
    expect(screen.getByRole('link', { name: 'ClinVar' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'DECIPHER' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /PubMed/ })).toBeInTheDocument();
  });

  // With the gene profile failed, the dialog says the lookup failed, and PVS1 is not
  // assessed, never read as "LOF disease mechanism is unconfirmed", as if the gene had been
  // checked.
  it('says the gene profile failed, and does not read PVS1 as an unconfirmed mechanism', async () => {
    let profileRequests = 0;
    mockedGet.mockImplementation((url: string) => {
      if (url.includes('/hpo')) return Promise.resolve({ data: [] });
      profileRequests += 1;
      return profileRequests === 1
        ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
        : Promise.resolve({
            data: {
              extra: {
                clingen_dosage_assertions: [{ haploinsufficiency: 'Sufficient evidence for dosage pathogenicity' }],
                gencc_assertions: [],
                hpo_terms: [],
              },
            },
          });
    });
    renderModal();

    const failure = await screen.findByText(/Could not load the gene profile of GENEX — this is not an empty result/);
    expect(failure).toHaveTextContent(/PVS1, BS2 and PP4 are not assessed from it: review them by hand/);
    expect(screen.getByRole('checkbox', { name: /PVS1/ })).not.toBeChecked();
    expect(screen.queryByText(/LOF disease mechanism is unconfirmed/)).not.toBeInTheDocument();

    // Loaded on retry, PVS1 applies from the gene's haploinsufficiency.
    await userEvent.click(within(failure).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /PVS1/ })).toBeChecked());
    expect(screen.queryByText(/Could not load the gene profile/)).not.toBeInTheDocument();
  });

  it('says the HPO terms failed, and offers PP4 for review rather than dropping it', async () => {
    mockedGet.mockImplementation((url: string) =>
      url.includes('/hpo')
        ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
        : Promise.resolve({ data: { extra: { clingen_dosage_assertions: [], gencc_assertions: [], hpo_terms: [] } } }),
    );
    renderModal();

    expect(await screen.findByText(/Could not load the family's HPO terms — this is not an empty result/)).toHaveTextContent(
      /PP4 is not assessed from them/,
    );
    // PP4 is suggested for review, not applied.
    const pp4 = (await screen.findByText('PP4')).closest('.acmg-criterion') as HTMLElement;
    await waitFor(() => expect(pp4).toHaveClass('acmg-criterion--suggested'));
    expect(within(pp4).getByRole('checkbox')).not.toBeChecked();
  });

  // PM6 compares the proband with the parents the pedigree links, not with whoever has the
  // role 'father' or 'mother': a PED import gives the grandparents those roles too.
  it('compares the proband with the linked parents for PM6, not with a grandparent', async () => {
    const trioVariant: SmallVariant = smallVariant({
      _id: 'chr3-300-G-A',
      chr: 'chr3',
      start: 300,
      end: 300,
      type: 'SNV',
      gene: 'GENEZ',
      gene_id: 'ENSG0003',
      effect: 'missense_variant',
      hgvsp: 'p.Gly10Asp',
      genotypes: [
        genotype({ sample: 'GF', gt: '0/0', dp: 30 }),
        genotype({ sample: 'GM', gt: '0/0', dp: 30 }),
        genotype({ sample: 'P', gt: '0/1', dp: 30 }),
        genotype({ sample: 'F', gt: '0/1', dp: 30 }),
        genotype({ sample: 'M', gt: '0/0', dp: 30 }),
      ],
    });
    const member = (sample_id: string, role: string, sex: string, affected = false) => ({ sample_id, role, sex, affected });
    const link = (parent: string, child: string, role: 'father' | 'mother') => ({
      id: `${parent}-${child}`,
      relationship_type: 'parent_child' as const,
      sample_id_a: parent,
      sample_id_b: child,
      role_a: role,
      role_b: 'child',
    });
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AcmgClassificationModal
          variant={trioVariant}
          familyId="F1"
          // The grandparents come first, so a lookup by role finds them.
          members={[
            member('GF', 'father', 'male'),
            member('GM', 'mother', 'female'),
            member('P', 'proband', 'female', true),
            member('F', 'father', 'male'),
            member('M', 'mother', 'female'),
          ]}
          relationships={[link('F', 'P', 'father'), link('M', 'P', 'mother'), link('GF', 'F', 'father'), link('GM', 'F', 'mother')]}
          onClose={vi.fn()}
          onSave={vi.fn(async () => undefined)}
        />
      </QueryClientProvider>,
    );

    const pm6 = await screen.findByRole('checkbox', { name: /PM6/ });
    const row = pm6.closest('.acmg-criterion') as HTMLElement;
    await waitFor(() => expect(row).toHaveClass('acmg-criterion--na'));
    expect(pm6).not.toBeChecked();
    await userEvent.hover(row);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Inherited from a parent — not de novo.');
  });

  // A de novo variant is PS2 where the Sample QC confirms both parents, at the strength the
  // phenotype gives it; PS2 and PM6 are never checked together.
  it('proposes PS2 from the Sample QC and swaps it with PM6 when PM6 is checked', async () => {
    const deNovoVariant: SmallVariant = smallVariant({
      _id: 'chr4-400-C-T',
      chr: 'chr4',
      start: 400,
      end: 400,
      type: 'SNV',
      gene: 'GENEW',
      gene_id: 'ENSG0004',
      effect: 'missense_variant',
      hgvsp: 'p.Arg20Cys',
      revel: 0.95,
      genotypes: [
        genotype({ sample: 'P', gt: '0/1', dp: 30 }),
        genotype({ sample: 'F', gt: '0/0', dp: 30 }),
        genotype({ sample: 'M', gt: '0/0', dp: 30 }),
      ],
    });
    const pass = (parent: string) => ({
      sample_a: parent,
      sample_b: 'P',
      expected_relationship: 'parent-child',
      inferred_relationship: 'parent-child',
      kinship: 0.25,
      ibs0_rate: 0.0001,
      informative_sites: 9000,
      status: 'pass',
      message: `Confirmed parent-child (${parent}).`,
    });
    mockedGet.mockImplementation((url: string, config?: { params?: { symbol?: string } }) => {
      if (url.includes('/hpo')) return Promise.resolve({ data: [] });
      if (url.includes('/qc/sample-integrity')) {
        return Promise.resolve({ data: { relatedness_checks: [pass('F'), pass('M')], mendelian_checks: [] } });
      }
      if (url.includes('/gene-phenotype-score')) {
        expect(config?.params?.symbol).toBe('GENEW');
        return Promise.resolve({
          data: { symbol: 'GENEW', score: 0.72, hpo_count: 2, matched: [{ hpo_id: 'HP:0001250', label: 'Seizure' }] },
        });
      }
      return Promise.resolve({ data: { extra: { clingen_dosage_assertions: [], gencc_assertions: [], hpo_terms: [] } } });
    });
    const member = (sample_id: string, role: string, sex: string, affected = false) => ({ sample_id, role, sex, affected });
    const link = (parent: string, role: 'father' | 'mother') => ({
      id: `${parent}-P`,
      relationship_type: 'parent_child' as const,
      sample_id_a: parent,
      sample_id_b: 'P',
      role_a: role,
      role_b: 'child',
    });
    const onSave: (payload: SmallVariantReviewSavePayload) => Promise<void> = vi.fn(async () => undefined);
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AcmgClassificationModal
          variant={deNovoVariant}
          familyId="F1"
          members={[member('P', 'proband', 'female', true), member('F', 'father', 'male'), member('M', 'mother', 'female')]}
          relationships={[link('F', 'father'), link('M', 'mother')]}
          onClose={vi.fn()}
          onSave={onSave}
        />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();

    const ps2 = await screen.findByRole('checkbox', { name: /PS2/ });
    await waitFor(() => expect(ps2).toBeChecked());
    expect(screen.getByLabelText('PS2 strength')).toHaveValue('strong');
    const pm6 = screen.getByRole('checkbox', { name: /PM6/ });
    expect(pm6).not.toBeChecked();
    // The evidence shows the parentage the Sample QC confirmed, the phenotype's tier and the
    // in silico marks of the variant card.
    expect(screen.getByText('confirmed — Confirmed parent-child (F).')).toBeInTheDocument();
    expect(screen.getByText(/0\.72 · highly specific for the gene \(Seizure\)/)).toBeInTheDocument();
    const revel = within(screen.getByRole('region', { name: 'In silico' })).getByRole('img', { name: 'pathogenic, strong' });
    expect(revel).toHaveAttribute('title', expect.stringMatching(/^REVEL 0\.95/));

    // Checking PM6 unchecks PS2, which is then marked as not combined with it.
    await user.click(pm6);
    expect(pm6).toBeChecked();
    expect(ps2).not.toBeChecked();
    const ps2Row = ps2.closest('.acmg-criterion') as HTMLElement;
    expect(within(ps2Row).getByLabelText('Not combined with PM6')).toBeInTheDocument();
    await user.hover(ps2Row);
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      'Not combined with PM6: one de novo observation is either confirmed (PS2) or assumed (PM6), never both (ClinGen SVI).',
    );
    // And checking PS2 again unchecks PM6.
    await user.click(ps2);
    expect(ps2).toBeChecked();
    expect(pm6).not.toBeChecked();

    await user.click(screen.getByRole('button', { name: /save classification/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(onSave).mock.calls[0][0] as SmallVariantReviewSavePayload;
    const accepted = (payload.acmg?.criteria ?? []).filter((c) => c.accepted).map((c) => c.code);
    expect(accepted).toContain('PS2');
    expect(accepted).not.toContain('PM6');
  });

  // PM1 and PP3 together count at most Strong: setting one lowers or unchecks the other.
  it('keeps PM1 and PP3 within Strong together', async () => {
    const missense: SmallVariant = smallVariant({
      _id: 'chr5-500-G-C',
      chr: 'chr5',
      start: 500,
      end: 500,
      type: 'SNV',
      gene: 'GENEX',
      gene_id: 'ENSG0005',
      effect: 'missense_variant',
      hgvsp: 'p.Gly50Ala',
      revel: 0.95,
      genotypes: [],
    });
    renderModal(undefined, missense);
    const user = userEvent.setup();

    const pp3 = await screen.findByRole('checkbox', { name: /PP3/ });
    await waitFor(() => expect(pp3).toBeChecked());
    expect(screen.getByLabelText('PP3 strength')).toHaveValue('strong');

    // PM1 at its default Moderate: PP3 lowered from Strong to Moderate (2 + 2 = 4).
    await user.click(screen.getByRole('checkbox', { name: /PM1/ }));
    expect(screen.getByLabelText('PP3 strength')).toHaveValue('moderate');
    expect(pp3).toBeChecked();

    // PM1 raised to Strong: no room for PP3, which is unchecked and marked.
    await user.selectOptions(screen.getByLabelText('PM1 strength'), 'strong');
    expect(pp3).not.toBeChecked();
    const row = pp3.closest('.acmg-criterion') as HTMLElement;
    expect(within(row).getByLabelText('Not combined with PM1')).toBeInTheDocument();
    await user.hover(row);
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      'Capped with PM1: PM1 and PP3 together count at most Strong, 4 points (ClinGen SVI, Pejaver et al. 2022).',
    );
  });

  it('says the Sample QC could not be loaded and offers a de novo variant as PM6', async () => {
    mockedGet.mockImplementation((url: string) => {
      if (url.includes('/hpo')) return Promise.resolve({ data: [] });
      if (url.includes('/qc/sample-integrity')) return Promise.reject(new Error('down'));
      if (url.includes('/gene-phenotype-score')) return Promise.resolve({ data: NO_PHENOTYPE_SCORE });
      return Promise.resolve({ data: { extra: { clingen_dosage_assertions: [], gencc_assertions: [], hpo_terms: [] } } });
    });
    renderModal();

    expect(await screen.findByText(/Could not load the family's Sample QC/)).toHaveTextContent(
      /a de novo variant is offered as PM6: use PS2 only where parentage is confirmed/,
    );
  });

  it('recomputes the class when a strength is changed', async () => {
    renderModal();
    const user = userEvent.setup();

    await screen.findByText('PVS1');
    await waitFor(() => expect(screen.getByText('Likely pathogenic - class 4')).toBeInTheDocument());

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
    // PVS1 (8) + PM2 (1) = 9 → likely pathogenic (class 4).
    expect(payload.classification).toBe('Likely pathogenic - class 4');
    expect(payload.tags).toContain('acmg_class_4');
    expect(payload.acmg?.point_total).toBe(9);
    expect(payload.acmg?.criteria.some((c) => c.code === 'PVS1' && c.accepted)).toBe(true);
  });

  it('emits the VUS sub-tier tag for a variant that lands in the VUS band', async () => {
    // Missense, very rare in gnomAD → only PM2 (1 pt) auto-applies → VUS cold.
    const vusVariant: SmallVariant = smallVariant({
      _id: 'chr2-200-C-G',
      chr: 'chr2',
      start: 200,
      end: 200,
      type: 'SNV',
      gene: 'GENEY',
      gene_id: 'ENSG0002',
      effect: 'missense_variant',
      gnomad_af: 1e-6,
      hgvsp: 'p.Ala67Gly',
      genotypes: [],
    });
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

  it('preserves in-progress analyst edits when late gene/HPO context resolves', async () => {
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

    // HPO resolves late (a proband 'present' term), re-running the seeding effect, which
    // must not re-apply the auto-suggestions and re-check PVS1.
    resolveHpo({
      data: [{ status: 'present', hpo_id: 'HP:0001250', label: 'Seizure', sample_id: 'S1' }],
    });

    // The PubMed link gains HPO scope, proving the seeding effect re-ran...
    await screen.findByRole('link', { name: /PubMed \(gene \+ HPO\)/ });
    // ...and the analyst's edit survived it.
    expect(screen.getByRole('checkbox', { name: /PVS1/ })).not.toBeChecked();
  });

  // A deleted custom tag stays on the reviews that hold it: the dialog lists it, marked,
  // only where the review holds it, so it can be removed, and adds it to no other review.
  const tagDefinitions: SmallVariantTagDefinition[] = [
    tagDefinition({
      key: 'needs_segregation',
      label: 'Needs segregation',
      group: 'custom',
      color: '#aa3366',
      sort_order: 500,
      scope: 'global',
      is_custom: true,
      is_active: true,
    }),
    tagDefinition({
      key: 'probe_x',
      label: 'Probe X',
      group: 'custom',
      color: '#336699',
      sort_order: 500,
      scope: 'global',
      is_custom: true,
      is_active: false,
    }),
  ];

  it('offers a deleted tag only to a review that holds it, marked, to be removed', async () => {
    const onSave = renderModal(
      undefined,
      { ...variant, review: review({ variant_id: variant._id, tags: ['probe_x'] }) },
      tagDefinitions,
    );
    const user = userEvent.setup();

    const held = await screen.findByRole('button', { name: 'Probe X (deleted)' });
    expect(held).toHaveAttribute('aria-pressed', 'true');
    await user.click(held);
    await user.click(screen.getByRole('button', { name: /save classification/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(onSave).mock.calls[0][0] as SmallVariantReviewSavePayload;
    expect(payload.tags).not.toContain('probe_x');
  });

  it('does not offer a deleted tag to a review that does not hold it', async () => {
    renderModal(undefined, variant, tagDefinitions);

    await screen.findByRole('button', { name: 'Needs segregation' });
    expect(screen.queryByRole('button', { name: /Probe X/ })).toBeNull();
  });

  // The data the criteria are weighed on is in the dialog itself, each kind of data in its
  // own card, and a value the annotation does not hold reads "—", never as evidence either way.
  it('shows the evidence beside the criteria: consequence, frequencies, in silico, gene, ClinVar, phenotype and family', async () => {
    const evidenceVariant: SmallVariant = smallVariant({
      ...variant,
      effect: 'missense_variant',
      impact: 'MODERATE',
      lof: null,
      hgvsc: 'c.298C>T',
      hgvsp: 'p.Arg100Trp',
      transcript_id: 'ENST0001',
      mane_select: true,
      exon: '3/12',
      gnomad_af: 2e-5,
      gnomad_hom_count: 0,
      population_frequencies: { gnomad_faf95_af: 1e-5 },
      internal_cohort: { samples: 2, het: 2, hom: 0, families: 1 },
      revel: 0.91,
      cadd_phred: 28.4,
      alpha_missense_pathogenicity: 0.987,
      alpha_missense_class: 'likely_pathogenic',
      gene_pli: 0.99,
      clinvar: 'Likely_pathogenic',
      clinvar_stars: 2,
      clinvar_conditions: ['Disorder X'],
      genotypes: [genotype({ sample: 'PROBAND', gt: '0/1', dp: 41 }), genotype({ sample: 'MOTHER', gt: '0/0', dp: 35 })],
    });
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AcmgClassificationModal
          variant={evidenceVariant}
          familyId="F1"
          members={[
            { sample_id: 'PROBAND', role: 'proband', sex: 'female', affected: true },
            { sample_id: 'MOTHER', role: 'mother', sex: 'female', affected: false },
          ]}
          onClose={vi.fn()}
          onSave={vi.fn(async () => undefined)}
        />
      </QueryClientProvider>,
    );

    const card = (name: string) => within(screen.getByRole('region', { name }));
    const value = (name: string, label: string) =>
      card(name).getByText(label, { selector: 'dt' }).nextElementSibling?.textContent;

    expect(value('Consequence', 'Consequence')).toBe('missense variant (MODERATE)');
    expect(value('Consequence', 'HGVS c.')).toBe('c.298C>T');
    expect(value('Consequence', 'Transcript')).toBe('ENST0001 (MANE Select)');
    expect(value('Consequence', 'Exon / intron')).toBe('exon 3/12');
    expect(card('Consequence').getByText('Informs PVS1 · PM1 · PM4 · BP1 · BP3 · BP7')).toBeInTheDocument();

    expect(value('Population frequency', 'gnomAD AF')).toBe('2.0e-5');
    expect(value('Population frequency', 'gnomAD FAF95')).toBe('1.0e-5');
    expect(value('Population frequency', 'gnomAD homozygotes')).toBe('0');
    expect(value('Population frequency', 'In-house cohort')).toBe('2 samples (2 het / 0 hom) · 1 family');

    expect(value('In silico', 'REVEL')).toBe('0.91');
    expect(value('In silico', 'CADD')).toBe('28.40');
    expect(value('In silico', 'AlphaMissense')).toBe('0.987 · likely pathogenic');
    // Not annotated: shown as absent, not left out.
    expect(value('In silico', 'SpliceAI')).toBe('—');

    expect(value('Gene', 'pLI')).toBe('0.990');
    expect(await card('Gene').findByText('Sufficient evidence for dosage pathogenicity')).toBeInTheDocument();

    expect(value('ClinVar', 'Classification')).toBe('Likely pathogenic ★★☆☆');
    expect(value('ClinVar', 'Conditions')).toBe('Disorder X');

    expect(await card('Phenotype').findByText('none recorded')).toBeInTheDocument();

    const family = within(screen.getByRole('region', { name: 'Family' }));
    expect(
      family.getAllByRole('row').slice(1).map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent)),
    ).toEqual([
      ['PROBAND', 'proband', 'affected', '0/1', '41'],
      ['MOTHER', 'mother', 'unaffected', '0/0', '35'],
    ]);
  });
});
