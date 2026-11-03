// A page that is loading says so with the spinner and the loading bar (ENG-10): `PageState`
// draws them only with `loading`, and some 30 loading states showed their title alone. This
// renders both forms, and scans the app's source, tests left out, for a loading state without
// it. The loading bar itself (`LoadingBar`) is an announced status.

import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import LoadingBar from '../LoadingBar';
import PageState from '../PageState';

const sourceRoot = path.resolve(process.cwd(), 'src');
const sources = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === '__tests__' || entry.name === 'test' ? [] : sources(full);
    return entry.name.endsWith('.tsx') ? [full] : [];
  });

/** Each `<PageState … />` whose title reads as loading ("Loading …", "Preparing …"). */
const loadingStates = (text: string): { body: string; line: number }[] =>
  [...text.matchAll(/<PageState\b([\s\S]*?)\/>/g)]
    .filter(([, body]) => /title=(?:"|\{`)(?:Loading|Preparing)\b/.test(body))
    .map((match) => ({ body: match[1], line: text.slice(0, match.index).split('\n').length }));

describe('PageState', () => {
  it('draws the spinner and an announced loading bar only when loading', () => {
    const { container, unmount } = render(<PageState loading title="Loading family workspace" />);
    expect(screen.getByRole('status', { name: 'Loading family workspace' })).toBeInTheDocument();
    expect(container.querySelector('.loading-bar')).not.toBeNull();
    expect(container.querySelector('.page-state-spinner')).not.toBeNull();
    expect(screen.getByRole('heading', { name: 'Loading family workspace' })).toBeInTheDocument();
    unmount();

    const settled = render(<PageState title="Family not found" />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(settled.container.querySelector('.loading-bar')).toBeNull();
    expect(settled.container.querySelector('.page-state-spinner')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Family not found' })).toBeInTheDocument();
  });

  it('is loading wherever the app shows a loading state', () => {
    const missing = sources(sourceRoot).flatMap((file) => {
      const text = readFileSync(file, 'utf8');
      return loadingStates(text)
        .filter(({ body }) => !/\sloading(?:[\s=/]|$)/.test(body))
        .map(({ line }) => `${path.relative(sourceRoot, file)}:${line}`);
    });
    expect(missing).toEqual([]);
  });

  it('would find one without it', () => {
    expect(loadingStates('<PageState kicker="Admin" title="Loading users" />')).toHaveLength(1);
    expect(loadingStates('<PageState title="Could not load users" />')).toHaveLength(0);
  });
});

describe('LoadingBar', () => {
  it('is an announced status with the animated flyer, named by its label', () => {
    const { container, unmount } = render(<LoadingBar label="Loading variants" />);
    expect(screen.getByRole('status', { name: 'Loading variants' })).toBeInTheDocument();
    expect(container.querySelector('.loading-bar-flyer')).not.toBeNull();
    unmount();

    render(<LoadingBar />);
    expect(screen.getByRole('status', { name: 'Loading…' })).toBeInTheDocument();
  });
});
