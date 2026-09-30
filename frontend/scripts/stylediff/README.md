# Style diff: does a CSS change render as intended?

`stylediff.mjs` renders two production builds of the frontend side by side, against the same
backend and data, and compares what the browser computes. For a clean-up the answer must be
"nothing renders differently". It verified the CSS clean-up of #712 (the PRs linked there).

**What it compares.** It covers ~110 routes (`states.mjs`): every app, admin and docs page, and
every family page for three families. Each route is rendered at three widths, and every element
is keyed by its structural DOM path. For each element it compares:

- the full computed style (custom properties aside: they only reach the rendering through the
  properties that read them);
- the computed style of its `::before`, `::after`, `::placeholder` and `::marker`;
- its page-level box.

Each state is also compared with `:hover` and with `:focus`/`:focus-visible`/`:focus-within`
forced through the DevTools protocol. The forcing applies to every element the base build's
stylesheet styles that way. States are further compared with every `<details>` open, and the
three report pages in print media.

**Determinism.** Before each capture the harness waits for the DOM to stay quiet for 800 ms, and
finishes every transition and animation, holding spinners at t=0. Live data can still change the
structure between the two loads (the audit-log pages gain rows). For such a state it compares, per
tag and class list, the set of computed styles instead.

## Set-up

1. **Datastores.** Use throwaway Postgres 16 and ClickHouse 26.8, at the image digests
   `.github/workflows/ci.yml` pins, or the dev stack. Start from an empty database.
2. **Seed.** From the repo root, with the backend's environment for those datastores
   (`APP_ENV=test`):

   ```bash
   RUN_INTEGRATION=1 python scripts/seed_playwright_e2e.py   # golden trio + the e2e user
   RUN_INTEGRATION=1 python scripts/seed_style_diff_demo.py  # NIPT demo + demo quartet
   ```

3. **Backend.** Start it on a free port, with the same environment and the start-up bootstrap
   loads off (the seeded data is all the pages need):

   ```bash
   REFERENCE_BOOTSTRAP_ENABLED=false HPO_BOOTSTRAP_ON_STARTUP=false \
   GENE_REFERENCE_BOOTSTRAP_ON_STARTUP=false \
   python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8011
   ```

4. **Two builds, each behind the production server.** Build the base (e.g. `main`) and the
   candidate into separate folders. Serve each with a copy of `server.mjs`, which serves the
   `dist` next to it with its CSP and `/api` proxy. From `frontend/`, for each build (`base` on
   4181, `cand` on 4182):

   ```bash
   npx vite build --outDir /tmp/stylediff/base/dist --emptyOutDir
   cp server.mjs proxyLocation.mjs proxyRequest.mjs securityHeaders.mjs /tmp/stylediff/base/
   ln -sfn "$PWD/node_modules" /tmp/stylediff/base/node_modules
   echo '{"type":"module"}' > /tmp/stylediff/base/package.json
   (cd /tmp/stylediff/base && PORT=4181 BACKEND_URL=http://127.0.0.1:8011 node server.mjs)
   ```

   `server.mjs` reads `index.html` once at start-up. Restart it after rebuilding.

## Run

```bash
cd frontend
node scripts/stylediff/stylediff.mjs --base http://127.0.0.1:4181 --cand http://127.0.0.1:4182 \
  --out /tmp/stylediff/report.json
```

- A full run is about 920 states, which takes a while. `--shard k/n` splits the states, so four
  processes (`--shard 0/4` … `3/4`) run in parallel.
- `--only <text>` limits the run to the states whose label contains it (`--only /report`).
- `--widths 1440,1000,700` sets the widths.
- `STYLEDIFF_CHANNEL=chrome` uses an installed Chrome, with a throwaway profile, when Playwright's
  own browser is not installed.
- The exit code is 1 when a difference is not explained by live data.

**Reading the report.** Every state prints `same` or `DIFF`. A `DIFF` lists how many elements
differ, and the JSON report holds the property-level changes of up to 40 elements per state
(`details`). A `DIFF … structure ±n/m, by class: same` state changed only because live data added
or removed rows.

**Before trusting a zero,** inject a one-declaration change into the candidate's built CSS. The
run must then report exactly the elements it affects.

## CSS coverage

`--coverage coverage.json` records, from the base build, the byte ranges of the stylesheet that
matched in any state. `--coverage-only` renders the base build alone for that. A rule the static
analysis calls dead must never appear in those ranges. The coverage is per rule, so a dead
selector that shares a list with a live one does show up.
