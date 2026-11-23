Choose the way in that matches how your data arrives.

```cards
Family builder | Build a new family by hand: add samples, set sex and roles, link parents and couples, and set affected and carrier status. Open to the analysts of a project whose role is analyst or higher, who add families to it, and to superusers and administrators; uploading a PED file here is for administrators.
Package import | A superuser or an administrator checks, corrects and approves the family folder a pipeline delivers to the CoGA server, or adds a folder by hand, and imports the family with all its data in one job.
```

- **Package import is for superusers and administrators.** The rest of loading data is for
  administrators: single uploads on the **Upload family and sample data** page, and replacing an
  existing family, by a package import with *Overwrite* too. So is editing an existing family's members
  or structure. HPO terms are added, changed and removed by the analysts of the family's project (role
  analyst or higher), by superusers and by administrators.
- For a **monogenic NIPT** case, tick *Monogenic NIPT (cfDNA from maternal plasma)* in the family builder
  and give the **Pregnancy ID**: see [Monogenic NIPT](#monogenic-nipt). A family may hold several
  pregnancies of one woman, each pregnancy's NIPT an analysis of its own.
- Each kind of data switches on part of CoGA:
  - small variants: the small-variant page and Sample QC;
  - structural variants: the SV page;
  - TRGT calls: the repeat page;
  - Paraphase: the paralogue page;
  - mitochondrial calls: the mtDNA analysis;
  - coverage, segments, APCAD and haplotypes: the viewer tracks.
  - Any variant data also switches on the variant summary.

> **Reference data first.** Genes, cytobands, ClinVar and the other annotation layers are loaded per
> assembly. If a viewer cannot place coordinates or a gene search is empty, check that the reference
> data for the family's assembly is loaded.

### Importing a package

The **Package import** page lists under **Deliveries** the family folders the pipelines delivered,
each with its package file (`coga-package.json`), checked by CoGA. Nothing is imported until you
approve it.

1. Read the delivery's **State** (*Ready*, *Needs attention*, *Still copying*) and its warnings; a NIPT
   delivery names its pregnancy (*· pregnancy ZW1*). **Add a folder by hand** adds a folder no pipeline
   delivered (for a NIPT, give its **Pregnancy ID**).
2. **Correct…** completes or corrects what the package says (the project, the analysis, a NIPT's
   pregnancy, the members, the datasets), with a reason.
3. **Dry run** checks it as an import would, without writing anything. **Approve…** imports it, or tick
   several ready ones and press **Approve selected**. For a family that exists already, choose *Update: add the
   missing datasets and members* or, as an administrator, *Overwrite: replace its datasets and
   pedigree*. **Refuse…** sets a delivery aside, with a reason.
4. Follow the job under **Recent family imports**: the latest 10 jobs, up to 100 on **Show all**. A
   delivery whose import failed is offered again: checked again, it waits among the deliveries, naming
   the failed job; once the cause is fixed, approve it again (with *Update* when the failed import kept
   the family).

CoGA keeps watching the folder after the import. A folder that changes, or is gone, is listed as
*Changed after import* (on one row, the folder's new delivery's once there is one), and every page of
the family warns of it (*Package folder changed after
import* or *Package folder gone*); signing out meanwhile asks for a reason. See
[Family report and sign-out](#clinical-report) for what ends the warning. A file put into the folder
later that the import reads, such as a CRAM, is listed by CoGA in the new delivery's package itself,
with a warning: check it before you approve.

[Data import reference (who may do what, reference data, package steps and checks)](/docs/reference/data-import "further-reading")
