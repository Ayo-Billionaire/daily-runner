"""First-email drafter (24 Nov 2026). Takes the email text the writing routine left on a lead row in Airtable (`Draft subject` + `Draft email`),
saves it as a draft in one of the four noqodepro mailboxes, then CLEARS it from Airtable and writes `Drafted to` (the mailbox name only).
Airtable ends up holding DM text only. Ayo opens the mailbox Drafts folder and sends by hand; followups.py takes it from there.

  - Never touches a lead that is already in any Lumina campaign list (no one gets two sequences).
  - One mailbox per lead, kept for every follow up. Least-loaded mailbox first, MAX_PER_BOX drafts a day each.
  - Refuses a draft that has a dash, an unfilled merge field, no subject or does not end with Ayo (same last guard as the approval tick).
PUBLIC REPO: counts only.

  python3 draftbox.py          does it
  python3 draftbox.py --dry    counts, writes nothing
"""
import sys, os, re, json, urllib.request, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mboxlib as MB
import leadgen as L
import common

DRY = '--dry' in sys.argv
MAX_PER_BOX = 30
TABLES = ('Email and Socials', 'Email Only')
DASHES = ('—', '–', ' - ')
LUMINA = 'https://luminnaleads.com'
ROUTINE_TAG = 'ROUTINE DRAFT'     # the writing routine puts this in `Claude cross-check` when it saves a draft for mailbox drafting


def log(*a):
    print('PUBLIC:', *a, flush=True)


def lumina_key():
    k = os.environ.get('LUMINALEADS_API_KEY', '').strip()
    if not k:
        f = os.path.expanduser('~/.secrets/api-keys.env')
        if os.path.exists(f):
            m = re.search(r'^LUMINALEADS_API_KEY=(.+)$', open(f).read(), re.M)
            k = m.group(1).strip().strip('"\'') if m else ''
    return k


def lumina_get(path):
    req = urllib.request.Request(LUMINA + path, headers={'Authorization': 'Bearer ' + lumina_key(), 'User-Agent': 'runner'})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def lumina_emails():
    """Every address sitting in a list of any campaign, live or not: those leads already have a sequence."""
    seen = set()
    camps = lumina_get('/api/campaigns')
    camps = camps if isinstance(camps, list) else camps.get('campaigns', [])
    for c in camps:
        for lid in c.get('leadListIds') or []:
            d = lumina_get(f'/api/leads?listId={lid}&limit=1000')
            rows = d if isinstance(d, list) else d.get('leads') or d.get('items') or []
            seen |= {(r.get('email') or '').lower() for r in rows if r.get('email')}
    return seen


def check(subject, text):
    if not subject or len(subject) > 200:
        return 'no subject'
    if not text or not text.rstrip().endswith('Ayo'):
        return 'does not end with Ayo'
    if any(d in text or d in subject for d in DASHES):
        return 'dash in copy'
    if '{{' in text or '}}' in text:
        return 'unfilled merge field'
    return None


def ensure_drafted_col(base):
    live = L.at('GET', f'meta/bases/{base}/tables')['tables']
    cols = {}
    for t in live:
        if t['name'] in TABLES:
            have = {f['name'] for f in t['fields']}
            if 'Drafted to' not in have and not DRY:
                L.at('POST', f"meta/bases/{base}/tables/{t['id']}/fields", {'name': 'Drafted to', 'type': 'singleLineText'})
            cols[t['name']] = (t['id'], 'Drafted to' in have)
    return cols


def main():
    cfg = L.config(); base = cfg['airtable']['base']
    boxes = cfg.get('mailboxes', [])
    if not boxes:
        log('draftbox: no mailboxes in PRIVATE_CONFIG'); return
    tids = ensure_drafted_col(base)
    led = common.load('mailbox_ledger', {})
    today = dt.date.today().isoformat()
    used = {b: sum(1 for v in led.values() if v.get('mb') == b and v.get('drafted_on') == today) for b in boxes}
    rows = []
    for name, (tid, has_col) in tids.items():
        formula = "AND({Draft email}!='', {Draft subject}!='', {Email}!='', {Drafted to}='')" if has_col else "AND({Draft email}!='', {Draft subject}!='', {Email}!='')"
        d = L.at('GET', f'{base}/{tid}', query={'pageSize': 100, 'filterByFormula': formula, 'fields[]': ['Business', 'Email', 'Draft subject', 'Draft email', 'Claude cross-check']})
        rows += [(name, r) for r in d['records'] if ROUTINE_TAG in r['fields'].get('Claude cross-check', '')]   # old style drafts stay where they are
    log('draftbox: rows with a draft waiting', len(rows))
    if not rows:
        return
    elsewhere = lumina_emails()
    stats = dict(drafted=0, in_lumina=0, refused=0, duplicate=0, full=0)
    conns, patches, waiting = {}, {}, {}
    for name, r in rows:
        f = r['fields']; lead = f['Email'].strip().lower()
        if lead in elsewhere:
            stats['in_lumina'] += 1; continue
        if lead in led:
            stats['duplicate'] += 1; continue
        if check(f['Draft subject'], f['Draft email']):
            stats['refused'] += 1; continue
        box = min(boxes, key=lambda b: used[b])
        if used[box] >= MAX_PER_BOX:
            stats['full'] += 1; continue
        if DRY:
            used[box] += 1; stats['drafted'] += 1; continue
        M = conns.get(box) or conns.setdefault(box, MB.connect(box))
        if box not in waiting:
            waiting[box] = MB.draft_targets(M)
        if lead in waiting[box]:
            stats['duplicate'] += 1; continue
        if MB.append_draft(M, box, lead, f['Draft subject'].strip(), f['Draft email'].strip() + '\n'):
            used[box] += 1; stats['drafted'] += 1
            led[lead] = {'mb': box, 'subject': f['Draft subject'].strip(), 'drafted_on': today, 'done': False}
            patches.setdefault(name, []).append({'id': r['id'], 'fields': {'Draft subject': '', 'Draft email': '', 'Drafted to': box.split('@')[0] + ' ' + today}})
    for M in conns.values():
        try:
            M.logout()
        except Exception:
            pass
    if not DRY and patches:
        common.save('mailbox_ledger', led)       # saved before Airtable is cleared, so a failed clear never loses the record
        common.commit('mailbox ledger')
        for name, recs in patches.items():
            for i in range(0, len(recs), 10):
                L.at('PATCH', f'{base}/{tids[name][0]}', {'records': recs[i:i + 10], 'typecast': True})
    log('draftbox', stats, '| per mailbox today', sorted(used.values()))


if __name__ == '__main__':
    main()
