Administrator tools sit behind admin access. **Admin**, in the top-bar menu or on the dashboard, opens
the [admin dashboard](/admin), grouped in six areas.

### Reference Data

- **Species & Assemblies** — the genome builds and their reference data (cytobands, genes, clinical
  CNVs, segmental duplications, DGV), with each dataset's status. Refresh the human gene information,
  rebuild the clinical-CNV knowledgebase, or upload a reference file.
- **Gene Panels** — the panel catalogue: create panels, import them from PanelApp, and see their genes.
- **HPO Terminology** — the HPO release in use; preview and apply a new release.
- **Monarch Data** — load the Monarch release (gene–disease and disease–phenotype links) that powers
  [phenotype matching](#phenotype-matching) and the ranking. Load it once after installation and about
  monthly; loading it also rebuilds the Mendeliome panel.

### User & Access Management

- **Users** — accounts, roles and activation. Activate a new account here; deactivate one to revoke
  access without deleting its history.
- **Projects & Access** — the projects (each tied to an assembly) and which families, samples and users
  belong to them. A user sees only the data of their projects, and the cohort counts in the explorers
  follow the same scope.

### Data Management

- **Family & Sample Data** — the inventory of families and samples: open a family's members and data,
  change its projects, download and verify the original import files, or delete a data layer, a sample
  or a family (this cannot be undone and is recorded in the audit log). Deleting a sample takes out only
  its own calls: the other members' small variants stay as they were, in every callset. A file kept in a
  storage bucket is not downloaded here; **Verify** checks it against the bucket's record of it instead of
  its SHA-256.
- **Family Statuses** — the workflow statuses analysts give a case.
- **Sequencing QC Thresholds** — the warning and error cut-offs per assay behind the **Seq. QC** chip.
  None ship by default; a metric without a cut-off reads as *not assessed*.
- **Package Import** — import a family package (see [Case setup and data import](#case-setup)).

### Variant Configuration

- **Variant Tags** — create, rename, recolour or remove custom review tags, for one project or for
  everyone. The built-in and ACMG tags are listed for reference.
- **Preset Filters** — the catalogue of the small-variant presets the team uses. Presets are saved on the
  small-variant page.

### Database & Operations

- **ClickHouse Tables & Operations** — technical database maintenance, for the bioinformatics team.
  Each assembly shows the last result of the scheduled integrity check and when it ran;
  **Integrity check** runs one now.

### Monitoring & Audit

- **Audit Logs** — every request to the server (who, what, when, the outcome) and every button and link a
  user clicked. Identifiers are masked, so the log shows how CoGA is used without exposing patient data.
  It is separate from each family's *Classification audit trail* on the report.
