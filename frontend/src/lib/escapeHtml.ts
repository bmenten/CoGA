/**
 * Escape a value for interpolation into an HTML string (#521).
 *
 * d3 tooltips are built as HTML (`selection.html(...)`), and the values in them come
 * from imported files — SV types, chromosome names, gene and panel names. The CSP
 * blocks inline script, but markup in a value could still restyle or spoof the tooltip.
 */
const ENTITIES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

export const escapeHtml = (value: unknown): string =>
  String(value ?? '').replace(/[&<>"']/g, (char) => ENTITIES[char]);
