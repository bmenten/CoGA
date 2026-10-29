import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import FamilyCaseHeader from '../FamilyCaseHeader';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: apiMock }));

const family = {
  family_id: 'F1',
  pedigree: '',
  members: [{ sample_id: 'P1' }, { sample_id: 'M1' }, { sample_id: 'F1A' }],
  projects: ['p1'],
  metadata: {},
  status: { key: 'in_review', label: 'In review' },
  assigned_to: null,
  reviewed_by: null,
};

// Which kinds of data the family has, as the presence checks answer.
const answer = (url: string) => {
  if (url === '/families/F1') return { data: family };
  if (url === '/family-statuses') return { data: [{ key: 'in_review', label: 'In review' }] };
  if (url === '/families/F1/assignable-users') return { data: [] };
  if (url === '/projects') return { data: [{ id: 'p1', name: 'Rare disease' }] };
  if (url.startsWith('/families/F1/small-variants?')) return { data: { total: 12 } };
  if (url.startsWith('/families/F1/structural-variants?')) return { data: { total: 0 } };
  if (url.startsWith('/families/F1/repeat-expansions?')) return { data: { loci_count: 3 } };
  if (url.startsWith('/families/F1/paraphase?')) return { data: { genes_count: 0 } };
  if (url.startsWith('/families/F1/mitochondrial-dna?')) return { data: { variant_count: 0, has_coverage: false } };
  return { data: [] };
};

const renderHeader = (current: 'repeat-expansions' | 'paraphase' | 'report' | undefined) =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <FamilyCaseHeader
          kicker="Repeat expansions"
          familyId="F1"
          family={family}
          projectId="p1"
          assemblyLabel="GRCh38 p14"
          current={current}
          facts={
            <div className="family-workspace-stat">
              <span className="stat-label">Loci with calls</span>
              <strong className="family-workspace-stat-value">3</strong>
            </div>
          }
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );

// The workspace and every analysis page share one top card: the family's facts on one line, the
// case's workflow fields with Sample QC and the report, and the row of the family's analyses.
describe('FamilyCaseHeader', () => {
  beforeEach(() => {
    apiMock.get.mockReset();
    apiMock.put.mockReset();
    apiMock.get.mockImplementation((url: string) => Promise.resolve(answer(url)));
  });

  it("gives the family's facts, then the page's, on one line", async () => {
    const { container } = renderHeader('repeat-expansions');
    await screen.findByText('Rare disease');
    const facts = container.querySelector('.family-workspace-summary') as HTMLElement;
    expect(
      Array.from(facts.querySelectorAll('.family-workspace-stat')).map((fact) => fact.textContent),
    ).toEqual(['Members3', 'AssemblyGRCh38 p14', 'ProjectsRare disease', 'Loci with calls3']);
  });

  it('offers the case fields, Sample QC and the report on an analysis page', async () => {
    renderHeader('repeat-expansions');
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Family status' })).toHaveValue('in_review'));
    expect(screen.getByRole('combobox', { name: 'Assigned to' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Reviewed by' })).toBeInTheDocument();
    const actions = await screen.findByRole('group', { name: 'Family actions' });
    expect(within(actions).getByRole('link', { name: 'Sample QC' })).toHaveAttribute('href', '/families/F1/qc');
    expect(within(actions).getByRole('link', { name: 'Report' })).toHaveAttribute(
      'href',
      '/families/F1/report?project_id=p1',
    );

    // A field saves as it is picked, from any page.
    apiMock.put.mockResolvedValue({ data: { ...family, status: null } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Family status' }), { target: { value: '' } });
    await waitFor(() =>
      expect(apiMock.put).toHaveBeenCalledWith('/families/F1/metadata', { status_key: null }),
    );
  });

  it("lists the family's analyses with the page it is on marked, and only those with data", async () => {
    renderHeader('repeat-expansions');
    const nav = await screen.findByRole('navigation', { name: 'Variant analyses' });
    await waitFor(() =>
      expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual([
        'Small variants',
        'Repeat expansions',
      ]),
    );
    expect(within(nav).getByRole('link', { name: 'Repeat expansions' })).toHaveAttribute('aria-current', 'page');
    expect(within(nav).getByRole('link', { name: 'Small variants' })).not.toHaveAttribute('aria-current');
    expect(within(nav).getByRole('link', { name: 'Small variants' })).toHaveAttribute(
      'href',
      '/families/F1/small-variants?project_id=p1',
    );
  });

  it('puts Sample QC and the report at the right of the foot row, as links like the analyses', async () => {
    const { container } = renderHeader('report');
    const actions = await screen.findByRole('group', { name: 'Family actions' });
    const bar = container.querySelector('.family-case-header-bar') as HTMLElement;
    expect(bar).toContainElement(actions);
    expect(bar).toContainElement(await screen.findByRole('navigation', { name: 'Variant analyses' }));
    // After the analyses, at the row's end: not a button above them.
    expect(bar.lastElementChild).toBe(actions);
    for (const link of within(actions).getAllByRole('link')) {
      expect(link).toHaveClass('family-module-link');
      expect(link).not.toHaveClass('button-secondary');
    }
    expect(within(actions).getByRole('link', { name: 'Report' })).toHaveAttribute('aria-current', 'page');
    expect(within(actions).getByRole('link', { name: 'Sample QC' })).not.toHaveAttribute('aria-current');
  });

  it('keeps the page it is on in the row even when the check finds no data there', async () => {
    renderHeader('paraphase');
    const nav = await screen.findByRole('navigation', { name: 'Variant analyses' });
    await waitFor(() => expect(within(nav).getByRole('link', { name: 'Small variants' })).toBeInTheDocument());
    expect(within(nav).getByRole('link', { name: 'Paraphase' })).toHaveAttribute('aria-current', 'page');
  });
});
