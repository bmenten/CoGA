import { useState, type FC } from 'react';
import api from '../../lib/api';
import { getErrorMessage } from '../../lib/errorMessage';

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

    const runUpload = async (overwrite: boolean) =>
      api.post(`/families/${familyId.trim()}/small-variants/upload`, formData, {
        params: {
          overwrite,
          source_format: familyFormat,
        },
        headers: { 'Content-Type': 'multipart/form-data' },
      });

    try {
      const { data } = await runUpload(false);
      setFamilyStatus(
        `Imported ${data.inserted} small variants via ${data.source_format}${data.haplotypes_inserted ? ` and created ${data.haplotypes_inserted} haplotype blocks` : ''}.`
      );
    } catch (err: unknown) {
      if ((err as { response?: { status?: number } })?.response?.status === 409) {
        const overwrite = window.confirm(
          'Small variants already exist for this family. Overwrite the existing family small variants and haplotypes?'
        );
        if (overwrite) {
          try {
            const { data } = await runUpload(true);
            setFamilyStatus(
              `Replaced family small variants with ${data.inserted} records via ${data.source_format}${data.haplotypes_inserted ? ` and ${data.haplotypes_inserted} haplotype blocks` : ''}.`
            );
          } catch (overwriteError: unknown) {
            setFamilyStatus(getErrorMessage(overwriteError, 'Family small-variant upload failed.'));
          }
        } else {
          setFamilyStatus('Family small-variant upload cancelled.');
        }
      } else {
        setFamilyStatus(getErrorMessage(err, 'Family small-variant upload failed.'));
      }
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

    const runUpload = async (overwrite: boolean) =>
      api.post(`/structural-variants/upload/${variantSample.trim()}`, formData, {
        params: {
          overwrite,
          source_format: variantFormat,
        },
        headers: { 'Content-Type': 'multipart/form-data' },
      });

    try {
      const { data } = await runUpload(false);
      setVariantStatus(
        `Processed ${data.processed} variants via ${data.source_format} (${data.created} created, ${data.merged} merged).`
      );
    } catch (err: unknown) {
      if ((err as { response?: { status?: number } })?.response?.status === 409) {
        const overwrite = window.confirm(
          'Structural variants already exist for this sample and source. Overwrite them?'
        );
        if (overwrite) {
          try {
            const { data } = await runUpload(true);
            setVariantStatus(
              `Replaced structural variants with ${data.processed} records via ${data.source_format} (${data.created} created, ${data.merged} merged).`
            );
          } catch (overwriteError: unknown) {
            setVariantStatus(getErrorMessage(overwriteError, 'Structural-variant upload failed.'));
          }
        } else {
          setVariantStatus('Structural-variant upload cancelled.');
        }
      } else {
        setVariantStatus(getErrorMessage(err, 'Structural-variant upload failed.'));
      }
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
      const { data } = await api.post(`/bed/upload/${bedSample.trim()}/${bedType}`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setBedStatus(`Uploaded ${data.inserted} ${bedType} record(s).`);
    } catch (err: unknown) {
      if ((err as { response?: { status?: number } })?.response?.status === 409) {
        const overwrite = window.confirm(
          'BED data already exist for this sample and track type. Overwrite them?'
        );
        if (overwrite) {
          try {
            const { data } = await api.post(
              `/bed/upload/${bedSample.trim()}/${bedType}?overwrite=true`,
              formData,
              {
                headers: { 'Content-Type': 'multipart/form-data' },
              }
            );
            setBedStatus(`Replaced ${data.inserted} ${bedType} record(s).`);
          } catch (overwriteError: unknown) {
            setBedStatus(getErrorMessage(overwriteError, 'BED upload failed.'));
          }
        } else {
          setBedStatus('BED upload cancelled.');
        }
      } else {
        setBedStatus(getErrorMessage(err, 'BED upload failed.'));
      }
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

    const runUpload = async (overwrite: boolean) =>
      api.post(`/repeat-expansions/upload/${repeatSample.trim()}`, formData, {
        params: { overwrite },
        headers: { 'Content-Type': 'multipart/form-data' },
      });

    try {
      const { data } = await runUpload(false);
      setRepeatStatus(`Imported ${data.inserted} TRGT repeat loci.`);
    } catch (err: unknown) {
      if ((err as { response?: { status?: number } })?.response?.status === 409) {
        const overwrite = window.confirm(
          'Repeat expansion data already exist for this sample. Overwrite them?'
        );
        if (overwrite) {
          try {
            const { data } = await runUpload(true);
            setRepeatStatus(`Replaced repeat expansion data with ${data.inserted} loci.`);
          } catch (overwriteError: unknown) {
            setRepeatStatus(getErrorMessage(overwriteError, 'TRGT upload failed.'));
          }
        } else {
          setRepeatStatus('TRGT upload cancelled.');
        }
      } else {
        setRepeatStatus(getErrorMessage(err, 'TRGT upload failed.'));
      }
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
              className="form-button w-full justify-center"
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
              className="form-button w-full justify-center"
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
              Upload coverage, APCAD, or segments per sample.
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
              className="form-button w-full justify-center"
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
              className="form-button w-full justify-center"
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
