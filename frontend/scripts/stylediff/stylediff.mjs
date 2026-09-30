// Side-by-side computed-style diff of two production builds of the frontend, rendered against
// the same backend and data. It verifies that a CSS change renders exactly as intended: for a
// clean-up, that nothing renders differently (#712). See README.md for the set-up.
//
// For every page state and viewport width it opens the page in both builds and compares, per
// element (keyed by its structural DOM path), the full computed style of the element and of its
// ::before/::after/::placeholder/::marker, and its page-level box. Each state is also compared
// with :hover and with :focus/:focus-visible/:focus-within forced (through the DevTools protocol)
// on every element the base build's stylesheet styles that way, and with every <details> open.
// The three report pages are compared in print media as well.
//
//   node scripts/stylediff/stylediff.mjs --base http://127.0.0.1:4181 --cand http://127.0.0.1:4182
//        [--out report.json] [--widths 1440,1000,700] [--only /families/] [--shard 0/4]
//        [--coverage coverage.json [--coverage-only]]
//
// STYLEDIFF_CHANNEL=chrome drives an installed Chrome (with a throwaway profile) instead of
// Playwright's own browser. E2E_USER_EMAIL / E2E_USER_PASSWORD as for the Playwright journeys.
import fs from 'node:fs';
import { chromium } from 'playwright';

import { PAGES, PRINT, PUBLIC } from './states.mjs';

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
};
const BASE = arg('base');
const CAND = arg('cand', BASE);
const OUT = arg('out', 'stylediff-report.json');
const WIDTHS = arg('widths', '1440,1000,700').split(',').map(Number);
const ONLY = arg('only', '');
// --shard k/n renders every n-th page state starting at k (0-based), to run several in parallel.
const [SHARD, SHARDS] = arg('shard', '0/1').split('/').map(Number);
// --coverage FILE records which stylesheet rules the base build matched in any state;
// --coverage-only renders the base build alone for that, without comparing.
const COVERAGE = arg('coverage', '');
const COVERAGE_ONLY = process.argv.includes('--coverage-only');
const EMAIL = process.env.E2E_USER_EMAIL || 'e2e.playwright@example.com';
const PASSWORD = process.env.E2E_USER_PASSWORD || 'e2e-playwright-pw';
if (!BASE) {
  console.error('usage: stylediff.mjs --base <url> --cand <url> [--out file] (see README.md)');
  process.exit(2);
}

/** Runs in the page: every element's computed style (hashed) and box, or in full for `paths`. */
function capture({ paths } = {}) {
  const hash = (str) => {
    let h1 = 0xdeadbeef;
    let h2 = 0x41c6ce57;
    for (let i = 0; i < str.length; i++) {
      const ch = str.charCodeAt(i);
      h1 = Math.imul(h1 ^ ch, 2654435761);
      h2 = Math.imul(h2 ^ ch, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return 4294967296 * (2097151 & h2) + (h1 >>> 0);
  };
  // Custom properties are left out: they reach the rendering only through the standard
  // properties that read them, which are compared, and an unread one changes nothing. Names are
  // sorted because Chrome enumerates custom properties in an order that shifts with the sheet.
  const names = (cs) => {
    const out = [];
    for (let i = 0; i < cs.length; i++) if (!cs[i].startsWith('--')) out.push(cs[i]);
    return out.sort();
  };
  const props = (cs) => Object.fromEntries(names(cs).map((n) => [n, cs.getPropertyValue(n)]));
  const str = (cs) => names(cs).map((n) => `${n}:${cs.getPropertyValue(n)};`).join('');
  const pathOf = (el) => {
    const parts = [];
    while (el && el !== document.documentElement) {
      const parent = el.parentElement;
      const index = parent ? Array.prototype.indexOf.call(parent.children, el) : 0;
      parts.unshift(`${el.tagName.toLowerCase()}:${index}`);
      el = parent;
    }
    return parts.join('/');
  };
  const pseudos = (el) => {
    const list = ['::before', '::after'];
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') list.push('::placeholder');
    if (el.tagName === 'SUMMARY' || el.tagName === 'LI') list.push('::marker');
    return list;
  };
  const wanted = paths ? new Set(paths) : null;
  const out = [];
  const elements = [document.documentElement, document.body, ...document.body.querySelectorAll('*')];
  for (const el of elements) {
    if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE') continue;
    const p = pathOf(el);
    if (wanted && !wanted.has(p)) continue;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const rec = { p, c: typeof el.className === 'string' ? el.className : el.getAttribute('class') || '' };
    rec.r = [r.left + scrollX, r.top + scrollY, r.width, r.height].map((v) => Math.round(v * 2) / 2);
    if (wanted) rec.s = props(cs);
    else rec.h = hash(str(cs));
    for (const pseudo of pseudos(el)) {
      const ps = getComputedStyle(el, pseudo);
      const content = ps.getPropertyValue('content');
      const generated = pseudo === '::before' || pseudo === '::after';
      if (generated && (!content || content === 'none' || content === 'normal')) continue;
      rec[pseudo] = wanted ? props(ps) : hash(str(ps));
    }
    out.push(rec);
  }
  return out;
}

// Finish transitions and one-shot animations; hold infinite ones (spinners) at t=0, so both
// builds are captured at the same point of every animation.
async function freeze(page) {
  await page.evaluate(() => {
    for (const a of document.getAnimations()) {
      try {
        const timing = a.effect && a.effect.getComputedTiming ? a.effect.getComputedTiming() : null;
        if (timing && timing.iterations === Infinity) {
          a.pause();
          a.currentTime = 0;
        } else a.finish();
      } catch {
        try {
          a.pause();
          a.currentTime = 0;
        } catch {
          /* detached */
        }
      }
    }
  });
}

// Wait until the DOM has not changed for quietMs (tracks load progressively), at most maxMs.
async function domQuiet(page, quietMs = 800, maxMs = 12000) {
  await page.evaluate(
    ({ quietMs, maxMs }) =>
      new Promise((resolve) => {
        const start = performance.now();
        let timer;
        const done = () => {
          observer.disconnect();
          resolve();
        };
        const observer = new MutationObserver(() => {
          clearTimeout(timer);
          if (performance.now() - start > maxMs) done();
          else timer = setTimeout(done, quietMs);
        });
        observer.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
        timer = setTimeout(done, quietMs);
      }),
    { quietMs, maxMs },
  );
}

async function settle(page) {
  try {
    await page.waitForLoadState('networkidle', { timeout: 15000 });
  } catch {
    /* a page that keeps polling */
  }
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await page.mouse.move(0, 0);
  await domQuiet(page);
  await freeze(page);
}

/**
 * Runs in the page: the elements the loaded stylesheets style through :hover, :focus(-visible,
 * -within) or :active, as the selector up to the compound that carries the state, with the state
 * removed.
 */
function interactionSelectors() {
  const state = /:(hover|focus-visible|focus-within|focus|active)\b/;
  const out = new Set();
  const walk = (rules) => {
    for (const rule of rules) {
      if (rule.cssRules) walk(rule.cssRules);
      if (!rule.selectorText) continue;
      for (const selector of rule.selectorText.split(',')) {
        if (!state.test(selector)) continue;
        const m = selector.match(/^(.*?:(?:hover|focus-visible|focus-within|focus|active)\b[^\s>+~]*)/);
        const target = (m ? m[1] : selector)
          .replace(/:(hover|focus-visible|focus-within|focus|active)\b/g, '')
          .replace(/::?(before|after|placeholder|marker)\b/g, '')
          .trim();
        if (target && !/[>+~]$/.test(target)) out.add(target);
      }
    }
  };
  for (const sheet of document.styleSheets) {
    try {
      walk(sheet.cssRules);
    } catch {
      /* a cross-origin sheet */
    }
  }
  return [...out];
}

async function forceStates(S, selectors, pseudoClasses) {
  const { root } = await S.cdp.send('DOM.getDocument', { depth: -1 });
  const ids = new Set();
  for (const selector of selectors) {
    try {
      const { nodeIds } = await S.cdp.send('DOM.querySelectorAll', { nodeId: root.nodeId, selector });
      nodeIds.forEach((id) => ids.add(id));
    } catch {
      /* a selector the engine rejects */
    }
  }
  for (const nodeId of ids) {
    try {
      await S.cdp.send('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: pseudoClasses });
    } catch {
      /* detached */
    }
  }
  await S.page.waitForTimeout(100);
  await freeze(S.page);
}

async function clearStates(S) {
  const { root } = await S.cdp.send('DOM.getDocument', { depth: -1 });
  const { nodeIds } = await S.cdp.send('DOM.querySelectorAll', { nodeId: root.nodeId, selector: '*' });
  for (const nodeId of nodeIds) {
    try {
      await S.cdp.send('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: [] });
    } catch {
      /* detached */
    }
  }
}

async function login(S) {
  await S.page.goto(`${S.base}/login`);
  await S.page.locator('input[type="email"]').fill(EMAIL);
  await S.page.locator('input[type="password"]').fill(PASSWORD);
  await S.page.getByRole('button', { name: /login|signing in/i }).click();
  await S.page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 20000 });
}

const diffProps = (a = {}, b = {}) => {
  const d = {};
  for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) if (a[k] !== b[k]) d[k] = [a[k], b[k]];
  return d;
};
const PSEUDOS = ['::before', '::after', '::placeholder', '::marker'];

async function compareState(A, B, label, report) {
  const [a, b] = await Promise.all([A.page.evaluate(capture), B.page.evaluate(capture)]);
  const ma = new Map(a.map((x) => [x.p, x]));
  const mb = new Map(b.map((x) => [x.p, x]));
  const missing = a.filter((x) => !mb.has(x.p)).map((x) => x.p);
  const extra = b.filter((x) => !ma.has(x.p)).map((x) => x.p);
  const differing = [];
  for (const x of a) {
    const y = mb.get(x.p);
    if (!y) continue;
    const styleDiff = ['h', ...PSEUDOS].some((k) => x[k] !== y[k]);
    const boxDiff = x.r.some((v, i) => v !== y.r[i]);
    if (styleDiff || boxDiff) differing.push({ p: x.p, c: x.c, styleDiff, r: x.r, r2: y.r });
  }
  const entry = { label, elements: a.length, missing: missing.length, extra: extra.length, differing: differing.length };
  const styled = differing.filter((d) => d.styleDiff).slice(0, 40).map((d) => d.p);
  if (styled.length) {
    const [da, db] = await Promise.all([
      A.page.evaluate(capture, { paths: styled }),
      B.page.evaluate(capture, { paths: styled }),
    ]);
    const mdb = new Map(db.map((x) => [x.p, x]));
    entry.details = da.map((x) => {
      const y = mdb.get(x.p) || {};
      const det = { p: x.p, c: x.c, style: diffProps(x.s, y.s) };
      for (const k of PSEUDOS) if (x[k] || y[k]) det[k] = diffProps(x[k], y[k]);
      return det;
    });
  }
  if (differing.some((d) => !d.styleDiff)) entry.boxOnly = differing.filter((d) => !d.styleDiff).slice(0, 10);
  if (missing.length || extra.length) {
    // Live data (new audit rows) can change the structure between the two loads. Compare what
    // still can be: per tag + class list, the set of computed styles.
    entry.structure = { missing: missing.slice(0, 5), extra: extra.slice(0, 5) };
    const signature = (list) => {
      const m = new Map();
      for (const x of list) {
        const key = `${x.p.split('/').pop().split(':')[0]}.${x.c.trim().split(/\s+/).sort().join('.')}`;
        if (!m.has(key)) m.set(key, new Set());
        m.get(key).add([x.h, ...PSEUDOS.map((k) => x[k] ?? '')].join('|'));
      }
      return m;
    };
    const sa = signature(a);
    const sb = signature(b);
    entry.byClass = [...sa.keys()].filter((k) => {
      const x = sa.get(k);
      const y = sb.get(k);
      return y && (x.size !== y.size || [...x].some((h) => !y.has(h)));
    });
  }
  report.push(entry);
  const differs = differing.length || missing.length || extra.length;
  const byClass = entry.byClass ? `, by class: ${entry.byClass.length ? entry.byClass.slice(0, 3).join(' ') : 'same'}` : '';
  console.log(
    `${differs ? 'DIFF' : 'same'} ${label} (${a.length} el${differing.length ? `, ${differing.length} differ` : ''}` +
      `${missing.length + extra.length ? `, structure ±${missing.length}/${extra.length}` : ''}${byClass})`,
  );
}

const browser = await chromium.launch(process.env.STYLEDIFF_CHANNEL ? { channel: process.env.STYLEDIFF_CHANNEL } : {});
const open = async (base) => {
  const context = await browser.newContext({ viewport: { width: WIDTHS[0], height: 900 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send('DOM.enable');
  await cdp.send('CSS.enable');
  return { page, cdp, base };
};
const A = await open(BASE);
const B = COVERAGE_ONLY ? null : await open(CAND);
const SIDES = B ? [A, B] : [A];
// Read from the base build, so both builds force the same elements.
await A.page.goto(`${BASE}/login`);
const SELECTORS = await A.page.evaluate(interactionSelectors);
const report = [];
const coverage = [];
let stateIndex = -1;

async function run(url, { print = false } = {}) {
  for (const width of print ? [1000] : WIDTHS) {
    const label = `${url} @${width}${print ? ' print' : ''}`;
    if (ONLY && !label.includes(ONLY)) continue;
    stateIndex += 1;
    if (stateIndex % SHARDS !== SHARD) continue;
    if (COVERAGE) await A.page.coverage.startCSSCoverage({ resetOnNavigation: false });
    await Promise.all(
      SIDES.map(async (S) => {
        await S.page.setViewportSize({ width, height: 900 });
        await S.page.emulateMedia({ media: print ? 'print' : 'screen' });
        await S.page.goto(`${S.base}${url}`);
        await settle(S.page);
      }),
    );
    if (B) await compareState(A, B, label, report);
    if (!print) {
      for (const [name, states] of [['hover', ['hover']], ['focus', ['focus', 'focus-visible', 'focus-within']]]) {
        await Promise.all(SIDES.map((S) => forceStates(S, SELECTORS, states)));
        if (B) await compareState(A, B, `${label} #${name}`, report);
        await Promise.all(SIDES.map((S) => clearStates(S)));
      }
      const opened = await Promise.all(
        SIDES.map((S) =>
          S.page.evaluate(() => {
            const closed = [...document.querySelectorAll('details:not([open])')];
            closed.forEach((d) => {
              d.open = true;
            });
            return closed.length;
          }),
        ),
      );
      if (opened.some(Boolean)) {
        await Promise.all(
          SIDES.map(async (S) => {
            await domQuiet(S.page, 500);
            await freeze(S.page);
          }),
        );
        if (B) await compareState(A, B, `${label} #details`, report);
      }
    }
    if (COVERAGE) {
      const entries = await A.page.coverage.stopCSSCoverage();
      coverage.push(...entries.filter((e) => e.url.includes('/assets/')).map((e) => ({ text: e.text, ranges: e.ranges })));
    }
    if (!B) console.log(`covered ${label}`);
  }
}

for (const url of PUBLIC) await run(url);
await Promise.all(SIDES.map(login));
for (const url of PAGES) await run(url);
for (const url of PRINT) await run(url, { print: true });
await browser.close();

if (COVERAGE) {
  // One copy of each stylesheet's text, with the union of the ranges any state used.
  const byText = new Map();
  for (const e of coverage) {
    if (!byText.has(e.text)) byText.set(e.text, []);
    byText.get(e.text).push(...e.ranges);
  }
  fs.writeFileSync(COVERAGE, JSON.stringify([...byText].map(([text, ranges]) => ({ text, ranges }))));
}
if (B) {
  fs.writeFileSync(OUT, JSON.stringify(report, null, 1));
  const differs = report.filter((e) => e.differing || e.missing || e.extra);
  // Rows shifting under live data also move styles between paths; the per-class check covers it.
  const unexplained = differs.filter((e) => !(e.missing + e.extra > 0 && e.byClass?.length === 0));
  console.log(
    `\n${report.length} states, ${report.reduce((n, e) => n + e.elements, 0)} element comparisons; ` +
      `${differs.length} with differences, ${unexplained.length} not explained by live data -> ${OUT}`,
  );
  process.exitCode = unexplained.length ? 1 : 0;
}
