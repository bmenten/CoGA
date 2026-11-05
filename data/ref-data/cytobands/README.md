# Cytobands — shipped backups of UCSC's tables (version-pinned)

Adding a human assembly from UCSC, at the first start (GRCh38) or by an administrator
(Administration → Species & assemblies), loads its cytobands from **UCSC's table as published
that day**, and records the file by name and SHA-256 with the import
(`reference_dataset_imports.source_version`, e.g. `read as published: UCSC cytobands:
cytoBandIdeo.txt.gz, SHA-256 …`).

These files are the **backup**. The import loads one only when UCSC cannot be reached, or serves
no cytoband table for the genome, and only if its SHA-256 is the pinned one below
(`CYTOBAND_BACKUP_FILES` in `backend/app/core/config.py`, TRACE-14). Another file is refused
with a logged error naming the file, both digests and the fix. The import then records the backup
by name, the day it was taken from UCSC, and its SHA-256. Without UCSC and without a usable
backup, the assembly import fails as a whole: its genes are not loaded either, and a start
creates the assembly empty and tries again at the next start.

| UCSC genome | File | Source | Served by UCSC (Last-Modified) | Taken on | Rows | SHA-256 |
| --- | --- | --- | --- | --- | --- | --- |
| hg38 (GRCh38) | `hg38.cytoBandIdeo.txt.gz` | <https://hgdownload.soe.ucsc.edu/goldenPath/hg38/database/cytoBandIdeo.txt.gz> | 2022-10-29 | 2026-11-06 | 1,549 | `c092fd7f0c1d0a3a543d1d826b3ae07a55bc9c8a9818580232ff9c8f13c60865` |
| hg19 (GRCh37) | `hg19.cytoBandIdeo.txt.gz` | <https://hgdownload.soe.ucsc.edu/goldenPath/hg19/database/cytoBandIdeo.txt.gz> | 2020-03-22 | 2026-11-06 | 1,136 | `ba056331d70e840f6874bf7211a6483e2e4ed01f9e3e36c4b9def6966f50ffd2` |

Each file is UCSC's download byte for byte (gzip, five tab-separated columns: chromosome, start,
end, band, Giemsa stain). Rows include the alternative and unplaced contigs UCSC lists. T2T
(`hs1`) has no UCSC cytoband table and no backup: it gets one band per chromosome, built from
UCSC's chromosome sizes.

## Updating a backup (controlled change)

1. Download the genome's table from the URL above, byte for byte.
2. Replace the file and update its row (Last-Modified, taken on, rows, SHA-256:
   `shasum -a 256 <file>`).
3. Move the pin in `backend/app/core/config.py` (`CYTOBAND_BACKUP_FILES`: SHA-256 and the day
   taken). `backend/tests/test_cytoband_backup.py` fails until the file, the pin and this table
   agree.
4. A deployment that reads its reference data from a bucket needs the new file there too.
