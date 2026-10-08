import { useState, type FC } from 'react';
import api from '../../lib/api';
import { getErrorMessage } from '../../lib/errorMessage';
import { apiPath } from '../../lib/apiPath';
import type { SmallVariantUploadResult } from '../../lib/apiSchema.generated';

type UploadMessages<T> = {
  /** Asked before data that already exist are overwritten. */
  confirm: string;
  done: (data: T) => string;
  replaced: (data: T) => string;
  failed: string;
  cancelled: string;
};

/**
 * Runs an upload. When the data already exist (409), asks before overwriting and runs it
 * once more with overwrite set. Resolves to what the form then says.
 */
const uploadWithOverwrite = async <R extends { data: unknown }>(
  runUpload: (overwrite: boolean) => Promise<R>,
  messages: UploadMessages<R['data']>
): Promise<string> => {
  try {
    const { data } = await runUpload(false);
    return messages.done(data);
  } catch (err: unknown) {
    if ((err as { response?: { status?: number } })?.response?.status !== 409) {
      return getErrorMessage(err, messages.failed);
    }
    if (!window.confirm(messages.confirm)) {
      return messages.cancelled;
    }
    try {
      const { data } = await runUpload(true);
      return messages.replaced(data);
    } catch (overwriteError: unknown) {
      return getErrorMessage(overwriteError, messages.failed);
    }
  }
};

const SampleUpload: FC = () => {
  const [familyFile, setFamilyFile] = useState<File | null>(null);
  const [familyId, setFamilyId] = useState('');
  const [familyFormat, setFamilyFormat] = useState('auto');
  const [familyStatus, setFamilyStatus] = useState('');
  const [familyLoading, setFamilyLoading] = useState(false);

  const [variantFile, setVariantFile] = useState<File | null>(null);
  const [variantSample, setVariantSample] = useState('');
  const [variantFormat, setVariantFormat] = useState('auto');
  const [variantStatus, setVariantStatus] = useState('');
  const [variantLoading, setVariantLoading] = useState(false);

  const [bedFile, setBedFile] = useState<File | null>(null);
  const [bedSample, setBedSample] = useState('');
  const [bedType, setBedType] = useState('coverage');
  const [bedStatus, setBedStatus] = useState('');
  const [bedLoading, setBedLoading] = useState(false);

  const [repeatFile, setRepeatFile] = useState<File | null>(null);
  const [repeatSample, setRepeatSample] = useState('');
  const [repeatStatus, setRepeatStatus] = useState('');
  const [repeatLoading, setRepeatLoading] = useState(false);

  const handleFamilySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!familyFile || !familyId.trim()) return;
    const formData = new FormData();
    formData.append('file', familyFile);
    setFamilyStatus('');
    setFamilyLoading(true);
    try {
      setFamilyStatus(
        await uploadWithOverwrite(
          (overwrite: boolean) =>
            api.post<SmallVariantUploadResult>(
              apiPath`/families/${familyId.trim()}/small-variants/upload`,
              formData,
              {
                params: {
                  overwrite,
                  source_format: familyFormat,
                },
                headers: { 'Content-Type': 'multipart/form-data' },
              }
            ),
          {
            confirm:
              'Small variants already exist for this family. Overwrite the existing family small variants and haplotypes?',
            done: (data) =>
              `Imported ${data.inserted} small variants via ${data.source_format}${data.haplotypes_inserted ? ` and created ${data.haplotypes_inserted} haplotype blocks` : ''}.`,
            replaced: (data) =>
              `Replaced family small variants with ${data.inserted} records via ${data.source_format}${data.haplotypes_inserted ? ` and ${data.haplotypes_inserted} haplotype blocks` : ''}.`,
            failed: 'Family small-variant upload failed.',
            cancelled: 'Family small-variant upload cancelled.',
          }
        )
      );
    } finally {
      setFamilyLoading(false);
    }
  };

  const handleVariantSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!variantFile || !variantSample.trim()) return;
    const formData = new FormData();
    formData.append('file', variantFile);
    setVariantStatus('');
    setVariantLoading(true);
    try {
      setVariantStatus(
        await uploadWithOverwrite(
          (overwrite: boolean) =>
            api.post(apiPath`/structural-variants/upload/${variantSample.trim()}`, formData, {
              params: {
                overwrite,
                source_format: variantFormat,
              },
              headers: { 'Content-Type': 'multipart/form-data' },
            }),
          {
            confirm: 'Structural variants already exist for this sample and source. Overwrite them?',
            done: (data) =>
              `Processed ${data.processed} variants via ${data.source_format} (${data.created} created, ${data.merged} merged).`,
            replaced: (data) =>
              `Replaced structural variants with ${data.processed} records via ${data.source_format} (${data.created} created, ${data.merged} merged).`,
            failed: 'Structural-variant upload failed.',
            cancelled: 'Structural-variant upload cancelled.',
          }
        )
      );
    } finally {
      setVariantLoading(false);
    }
  };

  const handleBedSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!bedFile || !bedSample.trim()) return;
    const formData = new FormData();
    formData.append('file', bedFile);
    setBedStatus('');
    setBedLoading(true);
    try {
      setBedStatus(
        await uploadWithOverwrite(
          (overwrite: boolean) =>
            api.post(
              overwrite
                ? apiPath`/bed/upload/${bedSample.trim()}/${bedType}?overwrite=true`
                : apiPath`/bed/upload/${bedSample.trim()}/${bedType}`,
              formData,
              {
                headers: { 'Content-Type': 'multipart/form-data' },
              }
            ),
          {
            confirm: 'BED data already exist for this sample and track type. Overwrite them?',
            done: (data) => `Uploaded ${data.inserted} ${bedType} record(s).`,
            replaced: (data) => `Replaced ${data.inserted} ${bedType} record(s).`,
            failed: 'BED upload failed.',
            cancelled: 'BED upload cancelled.',
          }
        )
      );
    } finally {
      setBedLoading(false);
    }
  };

  const handleRepeatSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!repeatFile || !repeatSample.trim()) return;
    const formData = new FormData();
    formData.append('file', repeatFile);
    setRepeatStatus('');
    setRepeatLoading(true);
    try {
      setRepeatStatus(
        await uploadWithOverwrite(
          (overwrite: boolean) =>
            api.post(apiPath`/repeat-expansions/upload/${repeatSample.trim()}`, formData, {
              params: { overwrite },
              headers: { 'Content-Type': 'multipart/form-data' },
            }),
          {
            confirm: 'Repeat expansion data already exist for this sample. Overwrite them?',
            done: (data) => `Imported ${data.inserted} TRGT repeat loci.`,
            replaced: (data) => `Replaced repeat expansion data with ${data.inserted} loci.`,
            failed: 'TRGT upload failed.',
            cancelled: 'TRGT upload cancelled.',
          }
        )
      );
    } finally {
      setRepeatLoading(false);
    }
  };

  return (
    <div className="page-shell space-y-6">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Upload</p>
            <h1 className="catalog-card-title">Upload family and sample data</h1>
            <p className="catalog-card-copy">
              Use the website for family-level small variants, sample-level structural
              variants, and BED-backed assay tracks. The small-variant and SV routes now
              follow the same parser semantics as the CLI import flows.
            </p>
          </div>
        </div>
      </section>

      <div className="grid gap-6 xl:grid-cols-4">
        <section className="surface-card space-y-5">
          <div className="space-y-2">
            <h2 className="section-title">Family Small Variants</h2>
            <p className="section-copy">
              Upload a family VCF in Clair3 or GLIMPSE2 style. GLIMPSE2 uploads also
              create haplotype blocks for the haplotype tracks.
            </p>
          </div>
          <form onSubmit={handleFamilySubmit} className="field-grid">
            <label className="field-label">
              Family ID
              <input
                type="text"
                placeholder="Family ID"
                value={familyId}
                onChange={(e) => setFamilyId(e.target.value)}
              />
            </label>
            <label className="field-label">
              Parser
              <select value={familyFormat} onChange={(e) => setFamilyFormat(e.target.value)}>
                <option value="auto">Auto detect</option>
                <option value="clair3">Clair3 / phased family VCF</option>
                <option value="glimpse2">GLIMPSE2 / haplotype VCF</option>
              </select>
            </label>
            <label className="field-label">
              Variant file
              <input
                type="file"
                accept=".vcf,.vcf.gz,.gz"
                onChange={(e) => setFamilyFile(e.target.files?.[0] || null)}
              />
            </label>
            <button
              type="submit"
              className="form-button"
              disabled={familyLoading}
            >
              Upload Family Variants
            </button>
          </form>
          {familyLoading && <div className="loading-spinner" />}
          {familyStatus && <p className="form-status text-center">{familyStatus}</p>}
        </section>

        <section className="surface-card space-y-5">
          <div className="space-y-2">
            <h2 className="section-title">Structural Variants</h2>
            <p className="section-copy">
              Upload manual TSVs or real Sniffles and Spectre VCFs with parser selection or
              auto detection.
            </p>
          </div>
          <form onSubmit={handleVariantSubmit} className="field-grid">
            <label className="field-label">
              Sample ID
              <input
                type="text"
                placeholder="Sample ID"
                value={variantSample}
                onChange={(e) => setVariantSample(e.target.value)}
              />
            </label>
            <label className="field-label">
              Parser
              <select value={variantFormat} onChange={(e) => setVariantFormat(e.target.value)}>
                <option value="auto">Auto detect</option>
                <option value="sniffles">Sniffles VCF</option>
                <option value="spectre">Spectre VCF</option>
                <option value="manual">Manual TSV</option>
              </select>
            </label>
            <label className="field-label">
              Variant file
              <input
                type="file"
                accept=".vcf,.vcf.gz,.gz,.tsv,.txt"
                onChange={(e) => setVariantFile(e.target.files?.[0] || null)}
              />
            </label>
            <button
              type="submit"
              className="form-button"
              disabled={variantLoading}
            >
              Upload Structural Variants
            </button>
          </form>
          {variantLoading && <div className="loading-spinner" />}
          {variantStatus && <p className="form-status text-center">{variantStatus}</p>}
        </section>

        <section className="surface-card space-y-5">
          <div className="space-y-2">
            <h2 className="section-title">BED Tracks</h2>
            <p className="section-copy">
              Upload coverage, APCAD, PCF APCAD segments, or segments per sample.
            </p>
          </div>
          <form onSubmit={handleBedSubmit} className="field-grid">
            <label className="field-label">
              Sample ID
              <input
                type="text"
                placeholder="Sample ID"
                value={bedSample}
                onChange={(e) => setBedSample(e.target.value)}
              />
            </label>
            <label className="field-label">
              Track type
              <select value={bedType} onChange={(e) => setBedType(e.target.value)}>
                <option value="coverage">Coverage</option>
                <option value="apcad">APCAD</option>
                <option value="apcad_pcf">APCAD PCF segments</option>
                <option value="segments">Segments</option>
              </select>
            </label>
            <label className="field-label">
              BED file
              <input
                type="file"
                accept=".bed,.bed.gz,.gz"
                onChange={(e) => setBedFile(e.target.files?.[0] || null)}
              />
            </label>
            <button
              type="submit"
              className="form-button"
              disabled={bedLoading}
            >
              Upload BED Track
            </button>
          </form>
          {bedLoading && <div className="loading-spinner" />}
          {bedStatus && <p className="form-status text-center">{bedStatus}</p>}
        </section>

        <section className="surface-card space-y-5">
          <div className="space-y-2">
            <h2 className="section-title">Repeat Expansions</h2>
            <p className="section-copy">
              Upload TRGT VCF output for one sample. Known repeat loci are classified into
              normal, grey-zone, and pathogenic ranges for the repeat table and viewer tracks.
            </p>
          </div>
          <form onSubmit={handleRepeatSubmit} className="field-grid">
            <label className="field-label">
              Sample ID
              <input
                type="text"
                placeholder="Sample ID"
                value={repeatSample}
                onChange={(e) => setRepeatSample(e.target.value)}
              />
            </label>
            <label className="field-label">
              TRGT file
              <input
                type="file"
                accept=".vcf,.vcf.gz,.gz"
                onChange={(e) => setRepeatFile(e.target.files?.[0] || null)}
              />
            </label>
            <button
              type="submit"
              className="form-button"
              disabled={repeatLoading}
            >
              Upload TRGT
            </button>
          </form>
          {repeatLoading && <div className="loading-spinner" />}
          {repeatStatus && <p className="form-status text-center">{repeatStatus}</p>}
        </section>
      </div>
    </div>
  );
};

export default SampleUpload;
