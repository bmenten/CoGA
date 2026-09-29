import { describe, expect, it } from 'vitest';

import {
  buildApiUnavailableMessage,
  getErrorMessage,
  isNetworkTransportError,
  isNotFoundError,
} from '../errorMessage';

describe('errorMessage helpers', () => {
  // #610 — only a 404 means the record does not exist; a failed request is not one.
  it('reads only an HTTP 404 as not found', () => {
    expect(isNotFoundError({ response: { status: 404, data: { detail: 'Family not found' } } })).toBe(true);
    expect(isNotFoundError({ response: { status: 500 } })).toBe(false);
    expect(isNotFoundError({ response: { status: 403 } })).toBe(false);
    expect(isNotFoundError({ request: {}, message: 'Network Error' })).toBe(false);
    expect(isNotFoundError(new Error('boom'))).toBe(false);
    expect(isNotFoundError(null)).toBe(false);
  });

  it('detects transport failures without an HTTP response', () => {
    expect(isNetworkTransportError({ request: {}, message: 'Network Error' })).toBe(true);
    expect(isNetworkTransportError({ code: 'ERR_NETWORK' })).toBe(true);
    expect(isNetworkTransportError({ response: { data: { detail: 'bad request' } } })).toBe(false);
  });

  it('returns a clear API unavailable message for transport failures', () => {
    expect(
      getErrorMessage({ request: {}, message: 'Network Error' }, 'Login failed', {
        networkFallback: buildApiUnavailableMessage('http://localhost:8000'),
      })
    ).toBe(
      'Unable to reach the API at http://localhost:8000. Check that the backend, Postgres, and ClickHouse services are running.'
    );
  });

  it('keeps HTTP error detail responses unchanged', () => {
    expect(
      getErrorMessage(
        { response: { data: { detail: 'Incorrect email or password' } } },
        'Login failed'
      )
    ).toBe('Incorrect email or password');
  });

  it('uses object-shaped backend detail messages', () => {
    expect(
      getErrorMessage({
        response: {
          data: {
            detail: {
              message: 'Renaming this member is blocked because genomic data exists.',
            },
          },
        },
      })
    ).toBe('Renaming this member is blocked because genomic data exists.');
  });
});
