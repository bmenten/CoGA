import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import Pedigree from '../../components/visualizations/Pedigree';
import api from '../../lib/api';
import { isAdmin } from '../../lib/auth';
import { getErrorMessage } from '../../lib/errorMessage';
import { useProjectCatalog } from '../../lib/reference';

type IntakeMode = 'manual' | 'upload';
type Sex = 'male' | 'female' | 'und';

type DraftMember = {
  localId: string;
  sampleId: string;
  fatherId: string;
  motherId: string;
  sex: Sex;
  affected: boolean;
  isProband: boolean;
};

type PedRow = {
  fid: string;
  iid: string;
  pid: string;
  mid: string;
  sex: string;
  phen: string;
};

type PedUploadResult = {
  families: Array<{
    family_id: string;
    samples: string[];
  }>;
};

let memberSequence = 0;

const createDraftMember = (overrides: Partial<DraftMember> = {}): DraftMember => ({
  localId: `member-${memberSequence++}`,
  sampleId: '',
  fatherId: '',
  motherId: '',
  sex: 'und',
  affected: false,
  isProband: false,
  ...overrides,
});

const rolePreviewFor = (member: DraftMember, members: DraftMember[]): string => {
  const fatherIds = new Set(members.map((entry) => entry.fatherId).filter(Boolean));
  const motherIds = new Set(members.map((entry) => entry.motherId).filter(Boolean));

  if (member.sampleId && fatherIds.has(member.sampleId)) {
    return 'Father';
  }
  if (member.sampleId && motherIds.has(member.sampleId)) {
    return 'Mother';
  }
  if (member.isProband) {
    return 'Proband';
  }
  if (member.fatherId || member.motherId) {
    return 'Child / Sibling';
  }
  return 'Relative';
};

const pedigreeRowsFor = (familyId: string, members: DraftMember[]): PedRow[] => {
  const normalizedFamilyId = familyId.trim() || 'FAMILY_ID';

  return members
    .filter((member) => member.sampleId.trim())
    .map((member) => ({
      fid: normalizedFamilyId,
      iid: member.sampleId.trim(),
      pid: member.fatherId.trim() || '0',
      mid: member.motherId.trim() || '0',
      sex: { male: '1', female: '2', und: '0' }[member.sex],
      phen: member.affected ? '2' : '1',
    }));
};

const pedPreviewFor = (rows: PedRow[]): string =>
  rows.map((row) => [row.fid, row.iid, row.pid, row.mid, row.sex, row.phen].join(' ')).join('\n');

const validateManualFamily = (familyId: string, members: DraftMember[]): string[] => {
  const errors: string[] = [];
  const normalizedFamilyId = familyId.trim();

  if (!normalizedFamilyId) {
    errors.push('Family ID is required.');
  }

  const normalizedMembers = members.map((member) => ({
    ...member,
    sampleId: member.sampleId.trim(),
    fatherId: member.fatherId.trim(),
    motherId: member.motherId.trim(),
  }));

  const sampleIds = normalizedMembers.map((member) => member.sampleId).filter(Boolean);
  if (sampleIds.length === 0) {
    errors.push('Add at least one family member.');
  }
  if (sampleIds.length !== new Set(sampleIds).size) {
    errors.push('Sample IDs must be unique.');
  }

  const memberBySampleId = new Map(
    normalizedMembers.filter((member) => member.sampleId).map((member) => [member.sampleId, member])
  );
  const probands = normalizedMembers.filter((member) => member.isProband);
  if (probands.length > 1) {
    errors.push('Only one proband can be selected.');
  }

  normalizedMembers.forEach((member, index) => {
    const memberLabel = member.sampleId || `Member ${index + 1}`;

    if (!member.sampleId) {
      errors.push(`${memberLabel}: sample ID is required.`);
      return;
    }
    if (member.fatherId && member.fatherId === member.sampleId) {
      errors.push(`${memberLabel}: father cannot reference the same sample.`);
    }
    if (member.motherId && member.motherId === member.sampleId) {
      errors.push(`${memberLabel}: mother cannot reference the same sample.`);
    }
    if (member.fatherId && member.motherId && member.fatherId === member.motherId) {
      errors.push(`${memberLabel}: father and mother must be different.`);
    }
    if (member.fatherId && !memberBySampleId.has(member.fatherId)) {
      errors.push(`${memberLabel}: father ${member.fatherId} is not present in this form.`);
    }
    if (member.motherId && !memberBySampleId.has(member.motherId)) {
      errors.push(`${memberLabel}: mother ${member.motherId} is not present in this form.`);
    }

    const father = member.fatherId ? memberBySampleId.get(member.fatherId) : undefined;
    const mother = member.motherId ? memberBySampleId.get(member.motherId) : undefined;
    if (father?.sex === 'female') {
      errors.push(`${memberLabel}: selected father has female sex.`);
    }
    if (mother?.sex === 'male') {
      errors.push(`${memberLabel}: selected mother has male sex.`);
    }
  });

  return Array.from(new Set(errors));
};

const FamilyIntakePanel: React.FC = () => {
  const userIsAdmin = isAdmin();
  const [mode, setMode] = useState<IntakeMode>('manual');
  const [pedFile, setPedFile] = useState<File | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [familyId, setFamilyId] = useState('');
  const [members, setMembers] = useState<DraftMember[]>([
    createDraftMember({ isProband: true, affected: true }),
  ]);
  const [status, setStatus] = useState('');
  const [statusTone, setStatusTone] = useState<'success' | 'error'>('success');
  const [loading, setLoading] = useState(false);
  const {
    data: projectOptions = [],
    isLoading: projectsLoading,
    error: projectsError,
  } = useProjectCatalog();

  useEffect(() => {
    if (!userIsAdmin && mode === 'upload') {
      setMode('manual');
    }
  }, [mode, userIsAdmin]);

  useEffect(() => {
    if (projectOptions.length === 0) {
      setSelectedProjectId('');
      return;
    }
    setSelectedProjectId((current) =>
      current && projectOptions.some((project) => project.id === current)
        ? current
        : projectOptions[0].id
    );
  }, [projectOptions]);

  const selectedProject = useMemo(
    () => projectOptions.find((project) => project.id === selectedProjectId),
    [projectOptions, selectedProjectId]
  );

  const validateProjectSelection = (): string | null => {
    if (projectsLoading) {
      return 'Projects are still loading.';
    }
    if (projectsError) {
      return 'Project list could not be loaded.';
    }
    if (projectOptions.length === 0) {
      return 'No accessible projects are available.';
    }
    if (!selectedProjectId) {
      return 'Select a project for this family.';
    }
    return null;
  };

  const addMember = (kind: 'proband' | 'father' | 'mother' | 'child' | 'relative') => {
    setMembers((currentMembers) => {
      const defaultFather = currentMembers.find(
        (member) => member.sampleId.trim() && member.sex === 'male'
      )?.sampleId;
      const defaultMother = currentMembers.find(
        (member) => member.sampleId.trim() && member.sex === 'female'
      )?.sampleId;

      const nextMember =
        kind === 'father'
          ? createDraftMember({ sex: 'male' })
          : kind === 'mother'
            ? createDraftMember({ sex: 'female' })
            : kind === 'proband'
              ? createDraftMember({ isProband: true, affected: true })
              : kind === 'child'
                ? createDraftMember({
                    fatherId: defaultFather ?? '',
                    motherId: defaultMother ?? '',
                  })
                : createDraftMember();

      const nextMembers = [...currentMembers, nextMember];
      if (nextMember.isProband) {
        return nextMembers.map((member) =>
          member.localId === nextMember.localId ? member : { ...member, isProband: false }
        );
      }
      return nextMembers;
    });
  };

  const updateMember = (memberLocalId: string, patch: Partial<DraftMember>) => {
    setMembers((currentMembers) => {
      const currentMember = currentMembers.find((member) => member.localId === memberLocalId);
      if (!currentMember) {
        return currentMembers;
      }

      const nextSampleId = patch.sampleId ?? currentMember.sampleId;
      return currentMembers.map((member) => {
        if (member.localId === memberLocalId) {
          return { ...member, ...patch };
        }

        const updatedMember = { ...member };
        if (patch.isProband) {
          updatedMember.isProband = false;
        }
        if (
          patch.sampleId !== undefined &&
          currentMember.sampleId &&
          nextSampleId !== currentMember.sampleId
        ) {
          if (updatedMember.fatherId === currentMember.sampleId) {
            updatedMember.fatherId = nextSampleId;
          }
          if (updatedMember.motherId === currentMember.sampleId) {
            updatedMember.motherId = nextSampleId;
          }
        }
        return updatedMember;
      });
    });
  };

  const removeMember = (memberLocalId: string) => {
    setMembers((currentMembers) => {
      const removedMember = currentMembers.find((member) => member.localId === memberLocalId);
      const remaining = currentMembers.filter((member) => member.localId !== memberLocalId);
      if (!removedMember) {
        return currentMembers;
      }

      const cleaned = remaining.map((member) => ({
        ...member,
        fatherId: member.fatherId === removedMember.sampleId ? '' : member.fatherId,
        motherId: member.motherId === removedMember.sampleId ? '' : member.motherId,
      }));

      if (cleaned.length === 0) {
        return [createDraftMember({ isProband: true, affected: true })];
      }

      if (!cleaned.some((member) => member.isProband)) {
        return cleaned.map((member, index) => ({
          ...member,
          isProband: index === 0,
        }));
      }

      return cleaned;
    });
  };

  const submitPedUpload = async () => {
    if (!userIsAdmin) {
      setStatusTone('error');
      setStatus('Only admins can upload PED files.');
      return;
    }
    const projectError = validateProjectSelection();
    if (projectError) {
      setStatusTone('error');
      setStatus(projectError);
      return;
    }
    if (!pedFile) {
      setStatusTone('error');
      setStatus('Select a PED file first.');
      return;
    }

    const formData = new FormData();
    formData.append('file', pedFile);

    const runUpload = async (overwrite: boolean) => {
      const params = new URLSearchParams({ project_id: selectedProjectId });
      if (overwrite) {
        params.set('overwrite', 'true');
      }
      const url = `/ped/upload?${params.toString()}`;
      return api.post<PedUploadResult>(url, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
    };

    setStatus('');
    setLoading(true);
    try {
      const response = await runUpload(false);
      const result = response.data.families[0];
      setStatusTone('success');
      setStatus(
        `Imported ${result.family_id} with ${result.samples.length} sample(s) in ${selectedProject?.name ?? 'the selected project'}.`
      );
      setPedFile(null);
    } catch (err: unknown) {
      if ((err as { response?: { status?: number } })?.response?.status === 409) {
        if (!userIsAdmin) {
          setStatusTone('error');
          setStatus('Family already exists; ask an admin to update or replace it.');
          return;
        }
        const overwrite = window.confirm(
          'This family already exists. Do you want to overwrite the existing family and samples?'
        );
        if (overwrite) {
          try {
            const response = await runUpload(true);
            const result = response.data.families[0];
            setStatusTone('success');
            setStatus(
              `Replaced ${result.family_id} with ${result.samples.length} sample(s) in ${selectedProject?.name ?? 'the selected project'}.`
            );
          } catch (overwriteError: unknown) {
            setStatusTone('error');
            setStatus(getErrorMessage(overwriteError, 'PED upload failed.'));
          }
        } else {
          setStatusTone('error');
          setStatus('PED upload cancelled.');
        }
      } else {
        setStatusTone('error');
        setStatus(getErrorMessage(err, 'PED upload failed.'));
      }
    } finally {
      setLoading(false);
    }
  };

  const submitManualFamily = async () => {
    const projectError = validateProjectSelection();
    if (projectError) {
      setStatusTone('error');
      setStatus(projectError);
      return;
    }
    const errors = validateManualFamily(familyId, members);
    if (errors.length > 0) {
      setStatusTone('error');
      setStatus(errors[0]);
      return;
    }

    const payload = {
      family_id: familyId.trim(),
      project_id: selectedProjectId,
      members: members.map((member) => ({
        sample_id: member.sampleId.trim(),
        father_id: member.fatherId.trim() || null,
        mother_id: member.motherId.trim() || null,
        sex: member.sex,
        affected: member.affected,
        is_proband: member.isProband,
      })),
    };

    const runCreate = async (overwrite: boolean) => {
      const url = overwrite ? '/ped/manual?overwrite=true' : '/ped/manual';
      return api.post<PedUploadResult>(url, payload);
    };

    setStatus('');
    setLoading(true);
    try {
      const response = await runCreate(false);
      const result = response.data.families[0];
      setStatusTone('success');
      setStatus(
        `Created ${result.family_id} with ${result.samples.length} sample(s) in ${selectedProject?.name ?? 'the selected project'}.`
      );
      setFamilyId('');
      setMembers([createDraftMember({ isProband: true, affected: true })]);
    } catch (err: unknown) {
      if ((err as { response?: { status?: number } })?.response?.status === 409) {
        if (!userIsAdmin) {
          setStatusTone('error');
          setStatus('Family already exists; ask an admin to update or replace it.');
          return;
        }
        const overwrite = window.confirm(
          'This family already exists. Do you want to overwrite the existing family and samples?'
        );
        if (overwrite) {
          try {
            const response = await runCreate(true);
            const result = response.data.families[0];
            setStatusTone('success');
            setStatus(
              `Replaced ${result.family_id} with ${result.samples.length} sample(s) in ${selectedProject?.name ?? 'the selected project'}.`
            );
          } catch (overwriteError: unknown) {
            setStatusTone('error');
            setStatus(getErrorMessage(overwriteError, 'Family creation failed.'));
          }
        } else {
          setStatusTone('error');
          setStatus('Family creation cancelled.');
        }
      } else {
        setStatusTone('error');
        setStatus(getErrorMessage(err, 'Family creation failed.'));
      }
    } finally {
      setLoading(false);
    }
  };

  const validationErrors = validateManualFamily(familyId, members);
  const pedigreeRows = pedigreeRowsFor(familyId, members);
  const pedPreview = pedPreviewFor(pedigreeRows);
  const namedMembersCount = members.filter((member) => member.sampleId.trim()).length;
  const affectedCount = members.filter((member) => member.affected).length;

  return (
    <section className="surface-card intake-panel">
      <div className="grid gap-4 border-b border-[var(--color-border)] pb-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
        <div className="space-y-3">
          <p className="page-kicker">Pedigree Intake</p>
          <h2 className="section-title">Family Builder</h2>
          <p className="page-subtitle max-w-3xl !text-[0.96rem]">
            Build a pedigree manually. The workspace keeps parent assignments consistent, shows the
            PED rows that will be saved, and draws the family structure as you edit.
          </p>
        </div>
        <div className="surface-card-flat">
          <div className="intake-summary-grid">
            <div className="stat-card">
              <span className="stat-label">Mode</span>
              <span className="stat-value !text-[1.5rem]">
                {mode === 'manual' ? 'Manual' : 'PED upload'}
              </span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Members</span>
              <span className="stat-value !text-[1.5rem]">{namedMembersCount}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Affected</span>
              <span className="stat-value !text-[1.5rem]">{affectedCount}</span>
            </div>
          </div>
          <div className="mt-5 flex flex-wrap gap-3">
            <button
              type="button"
              className={`pill-toggle ${mode === 'manual' ? 'pill-toggle--active' : ''}`}
              aria-pressed={mode === 'manual'}
              onClick={() => setMode('manual')}
            >
              Manual Builder
            </button>
            {userIsAdmin ? (
              <button
                type="button"
                className={`pill-toggle ${mode === 'upload' ? 'pill-toggle--active' : ''}`}
                aria-pressed={mode === 'upload'}
                onClick={() => setMode('upload')}
              >
                Upload PED
              </button>
            ) : null}
          </div>
          <label className="field-label mt-5" htmlFor="dashboard-family-project">
            Project
            <select
              id="dashboard-family-project"
              value={selectedProjectId}
              disabled={loading || projectsLoading || projectOptions.length === 0}
              onChange={(event) => setSelectedProjectId(event.target.value)}
            >
              {projectOptions.length === 0 ? (
                <option value="">
                  {projectsLoading ? 'Loading projects...' : 'No accessible projects'}
                </option>
              ) : (
                projectOptions.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))
              )}
            </select>
          </label>
          {projectsError && (
            <p className="status-note status-note--error mt-3">Project list could not be loaded.</p>
          )}
          {!projectsLoading && !projectsError && projectOptions.length === 0 && (
            <p className="status-note status-note--error mt-3">
              No projects are available for this account.
            </p>
          )}
        </div>
      </div>

      {mode === 'upload' ? (
        <div className="surface-card-muted space-y-4">
          <div className="space-y-2">
            <p className="page-kicker">Existing File</p>
            <h3 className="section-title">Import pedigree</h3>
            <p className="text-sm leading-7 text-[var(--color-text-muted)]">
              Use this when the pedigree structure already exists in PED format.
            </p>
          </div>
          <label className="field-label" htmlFor="dashboard-ped-file">
            PED file
            <input
              id="dashboard-ped-file"
              type="file"
              accept=".ped"
              onChange={(event) => setPedFile(event.target.files?.[0] || null)}
            />
          </label>
          <div className="action-row">
            <button
              type="button"
              disabled={loading || projectsLoading || projectOptions.length === 0}
              onClick={submitPedUpload}
            >
              {loading ? 'Uploading...' : 'Import PED'}
            </button>
            {userIsAdmin ? (
              <Link to="/upload-data" className="subtle-link">
                Upload variant or BED data afterwards
              </Link>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(320px,1fr)]">
          <div className="space-y-4">
            <div className="surface-card-muted space-y-4">
              <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
                <label className="field-label flex-1" htmlFor="dashboard-family-id">
                  Family ID
                  <input
                    id="dashboard-family-id"
                    value={familyId}
                    onChange={(event) => setFamilyId(event.target.value)}
                    placeholder="FAM-001"
                  />
                </label>
                <div className="member-card-tags">
                  <span className="badge-chip">{members.length} draft members</span>
                  <span className="badge-chip">{pedigreeRows.length} PED row(s)</span>
                  <span className="badge-chip">
                    {validationErrors.length === 0 ? 'Ready to submit' : `${validationErrors.length} issue(s)`}
                  </span>
                </div>
              </div>
              <p className="text-sm leading-7 text-[var(--color-text-muted)]">
                Start with a proband, then add parents, siblings, children, or relatives.
              </p>
              <div className="action-row">
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => addMember('proband')}
                >
                  Add proband
                </button>
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => addMember('father')}
                >
                  Add father
                </button>
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => addMember('mother')}
                >
                  Add mother
                </button>
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => addMember('child')}
                >
                  Add child
                </button>
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => addMember('relative')}
                >
                  Add relative
                </button>
              </div>
            </div>

            <div className="space-y-4">
              {members.map((member, index) => {
                const memberTitle = member.sampleId.trim() || `Member ${index + 1}`;
                const fatherOptions = members.filter(
                  (candidate) =>
                    candidate.localId !== member.localId &&
                    candidate.sampleId.trim() &&
                    (candidate.sex === 'male' || candidate.sampleId === member.fatherId)
                );
                const motherOptions = members.filter(
                  (candidate) =>
                    candidate.localId !== member.localId &&
                    candidate.sampleId.trim() &&
                    (candidate.sex === 'female' || candidate.sampleId === member.motherId)
                );
                const children = members.filter(
                  (candidate) =>
                    candidate.fatherId === member.sampleId || candidate.motherId === member.sampleId
                );

                return (
                  <article key={member.localId} className="surface-card-flat member-card">
                    <div className="member-card-header">
                      <div className="space-y-1">
                        <h3 className="section-title !text-[1.35rem]">{memberTitle}</h3>
                        <p className="text-sm text-[var(--color-text-muted)]">
                          Role preview: {rolePreviewFor(member, members)}
                        </p>
                        <div className="member-card-tags">
                          <span className="badge-chip">{member.sex}</span>
                          {member.isProband && <span className="badge-chip">Proband</span>}
                          {member.affected && <span className="badge-chip">Affected</span>}
                        </div>
                        {children.length > 0 && (
                          <p className="text-xs uppercase tracking-[0.12em] text-[var(--color-text-muted)]">
                            Children: {children.map((child) => child.sampleId || 'Unnamed').join(', ')}
                          </p>
                        )}
                      </div>
                      <button
                        type="button"
                        className="button-ghost"
                        onClick={() => removeMember(member.localId)}
                      >
                        Remove
                      </button>
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                      <label className="field-label">
                        Sample ID
                        <input
                          value={member.sampleId}
                          onChange={(event) =>
                            updateMember(member.localId, { sampleId: event.target.value })
                          }
                          placeholder={`S${index + 1}`}
                        />
                      </label>
                      <label className="field-label">
                        Sex
                        <select
                          value={member.sex}
                          onChange={(event) =>
                            updateMember(member.localId, { sex: event.target.value as Sex })
                          }
                        >
                          <option value="und">Unknown</option>
                          <option value="male">Male</option>
                          <option value="female">Female</option>
                        </select>
                      </label>
                      <label className="field-label">
                        Father
                        <select
                          value={member.fatherId}
                          onChange={(event) =>
                            updateMember(member.localId, { fatherId: event.target.value })
                          }
                        >
                          <option value="">Unknown</option>
                          {fatherOptions.map((candidate) => (
                            <option key={candidate.localId} value={candidate.sampleId}>
                              {candidate.sampleId}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="field-label">
                        Mother
                        <select
                          value={member.motherId}
                          onChange={(event) =>
                            updateMember(member.localId, { motherId: event.target.value })
                          }
                        >
                          <option value="">Unknown</option>
                          {motherOptions.map((candidate) => (
                            <option key={candidate.localId} value={candidate.sampleId}>
                              {candidate.sampleId}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>

                    <div className="flex flex-wrap gap-5 text-sm text-[var(--color-text-muted)]">
                      <label className="inline-flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={member.affected}
                          onChange={(event) =>
                            updateMember(member.localId, { affected: event.target.checked })
                          }
                        />
                        Affected
                      </label>
                      <label className="inline-flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={member.isProband}
                          onChange={(event) =>
                            updateMember(member.localId, { isProband: event.target.checked })
                          }
                        />
                        Proband
                      </label>
                    </div>
                  </article>
                );
              })}
            </div>

            <div className="action-row">
              <button
                type="button"
                className="form-button"
                disabled={loading || projectsLoading || projectOptions.length === 0}
                onClick={submitManualFamily}
              >
                {loading ? 'Saving...' : 'Create family'}
              </button>
              {userIsAdmin ? (
                <Link to="/upload-data" className="subtle-link">
                  Upload variant or BED data afterwards
                </Link>
              ) : null}
            </div>
          </div>

          <aside className="space-y-3">
            <div className="surface-card-muted">
              <div className="flex items-center justify-between gap-3">
                <h3 className="eyebrow-label">Validation</h3>
                <span className="badge-chip">
                  {validationErrors.length === 0 ? 'Consistent' : 'Needs review'}
                </span>
              </div>
              {validationErrors.length === 0 ? (
                <p className="status-note status-note--success mt-4">
                  Form is consistent and ready to submit.
                </p>
              ) : (
                <ul className="mt-4 space-y-2 text-sm text-[var(--color-variant-del)]">
                  {validationErrors.map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              )}
            </div>

            <div className="surface-card-muted">
              <div className="flex items-center justify-between gap-3">
                <h3 className="eyebrow-label">Pedigree Sketch</h3>
                <span className="text-xs uppercase tracking-[0.12em] text-[var(--color-text-muted)]">
                  Black fill = affected
                </span>
              </div>
              {pedigreeRows.length > 0 ? (
                <div
                  className="mono-panel mt-4 overflow-x-auto !bg-[rgba(255,255,255,0.92)]"
                  data-testid="pedigree-sketch"
                >
                  <Pedigree rows={pedigreeRows} />
                </div>
              ) : (
                <p className="mt-4 text-sm text-[var(--color-text-muted)]">
                  Add sample IDs to render the pedigree sketch.
                </p>
              )}
            </div>

            <div className="surface-card-muted">
              <h3 className="eyebrow-label">PED Preview</h3>
              <textarea readOnly value={pedPreview} className="mono-panel mt-4 !h-64 !text-xs" />
            </div>
          </aside>
        </div>
      )}

      {status && (
        <p
          className={`status-note ${statusTone === 'success' ? 'status-note--success' : 'status-note--error'}`}
          aria-live="polite"
        >
          {status}
        </p>
      )}
    </section>
  );
};

export default FamilyIntakePanel;
