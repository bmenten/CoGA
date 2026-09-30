/** A count with its noun, singular for one: `countOf(1, 'site')` → "1 site", `countOf(2, 'site')` → "2 sites". */
export const countOf = (count: number, one: string, many = `${one}s`): string =>
  `${count.toLocaleString()} ${count === 1 ? one : many}`;
