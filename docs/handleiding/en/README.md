# CoGA — Codebase manual

This manual explains the codebase of CoGA (*Comprehensive Genomic Analysis*) chapter by chapter. It exists in Dutch (the manual's main folder) and in English (its folder `en/`), with the same chapters. It is written for the **review board and auditors**, including readers who know little Python or TypeScript: every technical term is explained briefly, and every topic says **where in the code** it lives.

CoGA runs as an **in-house IVD under IVDR Article 5(5)** at CMGG (ISO 15189). The regulated part runs from the *annotated VCF file* to the *closed, frozen analysis in CoGA*. The clinical report is drafted and medically signed outside the device, in the EPD (the hospital's electronic patient record). It refers 1:1 to that analysis. The common thread is therefore **explainability, traceability and safety**, each time with the place in the code where a check, a log line or an access restriction is enforced.

> The data in the system is **synthetic**; there is no real patient data.

## Which code does this manual describe?

The manual describes the current `main`. When a PR changes behaviour described here, the same PR updates the chapter in both languages and says so in its description (from the first release candidate on, also in its CHANGELOG line). The CI check `scripts/check-handleiding-sync.sh` makes sure the web page matches the Markdown. If you review a fixed version, note the release tag or the commit you are reading. Where they differ, the code comes first, then `docs/regulatory/`, then `docs/`.

## How to read this manual

- **Paths** are in `monospace`, relative to the root folder of the repository (e.g. `backend/app/routers/auth.py`). Functions and endpoints are named, never given with a line number.
- A **"Where in the code"** note leads to the source.
- Every chapter ends with a **"Key files"** table.
- Where the manual summarises the other documentation or the technical file, it names the source: a path (e.g. `docs/application-scheme.md`) or a page in the app (e.g. *Docs → Sample-integrity QC*). In the web version such references are plain text; only the references between chapters are links.
- The chapters can be read on their own. Read them in the order 1 → 15 the first time.

## Web version

There is also a web page with all chapters on one page, in both languages: [`coga-handleiding.html`](../coga-handleiding.html). At the top, the **NL | EN** button chooses the language; the page remembers that choice and stays at the chapter you are reading. Without an earlier choice it follows the browser's language preference. It has a table of contents in the sidebar, a light and a dark theme and a print view for PDF, which prints only the chosen language. Open the file directly in a browser; it needs nothing external. The sidebar shows the product version from `VERSION`.

[`build_site.py`](../build_site.py) builds the page from exactly these Markdown files. First it compares every English chapter with the Dutch one: the same headings, tables (with as many rows), paragraphs, list items and code blocks, the same `code` references and the same references to other chapters. If they differ, the script writes nothing and lists the differences. A change made in one language only is caught this way as soon as it touches a heading, a table row, a paragraph, a list item or a code reference; the script does not compare the meaning of the sentences themselves.

```bash
pip install markdown
python docs/handleiding/build_site.py
```

If you update a chapter, do it in both languages. Then build the page again, also when `VERSION` changes, and commit it along. Use the same version of `markdown` as CI (the *catalogue* job in `.github/workflows/ci.yml`), so the output matches.

## Contents

| # | Chapter | What it covers |
| --- | --- | --- |
| 1 | [General architecture & structure](01-architecture.md) | The three layers, the path of one request, the folders, the configuration and the technology. The map for the rest. |
| 2 | [User roles, permissions & isolation](02-security-roles-permissions.md) | Roles, project-scoped access, enforcement in the backend, the restricted database role, security headers, the refusal of weak secrets and the protection of uploads. |
| 3 | [Database structures (Postgres & ClickHouse)](03-database-structures.md) | Why two databases, how the tables are grouped, how ClickHouse rows are linked to Postgres and the guarantees in the database. |
| 4 | [Initial deployment & seeding](04-deployment-and-seeding.md) | From nothing to a running platform: the schema, the first administrator, the reference data, the start-up order, the import worker processes and the roll-out on Google Cloud. |
| 5 | [Login & authentication](05-login-authentication.md) | Signing in with Microsoft (Entra ID / Azure AD), through CAS and as emergency administrator, logging in with a password, tokens, the session in the browser, rate limiting, registration and the mails about an account. |
| 6 | [Package import — manifest, checks, traceability](06-import-pipeline.md) | Reading a family package in safely: deliveries from watch folders and their approval, manifest, dry-run, where the data ends up and the provenance records. |
| 7 | [Backend: routers & services in detail](07-backend-routers-and-services.md) | The router → service → storage pattern, the safety rules for database queries, all routers and the request logging. |
| 8 | [Filter pages ↔ API](08-filter-pages-and-api.md) | The chain from filter to display for small and structural variants, and a summary of mtDNA and sample QC, Paraphase, repeat expansions, monogenic NIPT and PGT. |
| 9 | [Visualisations (chromosome, genome, circos, IGV)](09-visualisations.md) | The genome overview, the per-chromosome view, Circos, IGV and the tracks. |
| 10 | [Variant tagging & semi-automatic ACMG classification](10-tagging-and-acmg-classification.md) | Tags and the semi-automatic ACMG classification (small variants, mtDNA and CNVs), the frozen evidence and drift. |
| 11 | [Report & full traceability](11-report-and-traceability.md) | Signing out, the gates, the frozen snapshot, the hash-chained audit trail and the integrity anchors. |
| 12 | [HPO, Monarch & variant prioritisation](12-hpo-monarch-prioritisation.md) | Phenotypes, Monarch, semantic similarity, variant prioritisation and the ranking cache. |
| 13 | [Gene Explorer & version control](13-gene-explorer.md) | The gene profile, the sources of the gene information and the version control of reference data. |
| 14 | [Variant Explorer](14-variant-explorer.md) | Variants across all accessible projects, with emphasis on the isolation between projects. |
| 15 | [Other modules & administration](15-other-modules-and-admin.md) | The CNV knowledge base, gene panels, the administration functions, the in-app documentation, releases and UI telemetry. |

## How this manual was written

Every chapter was written from the source code and the project documentation. Every reference to a file, function or table was checked in the code. A summary of `docs/` or `docs/regulatory/` names its source (see *How to read this manual*). The English chapters are a translation of the Dutch ones, with the same structure and the same references.
