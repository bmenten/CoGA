export function compareChromosomes(a: string, b: string): number {
  const rank = (chr: string): number => {
    const cleaned = chr.replace(/^chr/i, '').toUpperCase();
    const num = parseInt(cleaned, 10);
    if (!Number.isNaN(num)) return num;
    if (cleaned === 'X') return 23;
    if (cleaned === 'Y') return 24;
    if (cleaned === 'MT' || cleaned === 'M') return 25;
    return Number.MAX_SAFE_INTEGER;
  };
  const diff = rank(a) - rank(b);
  return diff !== 0 ? diff : a.localeCompare(b);
}

