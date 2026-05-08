export const cssVar = (name: string): string =>
  getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
