// Render each 16:9 slide of coga-overview-deck.html to a 2x PNG.
//   node render.mjs        # light theme  -> slides/coga-*.png
//   node render.mjs dark   # dark theme   -> slides/coga-*-dark.png
// Uses the Playwright/Chromium already installed under ../../frontend/node_modules.
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';
import { mkdirSync } from 'fs';
import { createRequire } from 'module';

const HERE = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { chromium } = require(resolve(HERE, '../../frontend/node_modules/playwright/index.js'));

const theme = process.argv[2] === 'dark' ? 'dark' : 'light';
const outDir = resolve(HERE, 'slides');
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1360, height: 900 },
  deviceScaleFactor: 2,
  colorScheme: theme,
});
await page.goto('file://' + resolve(HERE, 'coga-overview-deck.html'));
await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
await page.waitForTimeout(400);

const ids    = ['s0','s1','s2','s3','s4','s5','s6'];
const names  = ['00-title','01-ingestion','02-architecture','03-reporting','04-capabilities','05-knowledge','06-roadmap'];
const suffix = theme === 'dark' ? '-dark' : '';
for (let i = 0; i < ids.length; i++) {
  const el = await page.$('#' + ids[i]);
  await el.screenshot({ path: resolve(outDir, `coga-${names[i]}${suffix}.png`) });
  console.log(`coga-${names[i]}${suffix}.png`);
}
await browser.close();
console.log('done', theme);
