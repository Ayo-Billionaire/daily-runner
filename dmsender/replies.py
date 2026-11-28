"""Cross channel stop (28 Nov 2026, owner's go): a coach who replied to an email gets no DM.

Runs inside every tick. Asks Lumina who replied since the last check (GET /api/campaigns/replies, read only),
keeps the replied addresses in encrypted state/replies.json, and ticks `Do not contact` on their Airtable lead row,
which the DM sender's sync already skips. Lumina itself stops the email follow ups on a reply (stopOnReply).
Airtable is only called when there is a new reply (free plan: about 1,000 calls a month).
PUBLIC REPO: only counts are printed."""
import datetime as dt
from common import CFG, _at, load, save, commit, now

DM_TABLES = ('Email and Socials',)          # lead tables that have both an Email column and a Do not contact column


def log(*a):
    print('PUBLIC:', *a, flush=True)


def run(lumina):
    st = load('replies.json', {'last': (now() - dt.timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ'), 'seen': [], 'emails': []})
    s, d = lumina('GET', f"/api/campaigns/replies?since={st['last']}&limit=200")
    if s != 200:
        raise RuntimeError(f'Lumina replies read failed ({s})')
    new = [r for r in d.get('replies', []) if r.get('recipientId') not in st['seen']]
    log('email replies since last check:', len(new))
    if not new:
        return st
    a = CFG['airtable']
    ticked = 0
    for r in new:
        em = (r.get('email') or '').strip().lower()
        st['seen'].append(r['recipientId'])
        if em and em not in st['emails']:
            st['emails'].append(em)
        if r.get('repliedAt') and r['repliedAt'] > st['last']:
            st['last'] = r['repliedAt']
        if not em or "'" in em:
            continue
        for tname in DM_TABLES:
            tid = a['tables'].get(tname)
            if not tid:
                continue
            j = _at('GET', f"{a['base']}/{tid}", query={'filterByFormula': f"LOWER({{Email}})='{em}'", 'fields[]': ['Email'], 'pageSize': 5})
            recs = [{'id': x['id'], 'fields': {'Do not contact': True}} for x in j.get('records', [])]
            if recs:
                _at('PATCH', f"{a['base']}/{tid}", body={'records': recs})
                ticked += len(recs)
    save('replies.json', st)
    commit('replies')
    log('lead rows marked do not contact:', ticked)
    return st
