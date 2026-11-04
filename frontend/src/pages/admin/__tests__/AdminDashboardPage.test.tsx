// Pins the admin landing page: six domain groups whose links lead to each admin workspace, each
// link named by its label alone with its description as a tooltip, and every target a route
// declared behind the RequireAdmin guard (none falls to "not found"). An admin sees every
// workspace; a superuser only theirs (the configuration the analyses read and package import),
// each routed behind the superuser guard.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it } from 'vitest';

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

// A superuser's workspaces; every other one is an admin's alone.
const SUPERUSER_CARDS = new Set([
  'Gene panels',
  'Sequencing QC thresholds',
  'Package import',
  'Variant tags',
  'Preset filters',
]);

// The admin routes sit inside `<Route element={<RequireAdmin />}>` in index.tsx, a superuser's
// inside `<Route element={<RequireAdmin superuser />}>`. They are all self-closing, so each block
// ends at the first `</Route>` after it; were a nested layout route ever added, a slice could only
// get shorter — a false alarm, never a false pass.
const APP_SOURCE = readFileSync(path.resolve(process.cwd(), 'src/index.tsx'), 'utf8');
const guardedPaths = (opening: string): Set<string> => {
  const start = APP_SOURCE.indexOf(opening);
  if (start < 0) return new Set();
  const block = APP_SOURCE.slice(start, APP_SOURCE.indexOf('</Route>', start));
  return new Set([...block.matchAll(/\bpath="([^"]+)"/g)].map((match) => match[1]));
};
const ADMIN_PATHS = guardedPaths('<Route element={<RequireAdmin />}>');
const SUPERUSER_PATHS = guardedPaths('<Route element={<RequireAdmin superuser />}>');

const renderPage = (role = 'admin') => {
  localStorage.setItem('token', 'token-123');
  localStorage.setItem('role', role);
  return render(
    <MemoryRouter>
      <AdminDashboardPage />
    </MemoryRouter>,
  );
};

beforeEach(() => {
  localStorage.clear();
});

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

  it.each(GROUPS)('links each $title workspace by its label', ({ title, cards }) => {
    renderPage();
    const section = sectionFor(title);

    expect(within(section).getAllByRole('link')).toHaveLength(cards.length);
    for (const [label, href] of cards) {
      expect(within(section).getByRole('link', { name: label })).toHaveAttribute('href', href);
    }
  });

  it('describes what each workspace holds in a tooltip that pointing or focus opens', () => {
    renderPage();
    const users = screen.getByRole('link', { name: 'Users' });
    // The description is not part of the link's text or name.
    expect(users).toHaveTextContent(/^Users$/);
    expect(screen.queryByRole('tooltip')).toBeNull();

    fireEvent.focus(users);
    expect(screen.getByRole('tooltip')).toHaveTextContent('User accounts, roles, and activation status.');
    expect(users).toHaveAccessibleDescription('User accounts, roles, and activation status.');
    fireEvent.blur(users);
    expect(screen.queryByRole('tooltip')).toBeNull();

    const auditLogs = screen.getByRole('link', { name: 'Audit logs' });
    fireEvent.mouseEnter(auditLogs.parentElement as HTMLElement);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Request, user, status, and update-event history.');
  });

  it('points every card at a route declared behind its guard', () => {
    renderPage();
    expect(ADMIN_PATHS.size).toBeGreaterThan(0);
    expect(SUPERUSER_PATHS.size).toBeGreaterThan(0);

    const cards = GROUPS.flatMap((group) => group.cards);
    expect(screen.getAllByRole('link')).toHaveLength(cards.length);
    // An unrouted target lands on "Page not found"; one routed outside its guard would be
    // offered to a role that may not use it.
    const misrouted = cards.filter(([label, href]) =>
      SUPERUSER_CARDS.has(label) ? !SUPERUSER_PATHS.has(href) : !ADMIN_PATHS.has(href),
    );
    expect(misrouted).toEqual([]);
  });

  it("shows a superuser only a superuser's workspaces, in their groups", () => {
    renderPage('superuser');

    const shown = GROUPS.map((group) => ({
      title: group.title,
      cards: group.cards.filter(([label]) => SUPERUSER_CARDS.has(label)),
    })).filter((group) => group.cards.length > 0);
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual(
      shown.map((group) => group.title),
    );
    for (const { title, cards } of shown) {
      const section = sectionFor(title);
      expect(within(section).getAllByRole('link').map((link) => link.textContent)).toEqual(
        cards.map(([label]) => label),
      );
    }
    expect(screen.queryByRole('link', { name: 'Users' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Audit logs' })).toBeNull();
  });
});
