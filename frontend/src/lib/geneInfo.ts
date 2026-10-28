/**
 * The gene page for a gene named on a finding, in the family's context: every finding card
 * links its gene name here, so the gene reads and leads the same on every page.
 */
export const geneInfoHref = (
  symbol: string | null | undefined,
  familyId?: string,
  projectId?: string | null,
): string | null => {
  const gene = (symbol || '').trim();
  if (!gene) return null;
  const params = new URLSearchParams({ gene });
  if (familyId) params.set('family_id', familyId);
  if (projectId) params.set('project_id', projectId);
  return `/genes?${params.toString()}`;
};
