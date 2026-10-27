import { apiPath, raw } from './apiPath';

/**
 * Build an in-app route with every interpolated value percent-encoded as one path segment, as
 * `apiPath` builds an API path (SEC-15). React Router decodes each segment back into the
 * route's params, a `/` included, so `useParams()` gives the identifier as written.
 *
 * Interpolated raw, an identifier broke in-app navigation: a `?` or `#` in it started the
 * query or the hash, a `/` added a segment no route matches, and a `%` left the path
 * undecodable.
 *
 *   <Link to={routePath`/families/${familyId}/nipt`}>
 *
 * A value that is deliberately a query string or a pre-built route goes through `raw()`:
 *
 *   navigate(routePath`/families/${familyId}/genome${raw(search)}`)
 */
export const routePath = apiPath;

export { raw };

/**
 * `from`, a path a link carries to return to, when it is one of the app's own family pages;
 * null otherwise. Whoever makes a link chooses its `back_path` or `from`: taken as it came,
 * `https://…` or `//…` sent the Back link off the site (#526, SEC-15).
 */
export const familyPagePath = (from: string | null | undefined): string | null =>
  from && from.startsWith('/families/') && !from.includes('\\') ? from : null;
