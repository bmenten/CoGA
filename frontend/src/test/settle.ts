import { act } from '@testing-library/react';

/**
 * Lets a page run until it asks for nothing more: each round lets the answers in (React Query
 * passes them on after a macrotask) and the requests they enable go out. A request {@link held}
 * stays unanswered. For a check that something is NOT shown: by then the page has had every
 * answer it will get, so what it would show with them is on screen.
 *
 * `get` is the mocked API `get`, whose calls are counted.
 */
export const settle = async (get: { mock: { calls: unknown[] } }): Promise<void> => {
  for (let round = 0, quiet = 0; quiet < 3; round += 1) {
    if (round === 100) throw new Error('the page kept asking for more');
    const asked = get.mock.calls.length;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
    });
    quiet = get.mock.calls.length === asked ? quiet + 1 : 0;
  }
};

/** An answer held back: a request that waits on `answered` stays unanswered until `release`. */
export const held = () => {
  let release = () => {};
  const answered = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { answered, release };
};
