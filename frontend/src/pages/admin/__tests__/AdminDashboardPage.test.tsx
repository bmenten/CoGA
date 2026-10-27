// Pins the admin landing page: six domain groups whose cards link to each admin workspace, and
// every card target is a route declared behind the RequireAdmin guard (none falls to "not found").
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import AdminDashboardPage from '../AdminDashboardPage';

const GROUPS: Array<{ title: string; cards: Array<[label: string, href: string]> }> = [
  {
    title: 'Reference data',
    cards: [
      ['Species & assemblies', '/admin/reference/assemblies'],
      ['Gene panels', '/admin/reference/gene-panels'],
      ['HPO terminology', '/admin/reference/hpo'],
      ['Monarch data', '/admin/reference/monarch'],
      ['Repeat catalogue', '/admin/reference/repeat-loci'],
    ],
  },
  {
    title: 'User & access management',
    cards: [
      ['Users', '/admin/access/users'],
      ['Projects & access', '/admin/access/projects'],
    ],
  },
  {
    title: 'Data management',
    cards: [
      ['Family & sample data', '/admin/data/families'],
      ['Family statuses', '/admin/data/family-statuses'],
      ['Sequencing QC thresholds', '/admin/data/qc-thresholds'],
      ['Package import', '/admin/data/upload'],
    ],
  },
  {
    title: 'Variant configuration',
    cards: [
      ['Variant tags', '/admin/variants/tags'],
      ['Preset filters', '/admin/variants/presets'],
    ],
  },
  {
    title: 'Database & operations',
    cards: [['ClickHouse tables & operations', '/admin/operations/clickhouse']],
  },
  {
    title: 'Monitoring & audit',
    cards: [['Audit logs', '/admin/monitoring/audit-logs']],
  },
];

// The admin routes sit inside `<Route element={<RequireAdmin />}>` in index.tsx. They are all
// self-closing, so the block ends at the first `</Route>` after it; were a nested layout route
// ever added, this slice could only get shorter — a false alarm, never a false pass.
const APP_SOURCE = readFileSync(path.resolve(process.cwd(), 'src/index.tsx'), 'utf8');
const GUARD_OPENING = '<Route element={<RequireAdmin />}>';
const guardStart = APP_SOURCE.indexOf(GUARD_OPENING);
const ADMIN_BLOCK = APP_SOURCE.slice(guardStart, APP_SOURCE.indexOf('</Route>', guardStart));
const GUARDED_PATHS = new Set([...ADMIN_BLOCK.matchAll(/\bpath="([^"]+)"/g)].map((match) => match[1]));

const renderPage = () =>
  render(
    <MemoryRouter>
      <AdminDashboardPage />
    </MemoryRouter>,
  );

const sectionFor = (title: string): HTMLElement => {
  const section = screen.getByRole('heading', { level: 2, name: title }).closest('section');
  if (!section) throw new Error(`no section for ${title}`);
  return section;
};

describe('AdminDashboardPage', () => {
  it('introduces the administration area and groups it by operational domain', () => {
    renderPage();

    expect(screen.getByRole('heading', { level: 1, name: 'Admin dashboard' })).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual(
      GROUPS.map((group) => group.title),
    );
  });

  it.each(GROUPS)('links each $title card to its workspace', ({ title, cards }) => {
    renderPage();
    const section = sectionFor(title);

    expect(within(section).getAllByRole('link')).toHaveLength(cards.length);
    for (const [label, href] of cards) {
      const card = within(section).getByRole('link', { name: (name) => name.startsWith(label) });
      expect(card).toHaveAttribute('href', href);
    }
  });

  it('describes what each workspace holds on its card', () => {
    renderPage();

    expect(screen.getByRole('link', { name: /^Users/ })).toHaveTextContent(
      'User accounts, roles, and activation status.',
    );
    expect(screen.getByRole('link', { name: /^Audit logs/ })).toHaveTextContent(
      'Request, user, status, and update-event history.',
    );
  });

  it('points every card at a route declared behind the admin guard', () => {
    renderPage();
    expect(guardStart).toBeGreaterThan(-1);

    const targets = screen.getAllByRole('link').map((link) => link.getAttribute('href') ?? '');
    expect(targets).toHaveLength(GROUPS.flatMap((group) => group.cards).length);
    // An unrouted target lands on "Page not found"; one routed outside the guard would be
    // offered to non-admins.
    expect(targets.filter((href) => !GUARDED_PATHS.has(href))).toEqual([]);
  });
});
