"""DM approval like the emails (28 Nov 2026, owner's choice "Like emails").

The DM writer routine saves each new DM in `Cold DM` with `DM status` = `waiting <YYYY-MM-DDTHH:MMZ>`.
The owner approves, edits or denies it on the outbox phone page. This step runs inside every tick and puts on the
sender's approved list (state/approved.json):
  approved <stamp>               at once (the owner tapped Approve)
  waiting <stamp> | by hand      12 hours after <stamp> (the owner edited it), with no format check: his words are final
  waiting <stamp>                12 hours after <stamp>, only if gate() passes; otherwise `held <reason>` so he sees why
A row with `Do not contact` ticked is never approved (`stopped replied`). Approved rows become `queued <stamp>`; the
12:35 UTC sync then puts them in the send queue. Uses the rows the tick has already read: no extra Airtable reads.
PUBLIC REPO: only counts are printed."""
import os, re, sys, datetime as dt
from common import CFG, _at, load, save, commit, now
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'leadgen'))
import banned

AUTO_HOURS = 12
FIELDS = ['Business', 'Cold DM', 'DM status', 'Instagram', 'Facebook', 'Do not contact']
STATUS = re.compile(r'^(waiting|approved) (\d{4}-\d{2}-\d{2}T\d{2}:\d{2})Z( \| by hand)?$')
DASHES = ('—', '–', ' - ', '-')


def log(*a):
    print('PUBLIC:', *a, flush=True)


def gate(text):
    """Checks for a DM that goes out with nobody reading it (same rules as the writer prompt). None = fine."""
    t = (text or '').replace('\r', '').strip()
    paras = [p for p in t.split('\n\n') if p.strip()]
    if len(paras) != 5:
        return f'{len(paras)} paragraphs, needs 5'
    if not re.fullmatch(r"Hi( [A-Z][A-Za-z'.]{1,24})?,", paras[0].strip()):
        return 'first line must be Hi Name,'
    if any(d in t for d in DASHES):
        return 'dash'
    if '!' in t or re.search(r'http|www\.|@', t, re.I):
        return 'exclamation, link or address'
    if t.count('?') != 1 or not t.endswith('?'):
        return 'needs one question, at the end'
    n = len(t.split())
    if n > 100 or n < 50:
        return f'{n} words, needs 50 to 100'
    q = re.findall(r'"([^"]+)"', t)
    if len(q) != 1 or not (12 <= len(q[0].split()) <= 45) or '?' in q[0]:
        return 'needs one quoted post of 12 to 45 words'
    low = t.lower()
    for need in ('by hand', 'automation', 'say yes'):
        if need not in low:
            return f'missing "{need}"'
    body = re.sub(r'"[^"]*"', '', low).split(',', 1)[-1]
    if re.search(r'\b(they|them|their|coaches|owners)\b', re.sub(r'\bfive coaches\b', '', body)):
        return 'talks about they or coaches, must say you'
    if not re.search(r'\b(instagram|facebook|linkedin|website|blog)\b', paras[1].lower()):
        return 'opening must name where the post was'
    ok, hits = banned.check(t)
    if not ok:
        return 'banned words: ' + ','.join(k for k, v in hits.items() if v)
    return None


def run(rows, yes):
    a = CFG['airtable']
    t_now = now()
    stamp = t_now.strftime('%Y-%m-%dT%H:%MZ')
    approved = set(load('approved.json', []))
    patches, add, held, waiting, stopped = {}, set(), 0, 0, 0
    for r in rows:
        f = r['f']
        m = STATUS.match((f.get('DM status') or '').strip())
        if not m or not (f.get('Cold DM') or '').strip():
            continue
        if f.get('Do not contact'):
            patches.setdefault(r['tid'], []).append({'id': r['id'], 'fields': {'DM status': 'stopped replied'}})
            stopped += 1
            continue
        kind, ts, by_hand = m.group(1), dt.datetime.strptime(m.group(2), '%Y-%m-%dT%H:%M').replace(tzinfo=dt.timezone.utc), bool(m.group(3))
        due = kind == 'approved' or (t_now - ts).total_seconds() >= AUTO_HOURS * 3600
        if not due:
            waiting += 1
            continue
        if kind == 'waiting' and not by_hand:
            bad = gate(f['Cold DM'])
            if bad:
                patches.setdefault(r['tid'], []).append({'id': r['id'], 'fields': {'DM status': f'held {bad}'[:90]}})
                held += 1
                continue
        if not (f.get('Instagram') or f.get('Facebook')):
            patches.setdefault(r['tid'], []).append({'id': r['id'], 'fields': {'DM status': 'held no Instagram or Facebook link'}})
            held += 1
            continue
        add.add(r['id'])
        patches.setdefault(r['tid'], []).append({'id': r['id'], 'fields': {'DM status': f'queued {stamp}'}})
    log('DMs: approve now', len(add), '| still waiting', waiting, '| held', held, '| stopped (replied)', stopped)
    if not yes:
        log('DRY RUN, nothing changed')
        return
    if add - approved:
        save('approved.json', sorted(approved | add))
        commit(f'approve {len(add - approved)} DMs (page or 12 h)')
    for tid, recs in patches.items():
        for i in range(0, len(recs), 10):
            _at('PATCH', f"{a['base']}/{tid}", body={'records': recs[i:i + 10], 'typecast': True})
