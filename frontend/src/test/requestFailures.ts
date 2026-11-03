import { fireEvent, screen, waitFor } from '@testing-library/react';
import { expect } from 'vitest';

/** An API error as axios rejects it: the message, and the response's status and body. */
export const httpError = (status: number, message = `Request failed with status code ${status}`, data?: unknown) =>
  Object.assign(new Error(message), { response: data === undefined ? { status } : { status, data } });

/** A request the server failed (500 unless said otherwise). */
export const serverError = (status = 500, message?: string): Promise<never> =>
  Promise.reject(httpError(status, message));

/** The family request of a family the API does not know, or that is outside the caller's projects. */
export const familyNotFound = (): Promise<never> =>
  Promise.reject(httpError(404, undefined, { detail: 'Family not found' }));

/**
 * #610: a failed request reads as one, never as a family that does not exist. The page shows
 * `heading`, the error said as a failed request (when `message` is given, the text it shows
 * before that), no "Family not found", and a Retry that asks again: `requests` counts the
 * failing requests.
 */
export const expectFailedRequestRetried = async ({
  heading,
  message,
  requests,
}: {
  heading: string;
  message?: string;
  requests: () => number;
}): Promise<void> => {
  expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument();
  if (message !== undefined) {
    expect(screen.getByText(`${message} This is a failed request, not a missing family.`)).toBeInTheDocument();
  }
  expect(screen.queryByText('Family not found')).not.toBeInTheDocument();
  const before = requests();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(requests()).toBeGreaterThan(before));
};

/** A family the API does not know reads "Family not found", with nothing to retry. */
export const expectFamilyNotFound = async (): Promise<void> => {
  expect(await screen.findByRole('heading', { name: 'Family not found' })).toBeInTheDocument();
  expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
};
