# The package file `coga-package.json`

A pipeline that delivers a package to CoGA describes it in one file, `coga-package.json`, at the
top of the package folder. The file says which run made the package, where it belongs (the
project, the assembly, the analysis), who is in the family, which datasets the import reads, and
every file with its checksum. It holds what a PED and a manifest (`manifest.yaml`) give the import
of a typed folder, and says itself when the package is complete.

This page is for the people who maintain the pipelines. It defines the format `coga-package/1`.
The format is fixed in code, in [coga_package.py](../backend/app/services/coga_package.py), which
CoGA reads the file with. Its JSON Schema is
[schemas/coga-package-1.schema.json](schemas/coga-package-1.schema.json), and
[schemas/examples/](schemas/examples/) holds a synthetic example of each kind of package.

**Where the file is read.** The watched import folders read `coga-package.json`
([data-import.md](data-import.md#watched-import-folders)); what a minimal file leaves out, CoGA
proposes from the folder, as Discover does. A delivery can be completed and corrected before it is
approved, within the limits below, each change with its reason on the record: through the API
([data-import.md](data-import.md#correcting-a-delivery)) and **Correct…** in **Deliveries**; the
family page (*Corrected at intake*) and every signed record (*Package intake*) list the
corrections. Adding a family by hand makes the same package: CoGA drafts it from the folder, a PED
if there is one, and what the person adds, and keeps it with a delivery of origin `manual`, which
goes through Deliveries like a pipeline's
([data-import.md](data-import.md#adding-a-folder-by-hand)). CoGA computes no checksums for it: the
import records each file's SHA-256 (the owner's decision of 2026-11-10).

**Still to do** (the owner's decisions of 2026-11-10): the demo data as package files; a bucket
mounted as a local folder.

## Writing the file

- **Name and place**: `coga-package.json`, in the package folder itself. JSON in UTF-8: one
  object, no key given twice in an object, no `NaN` or `Infinity`. At most 20 MB.
- **The package folder goes into the folder of its project** in the watched import folder:
  `<watched folder>/<project>/<package>`, the project folder named exactly as the `project` the
  file names. A package directly in the watched folder, or in the folder of another project,
  needs attention.
- **Whole, every time.** Write the file under a temporary name in the same folder
  (`.coga-package.json.tmp`) and rename it over `coga-package.json`. A reader then never sees half
  a file.
- **`status` is the go-ahead.** CoGA offers a package for import only when its file says
  `"status": "complete"`. The file may be there before that:
  - `running`: the run is under way. Write it when the run starts. It is optional, but a run
    into a folder delivered before then tells CoGA at once that the folder is changing: a
    delivery of that folder that no one has decided on yet is superseded, and no delivery is
    made until the file says `complete`.
  - `complete`: the last step of the run, once every output file is in place in the folder, with
    `files` listing them. In Nextflow, write it from `workflow.onComplete` or after the step that
    copies the outputs, not from a process whose outputs `publishDir` still copies in the
    background.
  - `failed`: the run stopped. CoGA offers nothing, and supersedes an undecided delivery of the
    folder as for `running`.
- **Copy the outputs, do not link them** (`publishDir mode: 'copy'`): CoGA reads no file through
  a link.
- **Check the file before writing it**, against the JSON Schema: with
  `check-jsonschema --schemafile coga-package-1.schema.json coga-package.json`, or the
  pipeline's own schema plugin. CoGA checks more than the schema says (below).

A complete file whose files have not all arrived (a copy that runs on) is not lost: CoGA sees a
file missing, or of another size, and waits for the next look.

## The fields

Every file holds `schema`, `status`, `written_at` and `pipeline`. A complete one also holds
`project`, `assembly`, `analysis_type`, `family` and at least one entry in `files`. A field the
format does not know is refused: a misspelt field (`clinical_staus`) would otherwise leave a
member's status unset without a word. Information of your own goes under `extra`.

| Field | Required | What it holds | Given by |
| --- | --- | --- | --- |
| `schema` | always | `coga-package/1` | pipeline |
| `status` | always | `running`, `complete` or `failed` | pipeline |
| `written_at` | always | when the file was written, with its time zone (`2026-11-10T15:42:10+01:00`) | pipeline |
| `pipeline` | always | `name`, `version` and `run_id`: one `run_id` per run, the same in each file it writes | pipeline |
| `project` | complete | the CoGA project, by its name; it fixes the assembly | samplesheet |
| `assembly` | complete | the reference assembly (`GRCh38`); must be the project's | pipeline |
| `analysis_type` | complete | `wgs`, `wgs_trio`, `pgt_m`, `monogenic_nipt`, `long_read_wgs` or `long_read_couple` | samplesheet |
| `family` | complete | `family_id`; `roi`, the region of interest (a gene or `chr7:1000-2000`); for PGT-M `pgt` (below) | samplesheet |
| `members` | no | every member of the family, the pedigree (below) | pipeline and laboratory |
| `couples` | no | each couple: `partners` (two members) and `context` (`reproductive`, `carrier screening`) | pipeline |
| `datasets` | no | what the import reads (below) | pipeline |
| `pipeline_used_pedigree` | no | the datasets the pipeline computed with the pedigree or the members' sex (below) | pipeline |
| `files` | complete | every file of the package: `path` (in the folder, with `/`), `size` in bytes, and `sha256` or `md5` | pipeline |
| `qc` | no | the pipeline's QC: `status` (`pass`, `warn`, `fail`) and its `report`, a file of the package | pipeline |
| `request_id` | no | the laboratory's request | samplesheet |
| `reference` | no | the reference genome the run used (its FASTA's name) | pipeline |
| `notes` | no | free text, shown with the delivery | pipeline |
| `extra` | no | anything else, kept with the package and never read | pipeline |

The pipeline does not know everything the import needs. The project, the analysis and the request
come from the laboratory, through the samplesheet. The clinical status of the members, their HPO
terms and the PGT inheritance model are the laboratory's and the clinic's: a pipeline may give
them, and otherwise they are added in CoGA before the import.

### Members

`members` is the only place the file describes a member: it holds what the PED's rows and the
manifest's `family.members`, `family.add_members` and `individuals` give a typed folder's import.
List every member, also one without data of their own (a fetus).

| Field | Required | What it holds |
| --- | --- | --- |
| `sample_id` | yes | the sample's ID: printable, no spaces, no `/` or `\`, not `.` or `..` |
| `sex` | yes | `male`, `female` or `unknown` |
| `father`, `mother` | no | members; a father is not female and a mother not male |
| `role` | no | `proband`, `father`, `mother`, `sibling`, `embryo` or `relative` |
| `clinical_status` | no | `affected`, `unaffected` or `unknown` (left out: unknown) |
| `carrier_status`, `carrier_type` | no | `carrier`, `not_carrier` or `unknown`; `obligate`, `proven`, `reported` or `inferred` |
| `assay`, `assay_panel` | no | the sample's assay where it is not the family's (`nipt_cfdna` for a maternal plasma) and its capture panel |
| `hpo` | no | `present` and `absent`: HPO terms (`HP:0001250`) |
| `related_to` | no | members this one is related to by an unknown degree (a PGT index on the mother's side) |

For PGT-M, `family.pgt` gives the `inheritance_model` (`AD`, `AR`, `XLD`, `XLR` or
`mitochondrial`), the `affected_parents` (whose condition the test is for) and the `indexes` (the
relatives the risk haplotype is read from). The import derives their statuses from the model, as
it does for a manifest ([data-import.md](data-import.md#the-manifest)).

### Datasets

`datasets` is the manifest's `datasets` block, unchanged: one entry per dataset the import reads,
keyed `snv`, `sv_needlr`, `repeats_trgt`, `wisecondorx`, `qdnaseq`, `apcad`, `pcf`, `haplotypes`,
`paraphase`, `coverage`, `cnv`, `mito`, `alignments`, `qc` or `pipeline_info`. What each entry
holds, and where it lands, is in [data-import.md](data-import.md#the-manifest). Its paths are
relative to the package folder, and every file an entry names is listed in `files`.

### The datasets the pipeline computed with the pedigree

`pipeline_used_pedigree` lists the datasets the pipeline computed with the pedigree or the
members' sex: a callset phased with the PED (`whatshap --ped`), a trio filter, APCAD, the
haplotypes. The pipeline knows this; CoGA cannot tell from the files. It matters when someone
corrects the pedigree in CoGA: the correction does not change what the pipeline already computed
with the old one, so imported haplotypes would contradict the recorded pedigree. Such a change is
then refused until those datasets are left out of the import or the pipeline runs again (below).

### A minimal file and a full one

A pipeline can start with a **minimal** file: the fields every file holds, and what a complete
one needs (where the package belongs and its files)
([schemas/examples/minimum.json](schemas/examples/minimum.json)). CoGA then proposes the members
(from the pipeline's PED, if the folder has one) and the datasets (from the folder's layout), for
the family the file names, and the delivery warns that it did (`package_members_proposed`,
`package_datasets_proposed`): a person checks them before approving. A **full** file adds `members`, `couples`, `datasets` and
`pipeline_used_pedigree`: nothing is proposed, and the pipeline says what it did. Each pipeline
moves from minimal to full at its own pace.

## What CoGA checks

Reading the file ([coga_package.py](../backend/app/services/coga_package.py)):

- it is one JSON object of the format, at most 20 MB, with no key twice, no `NaN` or `Infinity`,
  and no NUL or lone surrogate, which no stored text can hold;
- every field is one the format knows, of the right kind; `written_at` has its time zone;
- an ID follows the ID rule, and a path stays in the folder (relative, `/` between its parts, no
  `..`, no leading `~`);
- a complete file says where the package belongs and lists its files, each once, each with a
  checksum, and not `coga-package.json` itself;
- every member the file names (a parent, a partner, a relative, an affected parent, an index, a
  dataset's per-sample entry) is one of its `members`, listed once; a parent is another member,
  of the parent's sex where it is known;
- every dataset in `pipeline_used_pedigree` is one of the file's datasets.

Against the folder and CoGA, when the import watcher reads the file
([data-import.md](data-import.md#watched-import-folders)): every listed file is there with its
size and checksum, the pipeline's QC, the project and its assembly, a PED in the folder against
the members (a warning when it says otherwise: the pipeline's tools may have read it), the
package's validation with the manifest made from the file, every file the import reads listed,
and the family and samples CoGA already holds.

## What can be changed in CoGA

The pipeline's file is never changed: CoGA keeps its bytes and their SHA-256. A change made in
CoGA is a revision kept beside it (`import_delivery_revisions`, append-only), with its reason, who
made it and when, and on the import inbox's audit chain, and the import reads the package with
the changes ([data-import.md](data-import.md#correcting-a-delivery)). What CoGA proposed for a
minimal file is written into the package first, and is corrected the same way. The family's
record keeps the file's checksum, the revision, the SHA-256 of the package imported and every
change. The signed record lists every change made at intake (*Package intake*, a change the
pipeline used marked), and the family page the same (*Corrected at intake*).

| Kind of field | Fields | In Deliveries |
| --- | --- | --- |
| Fixed: the pipeline's | `schema`, `pipeline`, `assembly`, `family.family_id`, every `sample_id`, `files`, `qc` | Not changed. A wrong value is a refused delivery and a new run or copy. |
| Used by the pipeline | `sex`, `father`, `mother`, `couples` | Changed with a reason; refused while a dataset in `pipeline_used_pedigree` is in the import. Leave it out, or run the pipeline again. |
| The laboratory's | `project` (of the same assembly), `analysis_type`, `role`, `clinical_status`, the carrier fields, `hpo`, `related_to`, `family.roi`, `family.pgt`, a dataset in or out of the import and its options (`vcf_sample`, `source_format`, `exclude_filters`, a coverage `content`, another listed file) | Changed with a reason. |

The owner's decisions of 2026-11-10:

- The go-ahead is the file's `status`, set to `complete` as the run's last step; the file may be
  written earlier.
- Sample IDs and the family ID are never changed in CoGA: a wrong one is a refused delivery. The
  project may be changed, with a reason, to one of the same assembly.
- A change of the pedigree or a sex that the pipeline used is refused while a dataset it computed
  with them is in the import.
- The person who changes a delivery may approve it; both are on the record, and the Sample QC at
  sign-out remains the check of the pedigree and the samples' identity.
- The file is JSON, not YAML (a YAML reader turns an ID such as `012345`, `NA` or `no` into a
  number, nothing or false without a word), and a minimal file is accepted while the pipelines
  move to a full one.

## Examples

All synthetic, with checksums of nothing real:

- [running.json](schemas/examples/running.json): written as a PGT run starts.
- [minimum.json](schemas/examples/minimum.json): a short-read trio with only what a complete file
  needs; CoGA proposes the rest.
- [pgt-m.json](schemas/examples/pgt-m.json): an nf-cmgg/copgtm run: a couple, two embryos and an
  index related to the mother, with the PGT model and the datasets the pipeline computed with the
  pedigree.
- [monogenic-nipt.json](schemas/examples/monogenic-nipt.json): a monogenic NIPT pair: the maternal
  plasma, the father and the fetus, with each parent's callset and coverage table.
- [long-read-couple.json](schemas/examples/long-read-couple.json): a couple screened for
  carriership on the long-read pipeline, with each partner's own callsets.

After a change to the format's model, write the schema again with
`python scripts/generate-coga-package-schema.py`; the unit test
[test_coga_package.py](../backend/tests/test_coga_package.py) fails while it is out of date, and
reads every example.
