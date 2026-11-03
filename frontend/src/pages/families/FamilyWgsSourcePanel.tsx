import { useEffect, useState, type FC } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';
import type {
  WgsSourceOut,
  WgsSourcesOut,
} from '../../lib/apiSchema.generated';

type Props = {
  familyId: string;
  projectId?: string;
  enabled: boolean;
  currentSource: boolean;
};

/**
 * Registers one of the family's retained small-variant uploads as the ancestry PCA's WGS
 * source. CoGA checks the file against the stored genotypes; that the file is a whole genome
 * is the user's explicit declaration, which a variant distribution cannot prove.
 */
const FamilyWgsSourcePanel: FC<Props> = ({
  familyId,
  projectId,
  enabled,
  currentSource,
}) => {
  const client = useQueryClient();
  const params = projectId ? { project_id: projectId } : {};
  const [fileId, setFileId] = useState('');
  const [completeWgs, setCompleteWgs] = useState(false);
  const [assumeRef, setAssumeRef] = useState(false);
  const [allowUnfiltered, setAllowUnfiltered] = useState(false);
  const query = useQuery({
    queryKey: ['family-wgs-files', familyId, projectId],
    enabled,
    queryFn: async () =>
      (
        await api.get<WgsSourcesOut>(
          apiPath`/families/${familyId}/wgs-source`,
          { params }
        )
      ).data,
  });
  const files = query.data?.candidates;
  useEffect(() => {
    if (files?.length && !files.some((file) => file.file_id === fileId))
      setFileId(files[0].file_id);
  }, [files, fileId]);
  const register = useMutation({
    mutationFn: () =>
      api.post<WgsSourceOut>(
        apiPath`/families/${familyId}/wgs-source`,
        {
          file_id: fileId,
          full_wgs: true,
          genome_build: 'GRCh38',
          absent_record_is_hom_ref: assumeRef,
          allow_unfiltered: allowUnfiltered,
        },
        { params }
      ),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({
          queryKey: ['family-wgs-files', familyId, projectId],
        }),
        client.invalidateQueries({ queryKey: ['family-ancestry', familyId] }),
      ]);
    },
  });
  if (!query.data && !query.error) return null;
  return (
    <section
      className="surface-card space-y-4"
      aria-labelledby="wgs-source-heading"
    >
      <h2 id="wgs-source-heading" className="section-title">
        WGS source
      </h2>
      <p>
        The PCA reads one whole-genome VCF of the family. Register a retained
        upload: CoGA checks its checksum, its samples and that its calls are the
        family’s stored genotypes, without importing it again. A family package
        declares its source in its manifest instead.
      </p>
      {query.error && (
        <p role="alert">
          {getErrorMessage(query.error, 'Could not load retained VCFs.')}
        </p>
      )}
      {query.data && !query.data.can_register && (
        <p>
          Registering a source needs a family on GRCh38 and permission to change
          this project.
        </p>
      )}
      {query.data?.can_register && !files?.length && (
        <p>No retained nuclear VCF of this family is available.</p>
      )}
      {query.data?.can_register && Boolean(files?.length) && (
        <details open={!currentSource}>
          <summary>
            {currentSource
              ? 'Change the source or its assumptions'
              : 'Register the retained WGS source'}
          </summary>
          <form
            className="field-grid mt-4"
            onSubmit={(event) => {
              event.preventDefault();
              register.mutate();
            }}
          >
            <label className="field-label">
              Retained VCF
              <select
                value={fileId}
                onChange={(event) => setFileId(event.target.value)}
                disabled={register.isPending}
              >
                {files?.map((file) => (
                  <option key={file.file_id} value={file.file_id}>
                    {file.file_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field-label">
              <span>
                <input
                  type="checkbox"
                  checked={completeWgs}
                  onChange={(event) => setCompleteWgs(event.target.checked)}
                  disabled={register.isPending}
                />{' '}
                This is a complete whole-genome callset, not an exome, a panel
                or a regional subset
              </span>
            </label>
            <label className="field-label">
              Absent positions
              <select
                value={assumeRef ? 'reference' : 'unknown'}
                onChange={(event) =>
                  setAssumeRef(event.target.value === 'reference')
                }
                disabled={register.isPending}
              >
                <option value="unknown">Keep unknown</option>
                <option value="reference">
                  Assume homozygous reference (explicit WGS assumption)
                </option>
              </select>
            </label>
            <label className="field-label">
              <span>
                <input
                  type="checkbox"
                  checked={allowUnfiltered}
                  onChange={(event) => setAllowUnfiltered(event.target.checked)}
                  disabled={register.isPending}
                />{' '}
                Accept unfiltered calls (FILTER=.)
              </span>
            </label>
            <p>
              Failed filters, no-calls, other alleles and ambiguous records are
              always left out. Accepted unfiltered calls and assumed reference
              calls are counted apart in the marker quality.
            </p>
            <button
              type="submit"
              className="form-button"
              disabled={!fileId || !completeWgs || register.isPending}
            >
              {register.isPending
                ? 'Checking the retained VCF…'
                : 'Check and register the WGS source'}
            </button>
            {register.error && (
              <p role="alert">
                {getErrorMessage(
                  register.error,
                  'Could not register this source.'
                )}
              </p>
            )}
            {register.isSuccess && <p role="status">WGS source registered.</p>}
          </form>
        </details>
      )}
    </section>
  );
};

export default FamilyWgsSourcePanel;
