// The localhost game's build verdict: what the integrator says about the commit the open tab runs
// (scripts/dev/integrator.py rows in ~/turku3d/wt/.registry/integrations.jsonl). Pure functions; the service injects
// git ancestry. `isAncestor(a, b)` is true when commit a is b or one of its ancestors.
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

/**
 * Verification of `head` (the newest integration row covering it decides):
 *  - verified: that row is green with no advisory failure (or skipped: nothing in it reaches the live build), or no row
 *    covers it and it is in the live release;
 *  - tests-red: that row is green (built and published) but advisory checks failed; `stage` names the first;
 *  - failed: that row is red and its culprit is in head, data-red, or an infrastructure failure; `stage` names it;
 *  - unverified: no row covers it yet; `detail` says so when an integration covering it is running.
 * A row covers head when head is in from..to (in `to`, not in `from`).
 */
export function verdict({ head, live, rows, running, isAncestor }) {
  for (const row of [...rows].reverse()) {
    if (!row.from || !row.to || !isAncestor(head, row.to) || isAncestor(head, row.from)) continue;
    const stage = row.failure?.stage ?? (row.failure ? 'integration' : undefined);
    if (row.result === 'green' && row.advisory?.length) {
      const stages = [...new Set(row.advisory.map(entry => entry.stage))];
      return { state: 'tests-red', stage: stages.join(', '), detail: `integration ${row.id}` };
    }
    if (row.result === 'green') return { state: 'verified', detail: `integration ${row.id}` };
    if (row.result === 'skipped') return { state: 'verified', detail: 'no live change' };
    if (row.result === 'red' && row.culprit && isAncestor(row.culprit, head)) {
      return { state: 'failed', stage, detail: `culprit ${row.culprit.slice(0, 7)}${row.culprit_task ? ` (${row.culprit_task})` : ''}` };
    }
    if (row.result === 'data-red') return { state: 'failed', stage: `${stage} (data root)`, detail: row.data_cause };
    if (row.result === 'failed') return { state: 'failed', stage };
    break; // red, but head is before the culprit: the batch's good prefix waits for the next integration
  }
  if (live && isAncestor(head, live)) return { state: 'verified', detail: `live ${live.slice(0, 7)}` };
  if (running?.to && isAncestor(head, running.to)) {
    return { state: 'unverified', detail: running.stage ? `verifying: ${running.stage}` : 'verifying' };
  }
  return { state: 'unverified' };
}

/** The running integration's last stage from its log (`== <stage>: ...` lines), or null. */
export function runningStage(log) {
  let stage = null;
  try {
    for (const line of fs.readFileSync(log, 'utf8').split('\n')) {
      if (line.startsWith('== ') && line.includes(':')) stage = line.slice(3).split(':', 1)[0];
    }
  } catch { return null; }
  return stage;
}

/** Landing ledger row (scripts/dev/landing.py landed.jsonl) that produced `commit`, or null. */
export function landingOf(commit, landed) {
  for (let i = landed.length - 1; i >= 0; i--) if (landed[i].commit === commit) return landed[i];
  return null;
}
