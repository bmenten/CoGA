// API path segments are encoded — #521: an imported identifier could redirect a call.

import { describe, expect, it } from 'vitest';

import { apiPath, raw } from '../apiPath';

describe('apiPath', () => {
  it('encodes every interpolated value as one path segment', () => {
    // Raw, this id would make the browser resolve the DELETE to /admin/families/F1.
    expect(apiPath`/admin/samples/${'../families/F1'}`).toBe('/admin/samples/..%2Ffamilies%2FF1');
    expect(apiPath`/families/${'F 1?x=1#y'}/hpo`).toBe('/families/F%201%3Fx%3D1%23y/hpo');
    expect(apiPath`/panels/${42}`).toBe('/panels/42');
  });

  it('passes a deliberate query string through raw()', () => {
    expect(apiPath`/families/${'F1'}/small-variants${raw('?page=1&project_id=p')}`).toBe(
      '/families/F1/small-variants?page=1&project_id=p',
    );
  });

  it('leaves an ordinary identifier unchanged', () => {
    expect(apiPath`/families/${'FAM-001'}/report/sign-outs/${3}`).toBe('/families/FAM-001/report/sign-outs/3');
  });
});
