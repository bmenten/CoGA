CREATE DATABASE IF NOT EXISTS coga;

CREATE TABLE IF NOT EXISTS coga.`GRCh38/SNV_INDEL/variants_disk`
(
    `key` UInt64,
    `variantId` String,
    `chrom` LowCardinality(String),
    `pos` UInt32,
    `ref` String,
    `alt` String,
    `rsid` Nullable(String),
    `annotationDigest` String,
    `annotationsJson` String,
    `source` LowCardinality(String),
    `updatedAt` DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updatedAt)
PRIMARY KEY key
ORDER BY key;

CREATE TABLE IF NOT EXISTS coga.`GRCh38/SNV_INDEL/variants_memory`
(
    `key` UInt64,
    `variantId` String,
    `annotationDigest` String,
    `annotationsJson` String
)
ENGINE = Memory;

CREATE TABLE IF NOT EXISTS coga.`GRCh38/SNV_INDEL/variants/details`
(
    `key` UInt64,
    `variantId` String,
    `chrom` LowCardinality(String),
    `pos` UInt32,
    `ref` String,
    `alt` String,
    `rsid` Nullable(String),
    `filters` Array(LowCardinality(String)),
    `annotationsJson` String,
    `source` LowCardinality(String),
    `liftedOverChrom` LowCardinality(Nullable(String)),
    `liftedOverPos` Nullable(UInt32),
    `updatedAt` DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updatedAt)
PRIMARY KEY key
ORDER BY key;

CREATE TABLE IF NOT EXISTS coga.`GRCh38/SNV_INDEL/key_lookup`
(
    `variantId` String,
    `key` UInt64
)
ENGINE = ReplacingMergeTree
PRIMARY KEY variantId
ORDER BY variantId;

CREATE TABLE IF NOT EXISTS coga.`GRCh38/SNV_INDEL/entries`
(
    `key` UInt64,
    `variantId` String,
    `project_guid` LowCardinality(String),
    `family_guid` String,
    `sample_type` LowCardinality(String),
    `xpos` UInt64,
    `chrom` LowCardinality(String),
    `pos` UInt32,
    `ref` String,
    `alt` String,
    `is_gnomad_gt_5_percent` Bool DEFAULT false,
    `is_annotated_in_any_gene` Bool DEFAULT false,
    `gene_symbols` Array(String),
    `filters` Array(LowCardinality(String)),
    `calls` Nested(
        sampleId String,
        gt LowCardinality(String),
        gq Nullable(UInt16),
        dp Nullable(UInt16),
        ab Nullable(Float32),
        af Array(Nullable(Float32)),
        ad Array(Nullable(UInt16)),
        ps Nullable(UInt64)
    ),
    `sign` Int8
)
ENGINE = CollapsingMergeTree(sign)
PARTITION BY project_guid
ORDER BY (project_guid, family_guid, sample_type, chrom, pos, key);

CREATE TABLE IF NOT EXISTS coga.`GRCh38/SNV_INDEL/project_gt_stats`
(
    `project_guid` LowCardinality(String),
    `key` UInt64,
    `sample_type` LowCardinality(String),
    `ref_samples` UInt64,
    `het_samples` UInt64,
    `hom_samples` UInt64
)
ENGINE = SummingMergeTree
PARTITION BY project_guid
ORDER BY (project_guid, key, sample_type);

CREATE TABLE IF NOT EXISTS coga.`GRCh38/SNV_INDEL/gt_stats`
(
    `key` UInt64,
    `ac_wes` UInt64,
    `ac_wgs` UInt64,
    `hom_wes` UInt64,
    `hom_wgs` UInt64
)
ENGINE = SummingMergeTree
ORDER BY key;

CREATE MATERIALIZED VIEW IF NOT EXISTS coga.`GRCh38/SNV_INDEL/entries_to_project_gt_stats_mv`
TO coga.`GRCh38/SNV_INDEL/project_gt_stats`
AS
SELECT
    project_guid,
    key,
    sample_type,
    countIf(gt = 'REF') AS ref_samples,
    countIf(gt = 'HET') AS het_samples,
    countIf(gt = 'HOM') AS hom_samples
FROM coga.`GRCh38/SNV_INDEL/entries`
ARRAY JOIN calls.sampleId AS sampleId, calls.gt AS gt
GROUP BY project_guid, key, sample_type;

CREATE MATERIALIZED VIEW IF NOT EXISTS coga.`GRCh38/SNV_INDEL/project_gt_stats_to_gt_stats_mv`
TO coga.`GRCh38/SNV_INDEL/gt_stats`
AS
SELECT
    key,
    sumIf((het_samples * 1) + (hom_samples * 2), sample_type = 'WES') AS ac_wes,
    sumIf((het_samples * 1) + (hom_samples * 2), sample_type = 'WGS') AS ac_wgs,
    sumIf(hom_samples, sample_type = 'WES') AS hom_wes,
    sumIf(hom_samples, sample_type = 'WGS') AS hom_wgs
FROM coga.`GRCh38/SNV_INDEL/project_gt_stats`
GROUP BY key;

CREATE TABLE IF NOT EXISTS coga.`GRCh38/SV/variants/details`
(
    `key` UInt64,
    `variantId` String,
    `chrom` LowCardinality(String),
    `start` UInt32,
    `end` UInt32,
    `svType` LowCardinality(String),
    `source` LowCardinality(String),
    `remoteChrom` LowCardinality(Nullable(String)),
    `remoteStart` Nullable(UInt32),
    `remoteEnd` Nullable(UInt32),
    `svLen` Nullable(Int32),
    `filters` Array(LowCardinality(String)),
    `annotationsJson` String,
    `updatedAt` DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updatedAt)
PRIMARY KEY key
ORDER BY key;

CREATE TABLE IF NOT EXISTS coga.`GRCh38/SV/key_lookup`
(
    `variantId` String,
    `key` UInt64
)
ENGINE = ReplacingMergeTree
PRIMARY KEY variantId
ORDER BY variantId;

CREATE TABLE IF NOT EXISTS coga.`GRCh38/SV/entries`
(
    `key` UInt64,
    `variantId` String,
    `project_guid` LowCardinality(String),
    `family_guid` String,
    `sample_type` LowCardinality(String),
    `chrom` LowCardinality(String),
    `start` UInt32,
    `end` UInt32,
    `svType` LowCardinality(String),
    `source` LowCardinality(String),
    `gene_symbols` Array(String),
    `calls` Nested(
        sampleId String,
        gt LowCardinality(String),
        gq Nullable(UInt16),
        qual Nullable(Float32),
        readSupport Nullable(UInt32),
        filter Nullable(String)
    ),
    `sign` Int8
)
ENGINE = CollapsingMergeTree(sign)
PARTITION BY project_guid
ORDER BY (project_guid, family_guid, svType, chrom, start, key);

CREATE TABLE IF NOT EXISTS coga.`GRCh38/INTERVAL/entries`
(
    `family_guid` String,
    `sample_guid` String,
    `track_type` LowCardinality(String),
    `source` LowCardinality(String),
    `filename` String,
    `chrom` LowCardinality(String),
    `start` UInt64,
    `end` UInt64,
    `record_id` Nullable(String),
    `value` Nullable(Float64),
    `origin` Nullable(String),
    `hap1` Nullable(String),
    `hap2` Nullable(String),
    `ps` Nullable(UInt64),
    `metadata_json` String,
    `uploaded_at` DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY track_type
ORDER BY (family_guid, sample_guid, track_type, chrom, start, end, source);
