# CoGA overview deck

A seven-slide, public-facing overview of CoGA — a CoGA-branded counterpart to the
Dartmouth **AUGMET** deck, intended for introducing the platform to a broad audience.

Content is drawn from the codebase and the [handleiding](../handleiding/) and was
verified against source (the device boundary, tool names, datastores, knowledge
sources and roadmap all trace to `README.md`, `docs/ROADMAP.md`, the handleiding
chapters and the regulatory framing — CoGA is an in-house IVD under IVDR Art 5(5)).

## Files

| File | What it is |
|------|------------|
| `coga-overview-deck.html` | The deck source — a self-contained HTML file, 7 × 16:9 frames. Light/dark theme-aware. Open in a browser; **Print → Save as PDF** (landscape) for an editable export. |
| `CoGA-overview-deck.pdf` | Pre-rendered 7-page 16:9 PDF (light theme), one slide per page. |
| `slides/coga-NN-*.png` | Pre-rendered 2608×1468 (2×) PNGs — drag straight onto PowerPoint slides. |
| `slides/coga-NN-*-dark.png` | Dark-theme variants of each slide. |
| `render.mjs` | Regenerates the PNGs from the HTML (see below). |
| `render-pdf.mjs` | Regenerates the PDF from the HTML. |

## Slides

| # | Slide | AUGMET origin |
|---|-------|---------------|
| 00 | Title — CoGA wordmark + DNA-backbone motif | (new cover) |
| 01 | Instrument-agnostic ingestion | *Chemistry & Sequencer Agnostic* |
| 02 | One case, end to end (architecture) | *Bi-directional interface informatics* |
| 03 | Signed, traceable report | *Simplified reporting & case distribution* |
| 04 | What CoGA does (capability tree) | *Genomics* test-menu tree |
| 05 | Integrated knowledge for clinical interpretation | *Integrated Databases* logo wall |
| 06 | Where CoGA is heading (roadmap) | *Future RoadMap* |

## Design

Palette sampled from the CoGA logo — crimson **C** `#D0103A`, violet **G** `#4E3E82`,
teal **A** `#008A93` over charcoal `#1E2A38`. The C/G/A triad colour-codes the
capability/knowledge domains; a DNA-wave motif echoes the logo. Every slide carries
the IVDR Art 5(5) scope line. No external logos or fonts are used (self-contained,
system font stack + a monospace utility face for coordinates/hashes/versions).

## Regenerate

Requires the Playwright/Chromium already installed under `frontend/node_modules`
(the scripts resolve it relative to this folder). From this directory:

```bash
node render.mjs        # -> slides/coga-*.png        (light)
node render.mjs dark   # -> slides/coga-*-dark.png   (dark)
node render-pdf.mjs    # -> CoGA-overview-deck.pdf
```

Edit `coga-overview-deck.html` and re-run to update all outputs. Each `.slide`
element uses container-query units, so the frames scale cleanly at any width.
