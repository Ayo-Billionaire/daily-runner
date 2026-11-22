// runner-clock (17 Nov 2026). GitHub never fired the dm-sender schedule on this repo, so this Worker is the clock.
// Every minute (UTC; the DM jobs weekdays only, finder and tick every day):
//  1. starts the workflow due at this minute (12:35 sync, send slots from config.json slots_utc, 22:45 summary),
//     but SKIPS a send slot while another send run is still running or waiting (two runs at once broke the state save);
//  2. a run still waiting for an ubuntu-slim machine after 4 minutes is cancelled and restarted on ubuntu-24.04
//     (slim had no free machine on 17 Nov; standard minutes cost 4x, so slim stays the first choice).
const SLOTS = ["13:04", "13:31", "13:57", "14:26", "14:49", "15:18", "15:44", "16:13", "16:37", "17:06", "17:33", "17:58", "18:27", "18:51", "19:19", "19:46", "20:12", "20:41", "21:07", "21:36"];
const REPO = 'Ayo-Billionaire/daily-runner'; // same name the repo URL shows
const FALLBACK = 'ubuntu-24.04';
// 17 Nov 2026 18:51 UTC: GitHub refused ubuntu-slim with "recent account payments have failed or your spending limit needs
// to be increased"; ubuntu-24.04 runs fine. Start on 24.04 until the owner's billing page says slim is covered.
const FIRST = 'ubuntu-24.04';

function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${REPO}${path}`, { ...init,
    headers: { Authorization: 'Bearer ' + env.GH_DISPATCH_TOKEN, Accept: 'application/vnd.github+json',
               'User-Agent': 'runner-clock', 'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json' } });
}

async function note(env, msg) { console.log(msg); }

async function dispatch(env, wf, inputs) {
  const r = await gh(env, `/actions/workflows/${wf}/dispatches`, { method: 'POST', body: JSON.stringify({ ref: 'main', inputs }) });
  await note(env, `${new Date().toISOString()} dispatch ${wf} ${JSON.stringify(inputs)} -> ${r.status}`);
  return r.status;
}

async function activeRuns(env, wf) {
  const out = [];
  for (const st of ['in_progress', 'queued', 'waiting', 'pending']) {
    const r = await gh(env, `/actions/workflows/${wf}/runs?status=${st}&per_page=10`);
    if (r.ok) out.push(...(await r.json()).workflow_runs);
  }
  return out;
}

async function rescueStuck(env, wf, inputs, now) {
  // A run whose job never got a machine: status queued, older than 4 min, its job still queued on ubuntu-slim.
  const r = await gh(env, `/actions/workflows/${wf}/runs?status=queued&per_page=5`);
  if (!r.ok) return;
  for (const run of (await r.json()).workflow_runs) {
    if (now - Date.parse(run.created_at) < 4 * 60000) continue;
    const j = await gh(env, `/actions/runs/${run.id}/jobs`);
    if (!j.ok) continue;
    const jobs = (await j.json()).jobs || [];
    const waitingSlim = jobs.length > 0 && jobs.every(x => x.status === 'queued' && (x.labels || []).includes('ubuntu-slim'));
    if (!waitingSlim) continue;
    const c = await gh(env, `/actions/runs/${run.id}/cancel`, { method: 'POST' });
    await note(env, `cancel stuck slim run ${run.id} -> ${c.status}`);
    await dispatch(env, wf, { ...inputs, runner: FALLBACK });
  }
}

const SEND = { limit: '10', dry_run: 'false', force: 'false', only: '' };

// 22 Nov 2026: GitHub's own schedule has never fired on this repo, so the lead finder and the approval tick start here too, every
// day of the week. Tick slots (UTC): 11:30 lands before the 12:35 DM sync, 17:30 and 21:30 catch the rest of the day.
const TICKS = ['11:30', '17:30', '21:30'];

function due(d) {
  const day = d.getUTCDay();
  const hm = d.toISOString().slice(11, 16);
  const out = [];
  if (hm === '04:23') out.push(['leads.yml', null]);
  if (TICKS.includes(hm)) out.push(['daily.yml', { job: 'tick' }]);
  if (day === 0 || day === 6) return out;
  if (hm === '12:35') out.push(['daily.yml', { job: 'sync' }]);
  if (hm === '22:45') out.push(['daily.yml', { job: 'summary' }]);
  if (SLOTS.includes(hm)) out.push(['send.yml', SEND]);
  return out;
}

async function tick(env, when) {
  for (const [wf, inputs] of due(new Date(when))) {
    if (wf === 'send.yml') {
      const busy = await activeRuns(env, 'send.yml');
      if (busy.length) { await note(env, `skip send slot: run ${busy[0].id} still ${busy[0].status}`); continue; }
    }
    await dispatch(env, wf, inputs === null ? {} : { ...inputs, runner: FIRST });
  }
  await rescueStuck(env, 'send.yml', SEND, when);
  await rescueStuck(env, 'daily.yml', { job: 'sync' }, when);
}

export default {
  async scheduled(ev, env, ctx) { ctx.waitUntil(tick(env, ev.scheduledTime)); },
  async fetch(req, env) {
    const u = new URL(req.url);
    if (u.pathname === '/next') {
      const now = new Date(); const list = [];
      for (let m = 0; m < 60 * 24 * 3 && list.length < 5; m++) {
        const d = new Date(Math.ceil(now.getTime() / 60000) * 60000 + m * 60000);
        for (const [wf] of due(d)) list.push(`${d.toISOString().slice(0, 16)}Z ${wf}`);
      }
      return new Response(list.join('\n'));
    }
    return new Response('runner-clock ok');
  },
};
