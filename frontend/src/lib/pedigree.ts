/** One PED line as written: family, individual, father, mother, sex and phenotype. */
export interface PedRow {
  fid: string;
  iid: string;
  pid: string;
  mid: string;
  sex: string;
  phen: string;
}

/** The rows of a PED text, one per non-blank line, split on whitespace. */
export const parsePedigree = (pedigree?: string | null): PedRow[] => {
  if (!pedigree) return [];
  return pedigree
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => {
      const [fid, iid, pid, mid, sex, phen] = line.trim().split(/\s+/);
      return { fid, iid, pid, mid, sex, phen };
    });
};
