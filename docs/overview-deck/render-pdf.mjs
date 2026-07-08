// Render coga-overview-deck.html to a 7-page 16:9 PDF (one slide per page).
//   node render-pdf.mjs   ->  CoGA-overview-deck.pdf
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';
import { createRequire } from 'module';

const HERE = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { chromium } = require(resolve(HERE, '../../frontend/node_modules/playwright/index.js'));

const browser = await chromium.launch();
const page = await browser.newPage({ colorScheme: 'light' });
await page.goto('file://' + resolve(HERE, 'coga-overview-deck.html'));
await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'));
await page.emulateMedia({ media: 'print' });
await page.waitForTimeout(300);
await page.pdf({
  path: resolve(HERE, 'CoGA-overview-deck.pdf'),
  width: '13.333in', height: '7.5in', printBackground: true, pageRanges: '1-7',
});
await browser.close();
console.log('CoGA-overview-deck.pdf');
