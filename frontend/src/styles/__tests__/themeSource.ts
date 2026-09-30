import { readFileSync } from 'node:fs';
import path from 'node:path';

/** The stylesheet's entry: it imports the modules under styles/theme/ in cascade order (#711). */
export const THEME_ENTRY = path.resolve(process.cwd(), 'src/styles/theme.css');
export const THEME_DIR = path.resolve(process.cwd(), 'src/styles/theme');

export interface ThemeModule {
  /** The path theme.css imports it by, relative to src/styles (`theme/base.css`). */
  name: string;
  css: string;
}

/** The modules theme.css imports, in the order it imports them. */
export const themeModules = (): ThemeModule[] =>
  [
    ...readFileSync(THEME_ENTRY, 'utf8').matchAll(
      /@import\s+['"]\.\/([^'"]+)['"]\s*;/g
    ),
  ].map(([, name]) => ({
    name,
    css: readFileSync(path.resolve(path.dirname(THEME_ENTRY), name), 'utf8'),
  }));

/** The whole stylesheet in cascade order, as the browser receives it. */
export const themeText = (): string =>
  themeModules()
    .map((module) => module.css)
    .join('\n');
