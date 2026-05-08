DROP TABLE IF EXISTS sample_interval_tracks;

CREATE TABLE IF NOT EXISTS sample_interval_track_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sample_id UUID NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    family_id UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    assembly_id UUID REFERENCES assemblies(id) ON DELETE SET NULL,
    track_type TEXT NOT NULL CHECK (track_type IN ('coverage', 'apcad', 'segments', 'haplotype')),
    source TEXT NOT NULL DEFAULT 'web',
    filename TEXT NOT NULL DEFAULT '',
    row_count BIGINT NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (sample_id, track_type, source, filename)
);

CREATE INDEX IF NOT EXISTS idx_sample_interval_track_sources_sample_type
    ON sample_interval_track_sources (sample_id, track_type);

CREATE INDEX IF NOT EXISTS idx_sample_interval_track_sources_family_type
    ON sample_interval_track_sources (family_id, track_type);

CREATE INDEX IF NOT EXISTS idx_sample_interval_track_sources_assembly_type
    ON sample_interval_track_sources (assembly_id, track_type);
