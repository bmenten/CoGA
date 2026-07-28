import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MonarchPhenotypeMatchPanel from '../MonarchPhenotypeMatchPanel';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn() },
}));

const renderPanel = () =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <MonarchPhenotypeMatchPanel familyId="fam-1" projectId="proj-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );

describe('MonarchPhenotypeMatchPanel', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('fetches and renders ranked gene matches on demand, linking in-platform genes', async () => {
    (api.get as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        group: 'Human Genes',
        sample_id: null,
        query_hpo_ids: ['HP:0000851', 'HP:0000853'],
        source: 'Monarch Initiative semsim',
        results: [
          {
            rank: 1,
            score: 12.34,
            id: 'HGNC:11764',
            name: 'TG',
            category: 'biolink:Gene',
            symbol: 'TG',
            gene_in_platform: true,
            matching_phenotypes: [{ hpo_id: 'HP:0000851', label: 'Congenital hypothyroidism' }],
            extra_phenotypes: [{ hpo_id: 'HP:0000823', label: 'Delayed puberty' }],
          },
          {
            rank: 2,
            score: 11.45,
            id: 'HGNC:21071',
            name: 'IYD',
            category: 'biolink:Gene',
            symbol: 'IYD',
            gene_in_platform: false,
            matching_phenotypes: [],
            extra_phenotypes: [],
          },
        ],
      },
    });

    renderPanel();

    // Nothing fetched until the user triggers the match.
    expect(api.get).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: /find candidate genes/i }));

    const tgLink = await screen.findByRole('link', { name: 'TG' });
    expect(tgLink).toHaveAttribute(
      'href',
      '/genes?gene=TG&family_id=fam-1&project_id=proj-1',
    );
    // Requests the full candidate set the Monarch semsim API allows.
    expect(api.get).toHaveBeenCalledWith(
      '/families/fam-1/phenotype-match',
      expect.objectContaining({ params: expect.objectContaining({ limit: 50 }) }),
    );
    expect(screen.getByText(/Top 2 candidate genes/i)).toBeInTheDocument();
    expect(screen.getByText(/Ranked from 2 observed phenotypes/i)).toBeInTheDocument();
    // Four-column table: gene, score, matching and extra HPO terms.
    expect(screen.getByRole('columnheader', { name: /matching hpo terms/i })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /extra hpo terms/i })).toBeInTheDocument();
    expect(screen.getByText('12.34')).toBeInTheDocument();
    expect(screen.getByText('Congenital hypothyroidism')).toBeInTheDocument();
    expect(screen.getByText('Delayed puberty')).toBeInTheDocument();
    // Not-in-platform gene is shown but not linked.
    expect(screen.queryByRole('link', { name: 'IYD' })).not.toBeInTheDocument();
    expect(screen.getByText(/not in platform/i)).toBeInTheDocument();
  });

  it('shows guidance when the family has no present phenotypes', async () => {
    (api.get as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        group: 'Human Genes',
        sample_id: null,
        query_hpo_ids: [],
        source: 'Monarch Initiative semsim',
        results: [],
      },
    });

    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: /find candidate genes/i }));

    expect(
      await screen.findByText(/No present HPO phenotypes recorded/i),
    ).toBeInTheDocument();
  });
});
