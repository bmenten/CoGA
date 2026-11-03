import { useEffect, useState, type FC } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';
import type { WgsSourceOut, WgsSourcesOut } from '../../lib/apiSchema.generated';

type Props = { familyId: string; projectId?: string; enabled: boolean; currentSource: boolean };

const FamilyWgsSourcePanel: FC<Props> = ({ familyId, projectId, enabled, currentSource }) => {
  const client = useQueryClient();
  const params = projectId ? { project_id: projectId } : {};
  const [fileId, setFileId] = useState('');
  const [completeWgs, setCompleteWgs] = useState(false);
  const [assumeRef, setAssumeRef] = useState(false);
  const [allowUnfiltered, setAllowUnfiltered] = useState(false);
  const query = useQuery({
    queryKey: ['family-wgs-files', familyId, projectId], enabled,
    queryFn: async () => (await api.get<WgsSourcesOut>(apiPath`/families/${familyId}/wgs-source`, { params })).data,
  });
  const files = query.data?.candidates;
  const evidence = files?.find((file) => file.file_id === fileId)?.assessment;
  const recordCount = typeof evidence?.record_count === 'number' ? evidence.record_count : null;
  const chromosomeCount = Array.isArray(evidence?.autosomes) ? evidence.autosomes.length : null;
  useEffect(() => {
    if (files?.length && !files.some((file) => file.file_id === fileId)) setFileId(files[0].file_id);
  }, [files, fileId]);
  const register = useMutation({
    mutationFn: () => api.post<WgsSourceOut>(apiPath`/families/${familyId}/wgs-source`, {
      file_id: fileId, full_wgs: true, genome_build: 'GRCh38',
      absent_record_is_hom_ref: assumeRef, allow_unfiltered: allowUnfiltered,
    }, { params }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['family-wgs-files', familyId, projectId] }),
        client.invalidateQueries({ queryKey: ['family-ancestry', familyId] }),
      ]);
    },
  });
  if (!query.data && !query.error) return null;
  return <section className="surface-card space-y-4" aria-labelledby="wgs-source-heading">
    <h2 id="wgs-source-heading">WGS source</h2>
    <p>Verify a retained VCF for ancestry without importing its variants again. Variant distribution can suggest WGS; it does not prove callable coverage.</p>
    {query.error && <p role="alert">{getErrorMessage(query.error, 'Could not load retained VCFs.')}</p>}
    {query.data && !query.data.can_register && <p>Source registration requires GRCh38 and permission to change this project.</p>}
    {query.data?.can_register && !files?.length && <p>No retained nuclear VCF is available. A future complete-WGS upload can register its source automatically.</p>}
    {query.data?.can_register && Boolean(files?.length) && <details open={!currentSource}>
      <summary>{currentSource ? 'Change source or assumptions' : 'Register the retained WGS source'}</summary>
      <form className="field-grid mt-4" onSubmit={(event) => { event.preventDefault(); register.mutate(); }}>
        <label className="field-label">Retained VCF
          <select value={fileId} onChange={(event) => setFileId(event.target.value)} disabled={register.isPending}>
            {files?.map((file) => <option key={file.file_id} value={file.file_id}>{file.file_name}</option>)}
          </select>
        </label>
        {recordCount !== null && <p>Saved assessment: {recordCount.toLocaleString()} records across {chromosomeCount ?? 0} autosomes. {evidence?.genome_wide_candidate === true ? 'The variant distribution is compatible with genome-wide data.' : 'The distribution alone does not establish a whole-genome source.'}</p>}
        {recordCount === null && <p>This older upload has no saved WGS assessment. Verification will check the retained file now.</p>}
        <label className="field-label"><span><input type="checkbox" checked={completeWgs} onChange={(event) => setCompleteWgs(event.target.checked)} disabled={register.isPending} /> This is a complete whole-genome callset, not an exome, panel or regional subset</span></label>
        <label className="field-label">Absent positions
          <select value={assumeRef ? 'reference' : 'unknown'} onChange={(event) => setAssumeRef(event.target.value === 'reference')} disabled={register.isPending}>
            <option value="unknown">Keep unknown</option>
            <option value="reference">Assume homozygous reference (explicit WGS assumption)</option>
          </select>
        </label>
        <label className="field-label"><span><input type="checkbox" checked={allowUnfiltered} onChange={(event) => setAllowUnfiltered(event.target.checked)} disabled={register.isPending} /> Allow unfiltered calls (FILTER=.) for exploratory calculations</span></label>
        <p>Failed filters, no-calls, incompatible alleles and ambiguous records remain excluded. Accepted unfiltered calls and assumed reference calls are counted separately in QC.</p>
        <button className="form-button" disabled={!fileId || !completeWgs || register.isPending}>
          {register.isPending ? 'Verifying retained VCF…' : 'Verify and register WGS source'}
        </button>
        {register.error && <p role="alert">{getErrorMessage(register.error, 'Could not register this source.')}</p>}
        {register.isSuccess && <p role="status">WGS source registered. The ancestry controls have been refreshed.</p>}
      </form>
    </details>}
  </section>;
};

export default FamilyWgsSourcePanel;
