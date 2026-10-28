// Evidence from agent-owned post-deploy checks. Publication never waits for verification.
import fs from 'node:fs';

/** JSON lines of `file` (unparsable lines skipped); [] when it does not exist. */
export function readRows(file) {
  let text;
  try { text = fs.readFileSync(file, 'utf8'); } catch { return []; }
  const rows = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    try { rows.push(JSON.parse(line)); } catch { /* a torn last line is skipped */ }
  }
  return rows;
}

/** The newest agent-owned check for this exact commit decides the evidence badge. */
export function verdict({ head, rows }) {
  const row = [...rows].reverse().find(row => row.commit === head);
  if (row?.state === 'passed') return { state: 'verified', detail: 'selected post-deploy tests' };
  if (row?.state === 'failed') return { state: 'tests-red', stage: 'post-deploy', detail: row.job };
  return { state: 'unverified', ...(row?.state === 'running' ? { detail: 'selected tests running' } : {}) };
}

/** Landing ledger row (scripts/dev/landing.py landed.jsonl) that produced `commit`, or null. */
export function landingOf(commit, landed) {
  for (let i = landed.length - 1; i >= 0; i--) if (landed[i].commit === commit) return landed[i];
  return null;
}
