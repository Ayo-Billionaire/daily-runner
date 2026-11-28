"""Sent table (22 Nov 2026). A lead whose email is in a live Lumina campaign and/or whose DM has been sent moves to the `Sent` table.

A row moves only when EVERY channel it has is done:
  email done = its address sits in an active Lumina campaign list
  DM done    = `Approve DM` is ticked (the 12:35 sync ticks it once every platform of that coach is sent or skipped)
A row with a pending channel stays put. Moving gives a row a new record id, so a DM that is still waiting would lose its place.

Copy first, verify the copies by Original record ID, only then delete the originals. The Sent row keeps the original table
and id, like the Replied tables do.

  python3 dmsender/sentmove.py            DRY: counts only
  python3 dmsender/sentmove.py --yes      moves them
PUBLIC REPO: only counts are printed."""
import json, os, sys, urllib.parse
from common import CFG, _at, airtable_rows, load
import tick

YES = '--yes' in sys.argv
FIELDS = ['Business', 'City', 'Email', 'Website', 'Instagram', 'Facebook', 'LinkedIn', 'Draft subject', 'Draft email', 'Cold DM',
          'Approve DM', 'Scheduled on', 'DM status', 'Do not contact']
KEEP = ['Business', 'City', 'Email', 'Website', 'Instagram', 'Facebook', 'LinkedIn', 'Draft subject', 'Draft email', 'Cold DM']
LONG = {'Draft email', 'Cold DM'}


def log(*a):
    print('PUBLIC:', *a, flush=True)


def sent_table_id(create=True):
    base = CFG['airtable']['base']
    for t in _at('GET', f'meta/bases/{base}/tables')['tables']:
        if t['name'] == 'Sent':
            return t['id']
    if not create or not YES:
        return None
    fields = [{'name': 'Business', 'type': 'singleLineText'}] + \
             [{'name': n, 'type': 'multilineText' if n in LONG else 'singleLineText'} for n in KEEP[1:]] + \
             [{'name': n, 'type': 'singleLineText'} for n in ('Channel', 'Sent on', 'Campaign', 'Original table', 'Original record ID')]
    t = _at('POST', f'meta/bases/{base}/tables', {'name': 'Sent', 'fields': fields})
    log('created Sent table')
    return t['id']


def campaign_membership():
    """email -> (campaign id first 8, launch date) for every active Lumina campaign, this tick's campaign included."""
    s, camps = tick.lumina('GET', '/api/campaigns')
    camps = camps if isinstance(camps, list) else camps.get('campaigns', [])
    out = {}
    for c in camps:
        if c.get('status') != 'active':
            continue
        for lid in c.get('leadListIds') or []:
            s, d = tick.lumina('GET', f'/api/leads?listId={lid}&limit=1000')
            for r in (d if isinstance(d, list) else d.get('leads') or d.get('items') or []):
                e = (r.get('email') or '').lower()
                if e:
                    out[e] = (c['id'][:8], (c.get('launchedAt') or '')[:10])
    return out


def run():
    a = CFG['airtable']
    rows, calls = airtable_rows(FIELDS)
    member = campaign_membership()
    sent_dm = load('sent.json', {})
    dm_date = {}
    for v in sent_dm.values():
        if v.get('status') == 'sent':
            dm_date[v['rid']] = max(dm_date.get(v['rid'], ''), v.get('date', ''))
    ready = []
    for r in rows:
        f = r['f']
        em = (f.get('Email') or '').strip().lower()
        has_dm = bool((f.get('Cold DM') or '').strip() and (f.get('Instagram') or f.get('Facebook')))
        if not em and not has_dm:
            continue
        if em and em not in member:
            continue
        if has_dm and not f.get('Approve DM') and not f.get('Do not contact'):
            continue
        # 28 Nov: a lead with Instagram or Facebook but no DM yet waits here for the DM writer routine, which reads only the
        # lead tables. It moves once the writer marks it skipped, or the coach is marked Do not contact.
        dm_st = (f.get('DM status') or '').strip()
        if (f.get('Instagram') or f.get('Facebook')) and not has_dm and not (dm_st.startswith(('skipped', 'stopped')) or f.get('Do not contact')):
            continue
        chan = '+'.join(x for x, on in (('email', bool(em)), ('DM', has_dm)) if on)
        camp, launched = member.get(em, ('', ''))
        when = max(launched, dm_date.get(r['id'], '')) or ''
        ready.append({'r': r, 'channel': chan, 'campaign': camp, 'sent_on': when})
    log('rows read', len(rows), '| ready to move to Sent', len(ready), '| Airtable calls', calls)
    if not YES or not ready:
        if not YES:
            log('DRY RUN, nothing changed')
        return
    tid = sent_table_id()
    base = a['base']
    # 1. copy
    for i in range(0, len(ready), 10):
        recs = []
        for p in ready[i:i + 10]:
            f = p['r']['f']
            fields = {k: f[k] for k in KEEP if f.get(k)}
            fields.update({'Channel': p['channel'], 'Sent on': p['sent_on'], 'Campaign': p['campaign'],
                           'Original table': p['r']['table'], 'Original record ID': p['r']['id']})
            recs.append({'fields': fields})
        _at('POST', f'{base}/{tid}', body={'records': recs})
    # 2. verify every copy exists
    have, off = set(), None
    while True:
        q = {'pageSize': 100, 'fields[]': ['Original record ID']}
        if off:
            q['offset'] = off
        j = _at('GET', f'{base}/{tid}', query=q)
        have |= {x['fields'].get('Original record ID') for x in j['records']}
        off = j.get('offset')
        if not off:
            break
    ok = [p for p in ready if p['r']['id'] in have]
    log('copies verified in Sent:', len(ok), 'of', len(ready))
    if len(ok) != len(ready):
        raise RuntimeError(f'only {len(ok)} of {len(ready)} copies verified; originals NOT deleted')
    # 3. delete the originals
    by = {}
    for p in ok:
        by.setdefault(p['r']['tid'], []).append(p['r']['id'])
    for t, ids in by.items():
        for i in range(0, len(ids), 10):
            _at('DELETE', f'{base}/{t}?' + urllib.parse.urlencode([('records[]', x) for x in ids[i:i + 10]]))
    log('moved to Sent:', len(ok))


if __name__ == '__main__':
    run()
