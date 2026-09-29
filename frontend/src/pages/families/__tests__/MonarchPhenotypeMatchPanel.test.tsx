import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import MonarchPhenotypeMatchPanel from '../MonarchPhenotypeMatchPanel';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn() },
}));

const mockedGet = api.get as unknown as ReturnType<typeof vi.fn>;

const renderPanel = (client: QueryClient = createTestQueryClient()) =>
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <MonarchPhenotypeMatchPanel familyId="fam-1" projectId="proj-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );

/** A match of one in-platform gene, ranked from the given present terms. */
const matchOf = (symbol: string, queryHpoIds = ['HP:0000851']) => ({
  data: {
    group: 'Human Genes',
    sample_id: null,
    query_hpo_ids: queryHpoIds,
    source: 'Monarch Initiative semsim',
    results: [
      {
        rank: 1,
        score: 10,
        id: `HGNC:${symbol}`,
        name: symbol,
        category: 'biolink:Gene',
        symbol,
        gene_in_platform: true,
        matching_phenotypes: [],
        extra_phenotypes: [],
      },
    ],
  },
});

/** The backend's answer when the Monarch semsim service is down. */
const monarchDown = () =>
  Promise.reject(
    Object.assign(new Error('Request failed with status code 502'), {
      response: {
        status: 502,
        data: { detail: 'Monarch phenotype matching is unavailable: read timeout' },
      },
    }),
  );

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
};

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

  it('runs the match again on Re-run match and shows the new result', async () => {
    // A phenotype was added between the two runs: the second ranking differs.
    mockedGet
      .mockResolvedValueOnce(matchOf('TG'))
      .mockResolvedValueOnce(matchOf('PAX8', ['HP:0000851', 'HP:0000853']));

    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Find candidate genes' }));
    expect(await screen.findByRole('link', { name: 'TG' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Re-run match' }));

    expect(await screen.findByRole('link', { name: 'PAX8' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'TG' })).not.toBeInTheDocument();
    expect(screen.getByText(/Ranked from 2 observed phenotypes/i)).toBeInTheDocument();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it('shows a re-run as in progress, without the previous result standing as the current one', async () => {
    const rerun = deferred<ReturnType<typeof matchOf>>();
    mockedGet.mockResolvedValueOnce(matchOf('TG')).mockReturnValueOnce(rerun.promise);

    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Find candidate genes' }));
    await screen.findByRole('link', { name: 'TG' });

    await userEvent.click(screen.getByRole('button', { name: 'Re-run match' }));

    expect(await screen.findByRole('status')).toHaveTextContent(
      "Matching this family's phenotypes with Monarch…",
    );
    expect(screen.getByRole('button', { name: 'Matching…' })).toBeDisabled();
    expect(screen.queryByRole('link', { name: 'TG' })).not.toBeInTheDocument();

    rerun.resolve(matchOf('PAX8'));
    expect(await screen.findByRole('link', { name: 'PAX8' })).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Re-run match' })).toBeEnabled();
  });

  it('says a failed match failed, with the reason, and runs it again from the button', async () => {
    mockedGet.mockImplementationOnce(monarchDown).mockResolvedValueOnce(matchOf('TG'));

    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Find candidate genes' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load the phenotype match — this is not an empty result.');
    expect(alert).toHaveTextContent('Monarch phenotype matching is unavailable: read timeout');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByText(/No phenotype matches were returned/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Re-run match' }));

    expect(await screen.findByRole('link', { name: 'TG' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });

  it('does not leave the previous result on screen when a re-run fails', async () => {
    mockedGet.mockResolvedValueOnce(matchOf('TG')).mockImplementationOnce(monarchDown);

    renderPanel();
    await userEvent.click(screen.getByRole('button', { name: 'Find candidate genes' }));
    await screen.findByRole('link', { name: 'TG' });

    await userEvent.click(screen.getByRole('button', { name: 'Re-run match' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('read timeout');
    expect(screen.queryByRole('link', { name: 'TG' })).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('sends the phenotypes to Monarch only when pressed, and every press is a new run', async () => {
    mockedGet.mockResolvedValueOnce(matchOf('TG')).mockResolvedValueOnce(matchOf('PAX8'));
    const client = createTestQueryClient();

    const first = renderPanel(client);
    await userEvent.click(screen.getByRole('button', { name: 'Find candidate genes' }));
    await screen.findByRole('link', { name: 'TG' });
    first.unmount();

    // Back on the family page: an earlier answer is neither shown nor re-sent by itself.
    renderPanel(client);
    expect(screen.getByRole('button', { name: 'Find candidate genes' })).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(1));

    await userEvent.click(screen.getByRole('button', { name: 'Find candidate genes' }));
    expect(await screen.findByRole('link', { name: 'PAX8' })).toBeInTheDocument();
    expect(mockedGet).toHaveBeenCalledTimes(2);
  });
});
