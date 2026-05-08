export function formatGt(gt?: string): string {
  if (gt === '1/1' || gt === '1|1') return 'Hom';
  if (
    gt === '1/0' ||
    gt === '0/1' ||
    gt === '1|0' ||
    gt === '0|1'
  )
    return 'Het';
  return 'WT';
}
{/* what with missing values ./. ? */}