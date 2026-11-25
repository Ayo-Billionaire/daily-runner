"""Mover (24 Nov 2026): emails the owner APPROVED in the Lumina holding list go into the live campaign.

  holding list 73198061 (no campaign uses it) -> the routine writes each email there, first line status `edited`.
  25 Nov 2026 (Ayo): no manual approval. This job takes every row whose first line status is `edited` (written by the routine)
  or `approved` (approved by hand), checks them against the banned words and the email rules, imports the contact into the
  live campaign's list, sets the approved first line and the subject, reconciles the campaign, reads back, then marks Airtable.
  The subject comes from the Airtable marker `in Lumina <date> | subject: <subject>` (Lumina cannot return it).

  LIVE only when the plain file `mover.live` exists in the repo root; without it the job is a DRY RUN and changes nothing.
PUBLIC REPO: only counts are printed (PUBLIC: lines). Names, emails and ids never reach the log."""
import json, os, re, sys, time, uuid, urllib.request, urllib.error, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'leadgen'))
import banned
from common import CFG, _at
import tick   # lumina(), CAMPAIGN, LUMINA, lumina_key(), live_campaign_emails(), DASHES

HOLDING = '73198061-2507-4b18-a45e-cbd5809704ea'
MAX_PER_RUN = 20          # per tick, three ticks a day; the campaign sends 50 a day at most
LIVE = os.path.exists(os.path.join(os.path.dirname(HERE), 'mover.live'))
MARK = re.compile(r'^in Lumina (\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2})Z)? \| subject: (.*?)( \| by hand)?$')
AUTO_HOURS = 12           # an email nobody approved or denied goes out this long after it was written
TABLES = ('Email and Socials', 'Email Only')


def log(*a):
    print('PUBLIC:', *a, flush=True)


def check_text(subject, text):
    """Everything that must hold before a real send, with nobody reading it first. Returns None when fine, else a short reason.
    Two styles pass: 1 = point + other topics + system line + closing; 2 = point + a finished post in quotes + two more offered."""
    if not subject or len(subject.split()) > 7 or '|' in subject or ':' in subject or subject != subject.lower() or '{{' in subject:
        return 'subject'
    if any(d in text or d in subject for d in tick.DASHES):
        return 'dash'
    if '!' in text:
        return 'exclamation'
    if text.count('?') != 1:
        return 'question marks'
    if not text.rstrip().endswith('\nAyo'):
        return 'sign off'
    if '{{' in text or '}}' in text or '<' in text or '>' in text or 'http' in text.lower() or '@' in text:
        return 'merge field or link'
    first = text.split('\n', 1)[0]
    if not re.match(r"^Hi( [A-Z][A-Za-z'.\-]{1,24})?,$", first):
        return 'greeting'
    n = len(text.split())
    if n > 100 or n < 45:
        return 'length'
    quoted = re.findall(r'"([^"]+)"', text)
    if len(quoted) > 1:
        return 'quotes'
    if quoted:
        if '?' in quoted[0] or len(quoted[0].split()) > 40 or len(quoted[0].split()) < 12:
            return 'quoted post'
    ok, hits = banned.check(text + '\n' + subject)
    if not ok:
        return 'banned ' + ','.join(k for k, v in hits.items() if v and (k != 'avoid' or len(v) >= 2))
    return None


def holding_rows(list_id):
    rows, cursor = [], None
    while True:
        path = f'/api/lead-lists/{list_id}/leads?limit=200' + (f'&cursor={cursor}' if cursor else '')
        s, d = tick.lumina('GET', path)
        if s != 200:
            raise RuntimeError(f'list read {s}')
        rows += d.get('rows', [])
        cursor = d.get('nextCursor')
        if not cursor:
            return rows


def upload_csv(csv_text):
    """multipart POST /api/warehouse/imports/upload, field `file`."""
    b = uuid.uuid4().hex
    body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="move.csv"\r\nContent-Type: text/csv\r\n\r\n'
            f'{csv_text}\r\n--{b}--\r\n').encode()
    req = urllib.request.Request(tick.LUMINA + '/api/warehouse/imports/upload', method='POST', data=body,
                                 headers={'Authorization': 'Bearer ' + tick.lumina_key(), 'Content-Type': f'multipart/form-data; boundary={b}'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def csv_cell(x):
    return '"' + (x or '').replace('"', '""').replace('\n', ' ') + '"'


def marked_rows():
    """Airtable rows the routine put into the holding list: email -> (table id, record id, subject)."""
    a = CFG['airtable']
    out = {}
    for tname in TABLES:
        tid = a['tables'][tname]
        offset = None
        while True:
            q = {'pageSize': 100, 'fields[]': ['Email', 'Drafted to'], 'filterByFormula': "LEFT({Drafted to}, 10)='in Lumina '"}
            if offset:
                q['offset'] = offset
            j = _at('GET', f"{a['base']}/{tid}", query=q)
            for r in j['records']:
                m = MARK.match((r['fields'].get('Drafted to') or '').strip())
                em = (r['fields'].get('Email') or '').strip().lower()
                if m and em:
                    if m.group(2):
                        ts = dt.datetime.fromisoformat(m.group(1) + 'T' + m.group(2) + ':00+00:00')
                    elif m.group(1) == dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d'):
                        ts = dt.datetime.now(dt.timezone.utc)   # date only marker written today: treat as just written, never early
                    else:
                        ts = dt.datetime.fromisoformat(m.group(1) + 'T00:00:00+00:00')
                    out[em] = (tid, r['id'], m.group(3).strip(), ts, bool(m.group(4)))
            offset = j.get('offset')
            if not offset:
                break
    return out


def run():
    marks = marked_rows()
    rows = holding_rows(HOLDING)
    now = dt.datetime.now(dt.timezone.utc)
    def ready(r):
        st = r.get('firstLineStatus')
        em = (r.get('email') or '').lower()
        if em not in marks:
            return False
        return st == 'approved' or (st == 'edited' and now - marks[em][3] >= dt.timedelta(hours=AUTO_HOURS))
    approved = [r for r in rows if ready(r)]
    waiting = sum(1 for r in rows if r.get('firstLineStatus') == 'edited' and (r.get('email') or '').lower() in marks) - sum(1 for r in approved if r.get('firstLineStatus') == 'edited')
    log('holding rows', len(rows), '| ready to move', len(approved), '| still waiting for review', max(waiting, 0), '| mode', 'LIVE' if LIVE else 'DRY')
    if not approved:
        return

    s, camp = tick.lumina('GET', f'/api/campaigns/{tick.CAMPAIGN}')
    if s != 200 or camp.get('status') != 'active':
        raise RuntimeError(f'target campaign not readable or not active ({s})')
    list_id = camp['leadListIds'][0]
    elsewhere = tick.live_campaign_emails()
    s, st0 = tick.lumina('GET', f'/api/campaigns/{tick.CAMPAIGN}/stats')
    before = st0.get('recipients', 0)

    plan, refused = [], {}
    for r in approved:
        em = r['email'].lower()
        tid, rid, subject, _ts, by_hand = marks[em]
        # Ayo's own edits and approvals are never blocked: the checks only guard emails that send by themselves.
        human = by_hand or r.get('firstLineStatus') == 'approved'
        bad = (None if ((r.get('firstLine') or '').strip() and subject) else 'empty') if human else check_text(subject, r.get('firstLine') or '')
        if not bad and em in elsewhere:
            bad = 'already in campaign ' + elsewhere[em]
        if bad:
            refused[bad.split(' ')[0]] = refused.get(bad.split(' ')[0], 0) + 1
            continue
        plan.append({'row': r, 'email': em, 'tid': tid, 'rid': rid, 'subject': subject, 'text': r['firstLine']})
    plan = plan[:MAX_PER_RUN]
    log('plan: move', len(plan), '| refused', sum(refused.values()), dict(refused))
    if not LIVE or not plan:
        if not LIVE:
            log('DRY RUN, nothing changed')
        return

    # 1. import into the campaign list (same contact id, so the approved line comes with it)
    csv_text = 'Name,Email,Company\n' + '\n'.join(
        ','.join(csv_cell(x) for x in (p['row'].get('fullName'), p['email'], p['row'].get('companyName'))) for p in plan)
    up = upload_csv(csv_text)
    s, d = tick.lumina('POST', f"/api/warehouse/imports/{up['batchId']}/start", {'mapping': up['mapping'], 'listId': list_id})
    if s not in (200, 201):
        raise RuntimeError(f'import start {s}')
    for _ in range(24):
        time.sleep(5)
        s, d = tick.lumina('GET', f"/api/warehouse/imports/{up['batchId']}")
        if d.get('status') in ('done', 'failed'):
            break
    if d.get('status') != 'done':
        raise RuntimeError('import not done: ' + str(d.get('status')))

    # 2. set first line (approved) and subject on each contact
    by_email = {(r.get('email') or '').lower(): r['id'] for r in holding_rows(list_id)}
    ok = {}
    for p in plan:
        cid = by_email.get(p['email'])
        if not cid:
            continue
        s1, _ = tick.lumina('PATCH', f'/api/leads/{cid}/first-line', {'firstLine': p['text'], 'approve': True})
        s2, _ = tick.lumina('PATCH', f'/api/leads/{cid}/subject-line', {'subjectLine': p['subject']})
        if s1 == 200 and s2 == 200:
            ok[p['email']] = cid
        time.sleep(0.3)

    # 3. reconcile the campaign, read back
    s, _ = tick.lumina('PATCH', f'/api/campaigns/{tick.CAMPAIGN}', {'leadListIds': camp['leadListIds']})
    if s != 200:
        raise RuntimeError(f'campaign audience PATCH failed ({s})')
    time.sleep(5)
    back = {(r.get('email') or '').lower(): r for r in holding_rows(list_id)}
    proven = {e for e in ok if e in back and back[e].get('firstLineStatus') == 'approved'
              and back[e].get('firstLine') == next(p['text'] for p in plan if p['email'] == e)}
    s, st1 = tick.lumina('GET', f'/api/campaigns/{tick.CAMPAIGN}/stats')
    log('Lumina read back: recipients', before, '->', st1.get('recipients', 0), '| proven in list', len(proven), 'of', len(plan))

    # 4. Airtable markers
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    a = CFG['airtable']
    by_t = {}
    for p in plan:
        if p['email'] in proven:
            by_t.setdefault(p['tid'], []).append({'id': p['rid'], 'fields': {'Drafted to': f"moved {stamp} | subject: {p['subject']}"}})
    for tid, recs in by_t.items():
        for i in range(0, len(recs), 10):
            _at('PATCH', f"{a['base']}/{tid}", body={'records': recs[i:i + 10], 'typecast': True})
    log('moved and marked', sum(len(v) for v in by_t.values()), '| failed', len(plan) - len(proven))
    if len(proven) < len(plan):
        raise RuntimeError(f'{len(plan) - len(proven)} leads not proven in the campaign list')


if __name__ == '__main__':
    run()
