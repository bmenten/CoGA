// The API proxy forwards browser requests to the backend over an internal
// hostname (e.g. http://backend:8000). When the backend answers with a redirect
// (a 3xx Location), that Location is built from the host the backend saw — the
// internal one — so passing it through verbatim sends the browser to
// `http://backend:8000/...`, which it cannot resolve ("Unable to reach the API").
//
// Rewriting any Location that points at the internal backend origin into a
// same-origin relative path makes the browser follow the redirect back through
// the proxy, which is resilient to *any* backend redirect (trailing-slash
// normalisation, auth, future routes) rather than a single special case.
//
// The path keeps one leading slash (UI-3): `http://backend:8000//host` became
// `//host`, and `http://backend:8000/\host` `/\host`, which a browser reads as
// another host. And only the backend's own origin is rewritten: a Location on a
// host whose name merely starts with it, such as `http://backend:8000.example`,
// is another host's.
export function rewriteProxyLocation(location, backendUrl) {
  if (typeof location !== 'string' || location === '') {
    return location;
  }
  const base = String(backendUrl || '').replace(/\/+$/, '');
  if (!base || !location.startsWith(base)) {
    return location;
  }
  const rest = location.slice(base.length);
  if (rest !== '' && !/^[/?#]/.test(rest)) {
    return location;
  }
  return `/${rest.replace(/^[/\\]+/, '')}`;
}
