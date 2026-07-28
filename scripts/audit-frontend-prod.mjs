#!/usr/bin/env node
// Blocking dependency-audit gate for the frontend *production* tree.
//
// Replaces a bare `npm audit --omit=dev --audit-level=high`, which is all-or-nothing: it cannot
// exempt a single advisory, so one unfixable finding would force the whole gate off. This keeps the
// gate blocking and narrows the exemption to named advisories, each justified and dated in
// SECURITY-AUDIT-ALLOWLIST.md (evidence for technical-file item TF-13).
//
// Fails when:
//   - a high/critical advisory in the production tree is not allowlisted, or
//   - an allowlist entry is past its reviewBy date (a suppression must not silently become
//     permanent), or
//   - an allowlist entry no longer matches any finding (stale — the advisory was fixed or dropped,
//     so the exemption has to go with it).
//
// Usage: node scripts/audit-frontend-prod.mjs
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const BLOCKING = new Set(['high', 'critical']);
const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..');
const allowlistPath = resolve(here, 'frontend-audit-allowlist.json');

// `npm audit` exits non-zero whenever it finds anything at/above the audit level, so a non-zero exit
// is expected here and carries the JSON report on stdout. Only a missing report is a real error.
function runAudit() {
  try {
    return execFileSync('npm', ['audit', '--omit=dev', '--json'], {
      cwd: resolve(repoRoot, 'frontend'),
      encoding: 'utf8',
      maxBuffer: 32 * 1024 * 1024,
    });
  } catch (err) {
    if (err.stdout) return err.stdout;
    throw err;
  }
}

// Advisory ids live on the object-valued `via` entries. A string `via` is a back-reference to
// another vulnerable package (e.g. react-router-dom -> "react-router") and carries no id of its
// own; it is covered transitively once the root advisory it points at is resolved.
function collectFindings(report) {
  const findings = new Map(); // GHSA id -> {ghsa, severity, packages:Set, title}
  for (const [pkg, vuln] of Object.entries(report.vulnerabilities ?? {})) {
    for (const via of vuln.via ?? []) {
      if (typeof via === 'string') continue;
      const severity = String(via.severity ?? vuln.severity ?? '').toLowerCase();
      if (!BLOCKING.has(severity)) continue;
      const ghsa = via.url?.match(/GHSA-[0-9a-z-]+/i)?.[0] ?? String(via.source ?? 'unknown');
      const existing = findings.get(ghsa) ?? { ghsa, severity, packages: new Set(), title: via.title ?? '' };
      existing.packages.add(pkg);
      findings.set(ghsa, existing);
    }
  }
  return findings;
}

const allowlist = JSON.parse(readFileSync(allowlistPath, 'utf8')).allow ?? [];
const allowed = new Map(allowlist.map((entry) => [entry.ghsa, entry]));
const findings = collectFindings(JSON.parse(runAudit()));

const errors = [];
const notes = [];

// 1. Unallowlisted high/critical findings in the shipped tree.
for (const finding of findings.values()) {
  if (allowed.has(finding.ghsa)) continue;
  errors.push(
    `${finding.severity.toUpperCase()} ${finding.ghsa} in ${[...finding.packages].join(', ')}` +
      `${finding.title ? ` — ${finding.title}` : ''}\n` +
      '    Not allowlisted. Fix it, or add a justified entry to SECURITY-AUDIT-ALLOWLIST.md and\n' +
      '    scripts/frontend-audit-allowlist.json (only if no non-breaking fix exists).',
  );
}

// 2. Expired or stale allowlist entries.
const today = new Date().toISOString().slice(0, 10);
for (const entry of allowlist) {
  if (!entry.reviewBy || entry.reviewBy < today) {
    errors.push(
      `Allowlist entry ${entry.ghsa} (${entry.package}) is past its review date ` +
        `(reviewBy: ${entry.reviewBy ?? 'missing'}, today: ${today}).\n` +
        `    Re-justify with a new reviewBy date, or carry out the flip action: ${entry.flipAction ?? 'n/a'}`,
    );
  } else if (!findings.has(entry.ghsa)) {
    errors.push(
      `Allowlist entry ${entry.ghsa} (${entry.package}) no longer matches any production finding.\n` +
        '    The advisory was fixed or the dependency dropped — remove the entry here and in\n' +
        '    SECURITY-AUDIT-ALLOWLIST.md so the register stays accurate.',
    );
  } else {
    notes.push(`${entry.ghsa} (${entry.package}) — suppressed until ${entry.reviewBy}: ${entry.reason}`);
  }
}

for (const note of notes) {
  console.log(`::warning::frontend prod audit — allowlisted ${note}`);
}

if (errors.length > 0) {
  console.error('\nFrontend production dependency audit FAILED:\n');
  for (const error of errors) console.error(`  - ${error}\n`);
  console.error('See SECURITY-AUDIT-ALLOWLIST.md for the suppression policy.\n');
  process.exit(1);
}

console.log(
  `Frontend production dependency audit passed — ` +
    `${findings.size} high/critical finding(s), all allowlisted and in review date.`,
);
