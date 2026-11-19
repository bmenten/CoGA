**Admin**, in the top-bar menu, or **Open admin dashboard** on the dashboard opens the
[admin dashboard](/admin), grouped in six areas. A superuser sees five of its pages: **Gene panels**,
**Sequencing QC thresholds**, **Package import**, **Variant tags** and **Preset filters**. Every other
page is an administrator's alone.

### Reference data

- **Species & assemblies** — the genome builds and their reference data (cytobands, genes, clinical
  CNVs, segmental duplications, DGV), with each dataset's status. Refresh the human gene information,
  rebuild the clinical-CNV knowledgebase, load the pinned DGV release, or upload a reference file.
  Adding GRCh37 or GRCh38 starts the clinical-CNV build and the DGV load for it. CoGA holds one
  assembly per name: importing one it holds updates it in place (tick *Replace existing cytobands and
  genes* while it holds them), and **Add assembly** refuses a name that exists.
- **Gene panels** — the panel catalogue: create panels, import them from PanelApp, and see their genes.
  A panel names each gene by its HGNC symbol or by its Ensembl gene ID (`ENSG` and eleven digits; a
  version such as `.24` is dropped). The ID is the gene's stable name: a symbol can be renamed, or read
  as another gene's. A gene added by its ID is shown after its symbol, for example
  *BRCA1 (ENSG00000012048)*, and the panel keeps its variants by the ID, by its symbol and by its
  position. Adding the ID of a gene the panel holds by its symbol replaces that symbol.
  A PanelApp search shows each panel's genes, regions and STRs by confidence level (green, amber, red).
  A panel can be imported green only, green + amber, or green + amber + red: each is a panel of its
  own, to filter on apart. An imported gene is kept by the Ensembl gene ID PanelApp gives it.
  Deleting a panel asks first, naming the panel and its version: its genes, its regions and every
  archived version go with it.
- **HPO terminology** — the HPO release in use and the SHA-256 of the file it came from, and whether
  HPO has published a newer one; preview and apply a new release, downloaded from HPO or from a file
  on the server. The release is the one the file states. A file that states no release, or another
  than the release you asked for, is refused, and Apply imports only the file the preview showed.
  A new installation starts on HPO's newest release, downloaded at its first start; when HPO
  cannot be reached it starts on the release shipped with CoGA, which the page names.
- **Monarch data** — load the Monarch release (gene–disease and disease–phenotype links) that powers
  [phenotype matching](#phenotype-matching) and the ranking. A new installation loads the newest
  release at its first start; load a newer one about monthly. Loading it here also rebuilds the
  Mendeliome panel; after the first start, build that panel on the Gene panels page.
- **Repeat catalogue** — each repeat locus with its cut-offs, which are STRchive's as the pinned
  release states them (the page names it, *STRchive v2.28.0*), what is known of TRGT's count there,
  and QA's count correction, with the reason, who and when, and the history of these corrections (a
  locus's disease links to its page on STRchive). The **count correction**, in repeats, is added to
  TRGT's count before every call at the locus is classified (−1 turns TRGT's 18 into 17; from −20 to
  20), and TRGT's own count stays shown beside the corrected one. **Set count correction** changes it.
  No locus has one until QA enters it. At FMR1 a constant correction is wrong (TRGT is 1 to 3 low, by
  the number of AGG interruptions), and the page says so next to the field. A correction does not lift
  the *Near a boundary* mark, which the repeat page shows at every locus
  ([Structural variants, repeats, Paraphase and mtDNA](#specialised-analyses)).

  Each change needs a reason and is kept in the clinical audit trail, with the record before and
  after. The loci the stored calls name are listed first.

### User & access management

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
    was never active can be deleted. With a mail server set, an account you activate (or activate
    again) gets an e-mail that it can sign in.
  - Deactivate an account to revoke access without deleting its history. **Archive** one that is
    not coming back: it is deactivated and leaves the list (tick **Show archived accounts** to see
    it), every record still names it, and the same person signing in again makes no new request.
    **Restore** brings it back, inactive. You cannot deactivate, archive or delete your own: another
    administrator does that.
  - **Edit**, under *Project access*, sets the account's projects and its level in each: the same
    memberships as a project's members on **Projects & access**, set from the account's side.
    **Remove** takes a project off; **Give access to a project** lists a few projects without access
    at a time (type part of a name to find the others), and a project added starts as an analyst.
    The dialog lists what the save changes; **Save project access** saves it all at once. An
    administrator's or a superuser's row says it has every project, member or not.
  - Every change asks you to confirm it first, and is kept in the audit log.
  - When you deactivate an account, an import, a gene reference sync or a clinical CNV knowledgebase
    rebuild it asked for that has not started yet is not run, and a rebuild not yet loaded loads
    nothing: its job ends *failed*, saying why. The same holds once the account loses the role the job
    needs: superuser or administrator for an import (administrator for one that overwrites a family),
    administrator for the other two.
- **Projects & access** — the projects (each tied to an assembly) and which families, samples and users
  belong to them. A user sees only the data of their projects; the cohort counts in the explorers
  follow the same scope.
  - The members are listed with their level; **Remove** takes one off. Under them, **Add a member**
    lists a few users without access at a time: type part of a name or email to find the others, then
    **Add** one (or press Enter when the search finds one user). A member added starts as an analyst.
    One user's projects are also set on the **Users** page.
  - An **analyst** reads the project's records and changes them as far as their role allows: a
    viewer changes nothing, whatever their level.
  - A **reader** (a student, say) reads, searches, exports and prints them and saves their own filter
    presets, but changes nothing and does not see the audit trail.
  - Superusers and administrators have every right in every project, member or not.
  - A case can be assigned to, or reviewed by, an analyst of its projects whose role is not viewer, a
    superuser or an administrator. A case assigned to someone who became a reader or a viewer keeps
    them, marked *(not eligible)*, until it is changed.
  - A project that families are linked to cannot be deleted: **Delete project** is off and says how
    many. A family's small and structural variants are stored per project, and would stay behind
    under the deleted one. First move each family to other projects, or to none, or delete it, in
    **Admin → Family & sample data**; its variants move with it.
  - Deleting a project without families asks first and says what goes: its members lose the access
    it gave them; its review tags are deleted as a removed tag is: each stays on the reviews that
    hold it, marked deleted, and can no longer be added.

### Data management

- **Family & sample data** — the inventory of families and samples. Open a family's members and data,
  change its projects, download and verify the original import files, or delete a data layer, a sample
  or a family.
  - A family's members are edited in its **Family members** table, the same as on the family page:
    **Edit members**, then **Save changes** (see [Family workspace](#family-workspace)).
  - A family's small and structural variants move with its projects: each project it is added to
    gets them, and a project it leaves no longer holds them. A project on another assembly than
    the family's variants is refused. **Save project access** waits while the family is being
    imported, and takes longer for a family with many variants.
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
  - A file recorded outside the family's own package folder (the folder its latest import read,
    on disk, on a share or in a mounted bucket) and CoGA's upload folder is marked as not read, and
    is neither downloaded nor verified. A refused **Verify** says why.
- **Family statuses** — the workflow statuses analysts give a case.
- **Sequencing QC thresholds** — the warning and error cut-offs per assay behind the **Seq. QC** chip.
  None ship by default. Each metric of an analysis is judged by the cut-off in the analysis's own
  profile, else in the profile the family names, else in the default profile. A metric without a
  cut-off reads as *not assessed*, and a sample with no cut-off for any of its metrics shows *Not run*
  on the chip.
- **Package import** — check, correct and approve the deliveries, and follow their imports (see
  [Case setup and data import](#case-setup)). A superuser imports too; only an administrator
  overwrites an existing family.

### Variant configuration

- **Variant tags** — create, rename, recolour or remove custom review tags, for one project or for
  everyone. A renamed tag stays on every review and saved filter that uses it. A removed tag stays on
  the reviews that hold it, marked deleted, and can no longer be added. The built-in and ACMG tags are
  listed for reference.
- **Preset filters** — the catalogue of the small-variant presets the team uses. Presets are saved on the
  small-variant page.

### Database & operations

- **ClickHouse tables & operations** — technical database maintenance, for the bioinformatics team.
  Each assembly shows the last result of the scheduled integrity check and when it ran;
  **Integrity check** runs one now.

### Monitoring & audit

- **Audit logs** — every request to the server (who, what, when, the outcome) and every button and link a
  user clicked. Identifiers are masked, so the log shows how CoGA is used without exposing patient data.
  The **Flags** column marks:
  - a request for a family, sample or project outside the user's projects, which CoGA answered as for
    an unknown one;
  - a row in which a value the database cannot store was saved as an escape, naming the columns;
  - an account made at its first Microsoft sign-in, or whose role or activation changed (from and to);
  - every request of the emergency administrator's password sign-in (**Emergency sign-in**).

  The audit log is separate from each family's *Clinical audit trail* on the report.
