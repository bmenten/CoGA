// Strict-allowlist HTML sanitiser for reference-metadata snippets rendered via
// `dangerouslySetInnerHTML` (currently the clinical-CNV "Source" block).
//
// The backend already sanitises this content at ingest and read time, so this is
// defense-in-depth at the render sink: it protects the browser directly should a row
// ever reach it unsanitised. Default-deny: any element or
// attribute not on the allowlist is dropped; disallowed elements are unwrapped so their
// text is kept as inert text rather than executable markup.

const ALLOWED_TAGS = new Set([
  'A',
  'ABBR',
  'B',
  'BR',
  'CODE',
  'DIV',
  'EM',
  'I',
  'LI',
  'OL',
  'P',
  'SPAN',
  'STRONG',
  'SUB',
  'SUP',
  'U',
  'UL',
]);

// Per-tag attribute allowlist (uppercase tag → lowercase attribute names).
const ALLOWED_ATTRS: Record<string, Set<string>> = {
  A: new Set(['href', 'title']),
};

const ALLOWED_URL_SCHEMES = new Set(['http', 'https', 'mailto']);
const EMPTY_ATTRS = new Set<string>();

// Mirror of the backend `_href_is_safe`: no scheme, or a scheme on the allowlist. A ':'
// that only appears after a '/', '?' or '#' is part of a path/query/fragment, not a
// scheme, so relative references stay allowed; everything else is default-denied.
const isSafeHref = (value: string): boolean => {
  const v = value.trim();
  if (!v) return true;
  const colon = v.indexOf(':');
  if (colon === -1) return true;
  const pathSep = v.search(/[/?#]/);
  if (pathSep !== -1 && pathSep < colon) return true;
  return ALLOWED_URL_SCHEMES.has(v.slice(0, colon).trim().toLowerCase());
};

const sanitizeElement = (element: Element): void => {
  // Depth-first: sanitise children before deciding this element's fate, so an unwrapped
  // node only ever promotes already-sanitised subtrees.
  Array.from(element.children).forEach((child) => {
    sanitizeElement(child);
    const tag = child.tagName.toUpperCase();
    if (!ALLOWED_TAGS.has(tag)) {
      const parent = child.parentNode;
      if (parent) {
        while (child.firstChild) parent.insertBefore(child.firstChild, child);
        parent.removeChild(child);
      }
      return;
    }
    const allowed = ALLOWED_ATTRS[tag] ?? EMPTY_ATTRS;
    Array.from(child.attributes).forEach((attr) => {
      const name = attr.name.toLowerCase();
      if (!allowed.has(name)) {
        child.removeAttribute(attr.name);
      } else if (name === 'href' && !isSafeHref(attr.value)) {
        child.removeAttribute(attr.name);
      }
    });
  });
};

export const sanitizeHtml = (raw: string): string => {
  if (!raw) return '';
  const doc = new DOMParser().parseFromString(raw, 'text/html');
  sanitizeElement(doc.body);
  return doc.body.innerHTML;
};

export default sanitizeHtml;
