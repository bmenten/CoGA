Administrator tools sit behind admin access. **Admin**, in the top-bar menu or on the dashboard, opens
the [admin dashboard](/admin), grouped in six areas. A superuser sees five of its pages: **Gene panels**,
**Sequencing QC thresholds**, **Package import**, **Variant tags** and **Preset filters**. Every other
page is an administrator's alone.

### Reference Data

- **Species & assemblies** — the genome builds and their reference data (cytobands, genes, clinical
  CNVs, segmental duplications, DGV), with each dataset's status. Refresh the human gene information,
  rebuild the clinical-CNV knowledgebase, or upload a reference file.
- **Gene panels** — the panel catalogue: create panels, import them from PanelApp, and see their genes.
  Deleting a panel asks first, naming the panel and its version: its genes, its regions and every
  archived version go with it.
- **HPO terminology** — the HPO release in use and the SHA-256 of the file it came from; preview and
  apply a new release. The release is the one the file states. A file that states no release, or
  another than the expected release you enter, is refused.
- **Monarch data** — load the Monarch release (gene–disease and disease–phenotype links) that powers
  [phenotype matching](#phenotype-matching) and the ranking. Load it once after installation and about
  monthly; loading it also rebuilds the Mendeliome panel.
- **Repeat catalogue** — for each repeat locus, QA's record of TRGT's count there, with the reason,
  who and when, and the history of these records:
  - a **count correction**, in repeats, added to TRGT's count before every call at the locus is
    classified (−1 turns TRGT's 18 into 17; from −20 to 20). It applies whether or not the locus is
    validated, and TRGT's own count stays shown beside the corrected one. **Set count correction**
    changes it; on a validated locus a changed correction is recorded as not validated, until QA
    validates the locus with it. No locus has one until QA enters it. At FMR1 a constant correction is wrong (TRGT is
    1 to 3 low, by the number of AGG interruptions), and the page says so next to the field;
  - whether the locus is **validated**: TRGT's count, with its correction, is the clinical repeat
    number. Until it is, the repeat page marks its counts near a boundary
    ([Structural variants, repeats, Paraphase and mtDNA](#specialised-analyses)).

  Each change needs a reason and is kept in the clinical audit trail, with the record before and
  after. The loci the stored calls name are listed first.

### User & Access Management

- **Users** — accounts, how each signs in (Microsoft or a password), roles and activation, and each
  user's projects with their level there (*analyst* or *reader*).
  - A colleague's first Microsoft sign-in creates their account: a viewer in no project.
  - Set an account's role in its **Role** list; each role's tooltip says what it may do. CoGA asks you
    to confirm; you cannot change your own.
  - The roles, lowest first, each with every right of the ones before it: a **viewer** reads the
    records of their projects and changes none; an **analyst** also analyses them (reviews, tags,
    classifications, HPO terms, the case's workflow, new families); a **supervisor** also signs an
    analysis out; a **superuser** also has every project, member or not, and the gene panels, variant
    tags, preset filters, sequencing QC thresholds and package import; an **admin** has the whole
    administration. Give the supervisor role only to those the laboratory authorises to sign out.
  - Each account shows when it was added and where it stands. A sign-up, or a first sign-in through
    the organisation, *waits for approval*: these accounts come first, and every active administrator
    gets an e-mail about each when a mail server is set (the page says who, and **Send a test mail**
    checks it). Activate the account, or **Delete** it when it is not relevant; only an account that
    was never active can be deleted.
  - Deactivate an account to revoke access without deleting its history. **Archive** one that is
    not coming back: it is deactivated and leaves the list (tick **Show archived accounts** to see
    it), every record still names it, and the same person signing in again makes no new request.
    **Restore** brings it back, inactive. You cannot deactivate, archive or delete your own: another
    administrator does that.
  - Every change asks you to confirm it first, and is kept in the audit log.
  - When you deactivate an account, an import, a gene reference sync or a clinical CNV knowledgebase
    rebuild it asked for that has not started yet is not run, and a rebuild not yet loaded loads
    nothing: its job ends *failed*, saying why. The same holds once the account loses the role the job
    needs: superuser or administrator for an import (administrator for one that overwrites a family),
    administrator for the other two.
- **Projects & access** — the projects (each tied to an assembly) and which families, samples and users
  belong to them. A user sees only the data of their projects; the cohort counts in the explorers
  follow the same scope.
  - Tick a user to make them a member, and choose their level next to their name. A member added
    starts as an analyst.
  - An **analyst** reads the project's records and changes them as far as their role allows: a
    viewer changes nothing, whatever their level.
  - A **reader** (a student, say) reads, searches, exports and prints them and saves their own filter
    presets, but changes nothing and does not see the audit trail.
  - Superusers and administrators have every right in every project, member or not.
  - A case can be assigned to, or reviewed by, an analyst of its projects whose role is not viewer, a
    superuser or an administrator. A case assigned to someone who became a reader or a viewer keeps
    them, marked *(not eligible)*, until it is changed.
  - Deleting a project asks first and says what it unlinks. Its families and samples stay, but lose
    their link to it; its members lose the access it gave them; its review tags are deleted. A family
    in no other project can then be opened by superusers and administrators only.

### Data Management

- **Family & sample data** — the inventory of families and samples. Open a family's members and data,
  change its projects, download and verify the original import files, or delete a data layer, a sample
  or a family.
  - A deletion cannot be undone and is recorded in the audit log. Deleting a sample or a family is also
    written to the family's **Clinical audit trail**; a family's deletion is its last entry.
  - Deleting a family asks for its name, typed out, and the reason it goes, which its last entry
    records. Without a reason it is not deleted.
  - A data layer that more than one caller filled, such as a long-read sample's coverage from HiFiCNV,
    WisecondorX and QDNAseq, or its segments, lists each caller with its own **Delete**. That deletes
    the one caller's track and keeps the others; **Delete all** deletes every caller's.
  - Deleting a sample takes out only its own calls: the other members' small variants stay as they
    were, in every callset.
  - Deleting a sample or a family deletes CoGA's own copies of its uploaded files, never a package's
    files.
  - A file kept in a storage bucket is not downloaded here; **Verify** checks it against the bucket's
    record of it instead of its SHA-256.
  - A file recorded outside the import folders (`FAMILY_IMPORT_ROOTS`, a bucket's among them) and
    CoGA's upload folder is marked as not read, and is neither downloaded nor verified. A refused
    **Verify** says why.
- **Family statuses** — the workflow statuses analysts give a case.
- **Sequencing QC thresholds** — the warning and error cut-offs per assay behind the **Seq. QC** chip.
  None ship by default. A metric without a cut-off reads as *not assessed*, and a sample with no
  cut-off for any of its metrics shows *Not run* on the chip.
- **Package import** — import a family package (see [Case setup and data import](#case-setup)). A
  superuser imports too; only an administrator overwrites an existing family.

### Variant Configuration

- **Variant tags** — create, rename, recolour or remove custom review tags, for one project or for
  everyone. A renamed tag stays on every review and saved filter that uses it. A removed tag stays on
  the reviews that hold it, marked deleted, and can no longer be added. The built-in and ACMG tags are
  listed for reference.
- **Preset filters** — the catalogue of the small-variant presets the team uses. Presets are saved on the
  small-variant page.

### Database & Operations

- **ClickHouse tables & operations** — technical database maintenance, for the bioinformatics team.
  Each assembly shows the last result of the scheduled integrity check and when it ran;
  **Integrity check** runs one now.

### Monitoring & Audit

- **Audit logs** — every request to the server (who, what, when, the outcome) and every button and link a
  user clicked. Identifiers are masked, so the log shows how CoGA is used without exposing patient data.
  The **Flags** column marks:
  - a request for a family, sample or project outside the user's projects, which CoGA answered as for
    an unknown one;
  - a row in which a value the database cannot store was saved as an escape, naming the columns;
  - an account made at its first Microsoft sign-in, or whose role or activation changed (from and to);
  - every request of the emergency administrator's password sign-in (**Emergency sign-in**).

  The audit log is separate from each family's *Clinical audit trail* on the report.
