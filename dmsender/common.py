"""Shared helpers: config, state files, Airtable, Resend alerts, git commits of state."""
import json, os, subprocess, time, datetime as dt, urllib.request, urllib.parse, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, 'state')
os.makedirs(STATE, exist_ok=True)
CFG = json.load(open(os.path.join(ROOT, 'config.json')))
# Public repo: alert addresses, Airtable ids and the phone's address live in the PRIVATE_CONFIG secret
# (local runs read private_config.json, which git ignores).
_priv = os.environ.get('PRIVATE_CONFIG', '').strip()
if not _priv and os.path.exists(os.path.join(ROOT, 'private_config.json')):
    _priv = open(os.path.join(ROOT, 'private_config.json')).read()
if _priv:
    CFG.update(json.loads(_priv))


def state_key():
    """Fernet key that encrypts every state file, the browser login and the run log. Secret STATE_KEY in
    Actions; on the Mac it is DM_STATE_KEY in ~/.secrets/api-keys.env."""
    k = os.environ.get('STATE_KEY', '').strip()
    if not k:
        f = os.path.expanduser('~/.secrets/api-keys.env')
        if os.path.exists(f):
            for line in open(f):
                if line.startswith('DM_STATE_KEY='):
                    k = line.split('=', 1)[1].strip().strip('"\'')
    if not k:
        raise RuntimeError('no STATE_KEY: state files are encrypted and cannot be read')
    from cryptography.fernet import Fernet
    return Fernet(k.encode())


def now():
    return dt.datetime.now(dt.timezone.utc)


def today():
    return now().date().isoformat()


def load(name, default):
    """State files are stored encrypted (state/<name>.enc) because the repo is public."""
    p = os.path.join(STATE, name + '.enc')
    if not os.path.exists(p):
        return default
    with open(p, 'rb') as f:
        return json.loads(state_key().decrypt(f.read()))


def save(name, data):
    if load(name, None) == data:
        return  # encryption output changes every time; skip unchanged files so git sees no change
    raw = json.dumps(data, indent=1, sort_keys=True).encode()
    with open(os.path.join(STATE, name + '.enc'), 'wb') as f:
        f.write(state_key().encrypt(raw))


def commit(msg):
    """Commit and push state/ so a later run never re-sends. Retries on push races."""
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        return
    subprocess.run(['git', 'add', 'state'], cwd=ROOT, check=True)
    if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT).returncode == 0:
        return
    # Public repo: commit messages never carry names; the real message goes to the private run log.
    print('commit:', msg)
    subprocess.run(['git', 'commit', '-q', '-m', 'state update'], cwd=ROOT, check=True)
    for i in range(5):
        if subprocess.run(['git', 'push', '-q'], cwd=ROOT).returncode == 0:
            return
        subprocess.run(['git', 'pull', '-q', '--rebase'], cwd=ROOT)
        time.sleep(2 + i * 2)
    raise RuntimeError('could not push state after 5 tries')


# ---------- Airtable (free plan: 1,000 calls a month, so callers keep calls low) ----------
def _at(method, path, body=None, query=None):
    tok = os.environ['AIRTABLE_TOKEN']
    url = 'https://api.airtable.com/v0/' + path + ('?' + urllib.parse.urlencode(query, doseq=True) if query else '')
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < 2:
                time.sleep(31 if e.code == 429 else 5)
                continue
            raise RuntimeError(f'Airtable {method} {path} -> HTTP {e.code}: {e.read().decode()[:300]}')


def airtable_rows(fields):
    a = CFG['airtable']
    out, calls = [], 0
    for tname, tid in a['tables'].items():
        offset, pos = None, 0
        while True:
            q = {'pageSize': 100, 'view': a['view'], 'fields[]': fields}
            if offset:
                q['offset'] = offset
            j = _at('GET', f"{a['base']}/{tid}", query=q)
            calls += 1
            for r in j['records']:
                pos += 1
                out.append({'table': tname, 'tid': tid, 'id': r['id'], 'pos': pos, 'f': r['fields']})
            offset = j.get('offset')
            if not offset:
                break
    return out, calls


def airtable_tick(items):
    """items: list of (tid, record_id). PATCH Approve DM in tens, return records read back as ticked."""
    a = CFG['airtable']
    ok, calls = set(), 0
    by = {}
    for tid, rid in items:
        by.setdefault(tid, []).append(rid)
    for tid, ids in by.items():
        for i in range(0, len(ids), 10):
            j = _at('PATCH', f"{a['base']}/{tid}", body={'records': [{'id': r, 'fields': {'Approve DM': True}} for r in ids[i:i + 10]]})
            calls += 1
            for rec in j['records']:
                if rec['fields'].get('Approve DM') is True:
                    ok.add(rec['id'])
    return ok, calls


# ---------- Alerts ----------
def email(subject, text):
    key = os.environ.get('RESEND_API_KEY')
    if not key:
        print('NO RESEND KEY, would email:', subject)
        return False
    body = {'from': CFG['alert_from'], 'to': CFG['alert_to'], 'subject': subject, 'text': text}
    req = urllib.request.Request('https://api.resend.com/emails', method='POST', data=json.dumps(body).encode(),
                                 headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                          'User-Agent': 'dm-sender/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print('alert sent:', subject, json.load(r).get('id'))
            return True
    except urllib.error.HTTPError as e:
        print('ALERT FAILED', e.code, e.read().decode()[:300])
        return False


def run_link():
    s, r, i = os.environ.get('GITHUB_SERVER_URL'), os.environ.get('GITHUB_REPOSITORY'), os.environ.get('GITHUB_RUN_ID')
    return f'{s}/{r}/actions/runs/{i}' if s and r and i else '(local run)'


# ---------- Daily cap ----------
def daily_cap(d=None):
    d = d or now().date()
    sd = CFG.get('start_date')
    if not sd:
        return 0
    days = (d - dt.date.fromisoformat(sd)).days
    if days < 0:
        return 0
    days -= load('ramp.json', {}).get('shift_days', 0)  # each Meta warning drops back one step
    if days < 0:
        days = 0
    cap = 0
    for start, n in CFG['ramp']:
        if days >= start:
            cap = n
    return cap


def is_send_day(d=None):
    d = d or now().date()
    return not (CFG.get('weekdays_only') and d.weekday() >= 5)
