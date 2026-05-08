import { describe, expect, it } from 'vitest';

import {
  buildApiUnavailableMessage,
  getErrorMessage,
  isNetworkTransportError,
} from '../errorMessage';

describe('errorMessage helpers', () => {
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
});
