"""Approval tick (22 Nov 2026): the owner ticks `Approve` on a lead row in Airtable; this schedules everything for that lead.

  email: RETIRED 24 Nov (emails live in the Lumina holding list, dmsender/mover.py moves approved ones; Airtable holds no email text, so this branch never fires).
         Old text: lead added to the live Lumina campaign's list, subject + approved first line set, campaign audience reconciled,
         read back. The campaign already holds email 1 and the three follow ups (same subject, so they thread as Re:).
  DM:    record id added to state/approved.json, which is the only thing the DM sender is allowed to send.
  The row gets `Scheduled on` only after the read back passes. A failed row stays unticked-for-scheduling and is retried.

  python3 dmsender/tick.py --setup   add the `Approve` and `Scheduled on` columns to the three lead tables (once)
  python3 dmsender/tick.py           DRY: prints what it would do, changes nothing
  python3 dmsender/tick.py --yes     does it

PUBLIC REPO: only counts are printed (PUBLIC: lines). Names, emails and ids never reach the log."""
import json, os, re, sys, time, urllib.request, urllib.error, datetime as dt
from common import CFG, _at, airtable_rows, load, save, commit, today
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'leadgen'))
import banned   # 24 Nov: every DM goes through the banned words check before it is approved for sending

CAMPAIGN = 'eae37ccd-118b-465c-af9e-9aab162e168e'   # Coaches US: owners, post written for them (live, 7 steps)
LUMINA = 'https://luminnaleads.com'
MAX_PER_RUN = 40                                    # the campaign sends 50 a day at most
YES = '--yes' in sys.argv
DASHES = ('—', '–', ' - ')


def log(*a):
    print('PUBLIC:', *a, flush=True)


def lumina_key():
    k = os.environ.get('LUMINALEADS_API_KEY', '').strip()
    if not k:
        f = os.path.expanduser('~/.secrets/api-keys.env')
        if os.path.exists(f):
            for line in open(f):
                if line.startswith('LUMINALEADS_API_KEY='):
                    k = line.split('=', 1)[1].strip().strip('"\'')
    if not k:
        raise RuntimeError('no LUMINALEADS_API_KEY')
    return k


def lumina(method, path, body=None):
    req = urllib.request.Request(LUMINA + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': 'Bearer ' + lumina_key(), 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            t = r.read().decode()
            return r.status, (json.loads(t) if t else {})
    except urllib.error.HTTPError as e:
        t = e.read().decode()
        try:
            return e.code, json.loads(t)
        except Exception:
            return e.code, {'raw': t[:200]}


def setup_columns():
    a = CFG['airtable']
    meta = _at('GET', f"meta/bases/{a['base']}/tables")['tables']
    for t in meta:
        if t['id'] not in a['tables'].values() or t['name'] not in ('Email and Socials', 'Email Only', 'Socials Only (no email)'):
            continue
        have = {f['name'] for f in t['fields']}
        if 'Approve' not in have:
            _at('POST', f"meta/bases/{a['base']}/tables/{t['id']}/fields",
                {'name': 'Approve', 'type': 'checkbox', 'options': {'icon': 'check', 'color': 'greenBright'}})
            log('added Approve to', t['name'])
        if 'Scheduled on' not in have:
            _at('POST', f"meta/bases/{a['base']}/tables/{t['id']}/fields", {'name': 'Scheduled on', 'type': 'singleLineText'})
            log('added Scheduled on to', t['name'])


def first_name(email_text):
    m = re.match(r'\s*Hi ([^,\n]+),', email_text or '')
    return (m.group(1).strip() if m else '') or 'there'


def check_email(subject, text):
    """Last guard before a real send. The real gates run when the draft is written; this refuses obvious breakage."""
    if not subject or len(subject) > 200:
        return 'no subject'
    if not text or not text.rstrip().endswith('Ayo'):
        return 'email does not end with Ayo'
    if any(d in text or d in subject for d in DASHES):
        return 'dash in copy'
    if '{{' in text or '}}' in text:
        return 'unfilled merge field'
    return None


def live_campaign_emails():
    """Emails already sitting in the lists of every other active campaign, so nobody is in two sequences."""
    s, camps = lumina('GET', '/api/campaigns')
    camps = camps if isinstance(camps, list) else camps.get('campaigns', [])
    seen = {}
    for c in camps:
        if c.get('status') != 'active' or c['id'] == CAMPAIGN:
            continue
        for lid in c.get('leadListIds') or []:
            s, d = lumina('GET', f'/api/leads?listId={lid}&limit=1000')
            rows = d if isinstance(d, list) else d.get('leads') or d.get('items') or []
            for r in rows:
                e = (r.get('email') or '').lower()
                if e:
                    seen[e] = c['id'][:8]
    return seen


def run_tick():
    if '--setup' in sys.argv:
        setup_columns()
        return
    a = CFG['airtable']
    fields = ['Business', 'City', 'Email', 'Draft subject', 'Draft email', 'Cold DM', 'Instagram', 'Facebook', 'Approve', 'Scheduled on']
    rows, calls = airtable_rows(fields)
    todo = [r for r in rows if r['f'].get('Approve') and not r['f'].get('Scheduled on')]
    log('ticked and not yet scheduled:', len(todo), '| Airtable calls', calls)
    if not todo:
        return
    todo = todo[:MAX_PER_RUN]

    s, camp = lumina('GET', f'/api/campaigns/{CAMPAIGN}')
    if s != 200 or camp.get('status') != 'active':
        raise RuntimeError(f'target campaign not readable or not active ({s})')
    list_id = camp['leadListIds'][0]
    s, st0 = lumina('GET', f'/api/campaigns/{CAMPAIGN}/stats')
    before = st0.get('recipients', 0)
    elsewhere = live_campaign_emails()

    approved = set(load('approved.json', []))
    plan, failed, to_enrol = [], [], []
    for r in todo:
        f = r['f']
        email = (f.get('Email') or '').strip().lower()
        item = {'r': r, 'email': None, 'dm': False, 'note': []}
        if email and f.get('Draft email'):
            bad = check_email(f.get('Draft subject'), f['Draft email'])
            if bad:
                item['note'].append('email refused: ' + bad)
            elif email in elsewhere:
                item['note'].append('email skipped: already in campaign ' + elsewhere[email])
            else:
                item['email'] = email
        if (f.get('Cold DM') or '').strip() and (f.get('Instagram') or f.get('Facebook')):
            ok_dm, hits = banned.check(f['Cold DM'])
            if ok_dm:
                item['dm'] = True
            else:
                item['note'].append('DM refused: banned words ' + ','.join(k for k, v in hits.items() if v))
        plan.append(item)
    log('plan: emails', sum(1 for p in plan if p['email']), '| DMs', sum(1 for p in plan if p['dm']),
        '| nothing to do', sum(1 for p in plan if not p['email'] and not p['dm']))
    if not YES:
        log('DRY RUN, nothing changed')
        for p in plan:
            if p['note']:
                log('note:', p['note'][0][:60])
        return

    # 1. emails into Lumina
    ok_email = {}
    for p in plan:
        if not p['email']:
            continue
        f = p['r']['f']
        body = {'listId': list_id, 'fullName': first_name(f['Draft email']), 'firstName': first_name(f['Draft email']),
                'email': p['email'], 'companyName': (f.get('Business') or '')[:120]}
        if f.get('City'):
            body['location'] = f['City'][:80]
        s, d = lumina('POST', '/api/leads', body)
        lead = d.get('id') or (d.get('lead') or {}).get('id') or d.get('leadId')
        if s == 409 and lead:
            s = 200  # already a lead (their dedup): carry on, the list check below proves membership
        if s not in (200, 201) or not lead:
            failed.append((p, 'lead ' + str(s)))
            continue
        s1, _ = lumina('PATCH', f'/api/leads/{lead}/first-line', {'firstLine': f['Draft email'], 'approve': True})
        s2, _ = lumina('PATCH', f'/api/leads/{lead}/subject-line', {'subjectLine': f['Draft subject']})
        if s1 != 200 or s2 != 200:
            failed.append((p, f'first-line {s1} subject {s2}'))
            continue
        ok_email[p['email']] = lead
        time.sleep(0.3)

    # 2. one reconcile for the whole batch, then read back
    if ok_email:
        s, _ = lumina('PATCH', f'/api/campaigns/{CAMPAIGN}', {'leadListIds': camp['leadListIds']})
        if s != 200:
            raise RuntimeError(f'campaign audience PATCH failed ({s})')
        time.sleep(5)
        s, d = lumina('GET', f'/api/leads?listId={list_id}&limit=1000')
        rows_l = d if isinstance(d, list) else d.get('leads') or d.get('items') or []
        in_list = {(x.get('email') or '').lower() for x in rows_l}
        s, st1 = lumina('GET', f'/api/campaigns/{CAMPAIGN}/stats')
        after = st1.get('recipients', 0)
        missing = [e for e in ok_email if e not in in_list]
        log('Lumina read back: recipients', before, '->', after, '| in list', len(ok_email) - len(missing), 'of', len(ok_email))
        for e in missing:
            ok_email.pop(e)
        if after - before < len(ok_email):
            log('WARNING recipients rose by', after - before, 'for', len(ok_email), 'leads; marking only those proven in the list')

    # 3. DMs onto the sender's approved list
    new_dm = {p['r']['id'] for p in plan if p['dm']}
    if new_dm - approved:
        save('approved.json', sorted(approved | new_dm))
        commit(f'approve {len(new_dm - approved)} DMs (owner tick)')

    # 4. mark rows that are fully done
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%MZ')
    by_t, done = {}, 0
    for p in plan:
        email_ok = (not p['email']) or p['email'] in ok_email
        if not email_ok or any(p is x[0] for x in failed):
            continue
        what = [w for w, on in (('email', bool(p['email'])), ('DM', p['dm'])) if on]
        if not what:
            continue   # nothing could be scheduled for this row: leave it for the owner to see
        fld = {'Scheduled on': stamp + ' ' + '+'.join(what)}
        if p['email']:
            fld['Draft subject'] = ''   # 24 Nov: Lumina now holds the email (first line + subject, read back above), so Airtable keeps DM text only
            fld['Draft email'] = ''
        by_t.setdefault(p['r']['tid'], []).append({'id': p['r']['id'], 'fields': fld})
        done += 1
    for tid, recs in by_t.items():
        for i in range(0, len(recs), 10):
            _at('PATCH', f"{a['base']}/{tid}", body={'records': recs[i:i + 10]})
    log('rows marked scheduled:', done, '| failed', len(failed), '| left alone', len(plan) - done - len(failed))
    if failed:
        raise RuntimeError(f'{len(failed)} rows failed: ' + '; '.join(x[1] for x in failed[:5]))


def main():
    run_tick()
    if YES and '--setup' not in sys.argv:
        err = None
        try:
            import mover      # 24 Nov: emails approved in the Lumina holding list go into the live campaign (dry unless mover.live exists)
            mover.run()
        except Exception as e:   # the Sent move below must still run
            err = e
            print('PUBLIC: mover failed:', type(e).__name__, flush=True)
        import sentmove   # after every tick: leads whose channels are all done move to the Sent table
        sentmove.run()
        if err:
            raise err


if __name__ == '__main__':
    main()
