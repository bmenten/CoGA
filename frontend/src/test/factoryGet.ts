import { beforeAll, beforeEach, type Mock } from 'vitest';

export type ApiGet = (url: string, config?: unknown) => Promise<{ data: unknown }>;

/**
 * Keeps the answers a test file's `vi.mock('…/lib/api')` factory gives `api.get`, and puts them
 * back before each test, so an override one test sets cannot leak into the next (a leaked
 * `mockImplementation` made a test's outcome depend on the order the tests ran in). Call it in
 * the file's `describe` block; a test overrides some URLs with `get.mockImplementation`, falling
 * back to `factory` for the others.
 */
export function useFactoryGet(apiGet: unknown): { get: Mock<ApiGet>; factory: ApiGet } {
  const get = apiGet as Mock<ApiGet>;
  let kept: ApiGet | undefined;
  beforeAll(() => {
    kept = get.getMockImplementation();
  });
  beforeEach(() => {
    get.mockImplementation(kept!);
  });
  return { get, factory: (url, config) => kept!(url, config) };
}
