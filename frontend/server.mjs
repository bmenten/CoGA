import express from 'express';
import path from 'node:path';
import { Readable, pipeline } from 'node:stream';
import { fileURLToPath } from 'node:url';

import { rewriteProxyLocation } from './proxyLocation.mjs';
import { PROXY_OWNED_HEADERS, forwardedHeaders, proxyTimeoutMs } from './proxyRequest.mjs';
import { securityHeaders } from './securityHeaders.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = process.env.PORT || 3000;
const BACKEND_URL = (process.env.BACKEND_URL || 'http://backend:8000').replace(/\/+$/, '');
const app = express();

const distPath = path.join(__dirname, 'dist');
const hopByHopHeaders = new Set([
  'connection',
  'content-length',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

app.use('/api', async (req, res) => {
  const targetUrl = `${BACKEND_URL}${req.originalUrl}`;
  const headers = new Headers();

  for (const [name, value] of Object.entries(req.headers)) {
    const lowered = name.toLowerCase();
    if (!value || hopByHopHeaders.has(lowered) || PROXY_OWNED_HEADERS.has(lowered)) {
      continue;
    }
    if (Array.isArray(value)) {
      value.forEach((entry) => headers.append(name, entry));
    } else {
      headers.set(name, value);
    }
  }
  // One clean client address for the backend's throttling and audit log (#521).
  for (const [name, value] of Object.entries(forwardedHeaders(req))) {
    if (value) headers.set(name, value);
  }

  // A backend that never answers must not hold the request open forever, and a
  // client that goes away should not leave the backend working for nobody (#521).
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, proxyTimeoutMs());
  res.on('close', () => {
    clearTimeout(timer);
    if (!res.writableFinished) controller.abort();
  });

  try {
    const requestInit = {
      method: req.method,
      headers,
      redirect: 'manual',
      signal: controller.signal,
    };
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      requestInit.body = req;
      requestInit.duplex = 'half';
    }

    const upstream = await fetch(targetUrl, requestInit);
    // The time limit is on the backend starting to answer; a long download may stream.
    clearTimeout(timer);
    res.status(upstream.status);
    upstream.headers.forEach((value, name) => {
      if (hopByHopHeaders.has(name.toLowerCase())) {
        return;
      }
      // A redirect Location built from the internal backend host (backend:8000)
      // is unreachable from the browser; rewrite it to a same-origin path so the
      // browser follows the redirect back through this proxy.
      if (name.toLowerCase() === 'location') {
        res.setHeader(name, rewriteProxyLocation(value, BACKEND_URL));
        return;
      }
      res.setHeader(name, value);
    });

    if (!upstream.body) {
      res.end();
      return;
    }
    // pipeline, not pipe: an upstream reset mid-stream used to be an unhandled stream
    // error that could take the whole server down (#521). Now it ends this response.
    pipeline(Readable.fromWeb(upstream.body), res, (error) => {
      if (error && !controller.signal.aborted) {
        console.error(`API proxy stream failed for ${req.method} ${req.originalUrl}`, error);
      }
    });
  } catch (error) {
    clearTimeout(timer);
    if (res.headersSent || res.writableEnded) {
      res.destroy();
      return;
    }
    if (controller.signal.aborted && !timedOut) {
      // The client went away; there is no one to answer.
      return;
    }
    console.error(`API proxy failed for ${req.method} ${req.originalUrl}`, error);
    // The backend address stays in the server log; the browser needs only the outcome.
    res.status(timedOut ? 504 : 502).json({
      detail: timedOut
        ? 'The backend API did not respond in time.'
        : 'Unable to reach the backend API.',
    });
  }
});

// SPA security headers — applied to the app shell + static assets below. The /api
// proxy above already returned/ended its responses, so it keeps forwarding the
// backend's own headers rather than these page-level ones.
app.use(securityHeaders);

// Serve static assets from the Vite build output
app.use(express.static(distPath));

// All other routes should return the main index.html, allowing
// React Router to handle client side navigation. Express 5 uses
// `path-to-regexp@6`, which does not accept "*" string paths. Use
// a regular expression to match any remaining route instead.
app.get(/.*/, (_req, res) => {
  // `root` keeps send's dotfile rule to the file name: an absolute path through a
  // dot-directory (a checkout under ~/.something) would otherwise answer 404.
  res.sendFile('index.html', { root: distPath });
});

// Only start listening when run directly (`node server.mjs`); importing this module
// (e.g. from a test) must not bind a port.
if (process.argv[1] === __filename) {
  app.listen(PORT, () => {
    console.log(`Frontend running on port ${PORT}`);
  });
}

export { app };
