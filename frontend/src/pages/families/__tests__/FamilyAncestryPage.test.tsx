import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, expect, it, vi } from 'vitest';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import FamilyAncestryPage from '../FamilyAncestryPage';

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('../../../lib/api', () => ({ default: api }));
vi.mock('../FamilyCaseHeader', () => ({ default: () => <div>Family header</div> }));
vi.mock('../FamilyAncestryPanel', () => ({ default: ({ enabled }: { enabled: boolean }) => <div>Ancestry panel {String(enabled)}</div> }));
vi.mock('../FamilyWgsSourcePanel', () => ({ default: ({ currentSource }: { currentSource: boolean }) => <div>Verified source {String(currentSource)}</div> }));
vi.mock('../../../lib/reference', () => ({ useFamilyReference: () => ({ projectId: 'project1', assemblyName: 'GRCh38', isLoading: false, isError: false, retry: vi.fn() }) }));

const renderPage = () => render(<QueryClientProvider client={createTestQueryClient()}><MemoryRouter initialEntries={['/families/F1/ancestry?project_id=project1']}>
  <Routes><Route path="/families/:familyId/ancestry" element={<FamilyAncestryPage />} /></Routes>
</MemoryRouter></QueryClientProvider>);

beforeEach(() => {
  vi.resetAllMocks();
  api.get.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith('/ancestry')
    ? { source_state: 'ready', runs: [] } : { family_id: 'F1', projects: ['project1'] } }));
});

it('opens standalone ancestry and source registration without any score calculation or catalogue calls', async () => {
  renderPage();
  expect(await screen.findByRole('heading', { name: 'Ancestry' })).toBeVisible();
  expect(await screen.findByText('Verified source true')).toBeVisible();
  expect(screen.getByText('Ancestry panel true')).toBeVisible();
  expect(api.get).toHaveBeenCalledWith('/families/F1/ancestry', { params: { project_id: 'project1' } });
  expect(api.get.mock.calls.every(([path]) => !String(path).includes('pgs'))).toBe(true);
  expect(api.post).not.toHaveBeenCalled();
});

it('keeps unavailable families explicit and provides a retry', async () => {
  api.get.mockRejectedValue(new Error('Unavailable'));
  renderPage();
  expect(await screen.findByText('Family could not be loaded')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Retry' })).toBeVisible();
});
