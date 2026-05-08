import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = process.env.PORT || 3000;
const app = express();

const distPath = path.join(__dirname, 'dist');

// Serve static assets from the Vite build output
app.use(express.static(distPath));

// All other routes should return the main index.html, allowing
// React Router to handle client side navigation. Express 5 uses
// `path-to-regexp@6`, which does not accept "*" string paths. Use
// a regular expression to match any remaining route instead.
app.get(/.*/, (_req, res) => {
  res.sendFile(path.join(distPath, 'index.html'));
});

app.listen(PORT, () => {
  console.log(`Frontend running on port ${PORT}`);
});
