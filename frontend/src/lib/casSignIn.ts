// Sign-in through the organisation's CAS server, such as login.ugent.be.
//
// The backend says whether CoGA uses it (GET /auth/config: the CAS login page for CoGA's
// service and the CAS sign-out page; none of it secret). The browser goes to the CAS login page
// and comes back to /login with a one-time service ticket, which the API checks with the CAS
// server (POST /auth/cas/session) before it starts the session with a token of its own.
//
// The CAS server sends the browser back to exactly the service it was given, /login without a
// query string, so the page to open after the sign-in waits in this tab's session storage.
import api from './api';
import type { CasSignInOut } from './apiSchema.generated';

export type CasSignIn = CasSignInOut;

const NEXT_PATH_KEY = 'coga.cas-sign-in.next';
// A service ticket as CAS servers write it; anything else in the query string is not one.
const SERVICE_TICKET = /^ST-[A-Za-z0-9._-]{1,250}$/;

/** The service ticket the CAS server sent the browser back with, if the query names one. */
export const casTicketFrom = (search: string): string | null => {
  const ticket = new URLSearchParams(search).get('ticket');
  return ticket && SERVICE_TICKET.test(ticket) ? ticket : null;
};

/** Go to the CAS server's login page; the sign-in comes back to /login. */
export const startCasSignIn = (config: CasSignIn, nextPath: string): void => {
  try {
    window.sessionStorage.setItem(NEXT_PATH_KEY, nextPath);
  } catch {
    // Without session storage the sign-in still works; it opens the dashboard.
  }
  window.location.assign(config.login_url);
};

export interface CompletedCasSignIn {
  accessToken: string;
  nextPath: string | null;
}

/** Have the API check the ticket and start the session; the page asked for before it. */
export const completeCasSignIn = async (ticket: string): Promise<CompletedCasSignIn> => {
  const response = await api.post('/auth/cas/session', { ticket });
  let nextPath: string | null = null;
  try {
    nextPath = window.sessionStorage.getItem(NEXT_PATH_KEY);
    window.sessionStorage.removeItem(NEXT_PATH_KEY);
  } catch {
    nextPath = null;
  }
  return { accessToken: String(response.data.access_token), nextPath };
};
