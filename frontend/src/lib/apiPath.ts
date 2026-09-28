/**
 * Build an API path with every interpolated value percent-encoded as one path
 * segment (#521).
 *
 * Identifiers reach API paths from imported data (sample and family ids from VCFs and
 * pedigrees) and from the URL. Interpolated raw, an id containing `/`, `?`, `#` or `..`
 * changes which endpoint is called: `DELETE /admin/samples/${id}` with id
 * `../families/F1` is resolved by the browser to `DELETE /admin/families/F1`.
 *
 *   api.delete(apiPath`/admin/samples/${sampleId}`)
 *
 * A value that is deliberately a query string or a pre-built path goes through `raw()`:
 *
 *   api.get(apiPath`/families/${familyId}/small-variants${raw(queryString)}`)
 */
const RAW = Symbol('apiPath.raw');

export interface RawPathPart {
  readonly [RAW]: string;
}

export const raw = (value: string): RawPathPart => ({ [RAW]: value });

const isRaw = (value: unknown): value is RawPathPart =>
  typeof value === 'object' && value !== null && RAW in value;

export function apiPath(strings: TemplateStringsArray, ...values: unknown[]): string {
  let path = strings[0];
  values.forEach((value, index) => {
    path += (isRaw(value) ? value[RAW] : encodeURIComponent(String(value))) + strings[index + 1];
  });
  return path;
}
