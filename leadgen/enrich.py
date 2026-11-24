"""Enrich step (24 Nov 2026): fills the facts the drafting routine needs, for each new lead, in new Airtable columns.

  Owner first name   from the email address or the About page ("I'm Jen", "My name is Jen")
  Site summary       the site's own description plus the opening of its About page, about 900 characters
  Latest IG posts    the 3 newest Instagram captions with their dates (Firecrawl profile read, 1 credit)
  Last IG post date  newest post date
  Posting gap days   days between that date and today
  Enriched on        the day it was done

Queue lives in the encrypted state file `enrich_state`. leadgen.py adds every lead it writes; the first run also seeds the queue from the
three lead tables. Each run takes ENRICH_PER_RUN leads. PUBLIC REPO: only counts are printed, never names, emails or captions.

  python3 enrich.py           does it
  python3 enrich.py --sample  prints what it would write for 3 leads, writes nothing
"""
import sys, os, re, json, datetime as dt, concurrent.futures as cf
import html as _html
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import leadgen as L
import common

ENRICH_PER_RUN = 200            # sites are free to read; Instagram is the limited part (IG_READS)
IG_READS = 40                      # Firecrawl reads for Instagram per run (4,000 credits a month across the keys, the finder uses 60 a day)
FIELDS_OF = {}
LEAD_TABLES = ('Email and Socials', 'Email Only', 'Socials Only (no email)')
COLS = {'Owner first name': 'singleLineText', 'Site summary': 'multilineText', 'Latest IG posts': 'multilineText',
        'Last IG post date': 'singleLineText', 'Posting gap days': 'singleLineText', 'Enriched on': 'singleLineText'}
NOT_NAMES = {'coach', 'coaching', 'life', 'wellness', 'info', 'hello', 'contact', 'team', 'admin', 'office', 'support', 'mail', 'email', 'the', 'my', 'live',
             'your', 'best', 'our', 'advanced', 'mind', 'next', 'new', 'more', 'what', 'why', 'how', 'this', 'that', 'here', 'all', 'one', 'join', 'start', 'take', 'get', 'find', 'let', 'make', 'meet', 'welcome', 'hello', 'about', 'home', 'free', 'book', 'work', 'top', 'real', 'simply', 'just', 'brave', 'bold', 'rise', 'shine', 'bloom', 'grow', 'thrive', 'health', 'business', 'career', 'mindset', 'leadership', 'executive', 'success', 'happy', 'true', 'inner', 'vital', 'dr', 'ms', 'mr', 'mrs'}
BIZ_WORDS = {'institute', 'academy', 'coaching', 'center', 'centre', 'group', 'llc', 'inc', 'school', 'studio', 'collective', 'solutions', 'consulting',
             'wellness', 'mastery', 'mind', 'advanced', 'international', 'association', 'foundation', 'network', 'training', 'practice', 'ventures', 'partners'}
NAME_RE = re.compile(r"(?:\bI['’]m|\bI am|\bMy name is|\bHi,? I['’]m|\bHello,? I['’]m|\bHey,? I['’]m|\bMeet)\s+(?:Dr\.?\s+)?([A-Z][a-z]{2,15})\b")
META_RE = re.compile(r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)', re.I)
META_RE2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\'](?:description|og:description)["\']', re.I)


def log(*a):
    print('PUBLIC:', *a, flush=True)


def visible(h):
    h = re.sub(r'(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>', ' ', h)
    return re.sub(r'\s+', ' ', _html.unescape(re.sub(r'<[^>]+>', ' ', h))).strip()


def first_name_from_email(em):
    if not em or '@' not in em:
        return ''
    local, ed = em.lower().split('@')
    part = re.split(r'[._\-+0-9]+', local)[0]
    if len(part) < 3 or part in NOT_NAMES or part in L.ROLE_EXTRA:
        return ''
    if ed in L.FREE_MAIL and any(w in local for w in ('coach', 'wellness', 'life', 'llc', 'health', 'consult')):
        return ''
    if len(local) > 14 and '.' not in local and '_' not in local:      # one long word is a business, not a first name
        return ''
    return part.capitalize()


def site_facts(host):
    base = 'https://' + host
    home = L.get(base) or L.get('http://' + host)
    if not home:
        return '', '', ''
    about = ''
    for p in ('/about', '/about-me', '/about-us', '/meet-', '/my-story'):
        about = L.get(base + p)
        if about:
            break
    desc = ''
    for rx in (META_RE, META_RE2):
        m = rx.search(home)
        if m:
            desc = _html.unescape(m.group(1)).strip()
            break
    body = visible(about or home)
    text = visible(home) + ' ' + body
    name = ''
    m = NAME_RE.search(text)
    if m and m.group(1).lower() not in NOT_NAMES:
        name = m.group(1)
    summary = (desc + ' | ' if desc else '') + body[:600]
    return name, re.sub(r'\s+', ' ', summary).strip()[:900], text


POST_RE = re.compile(r'-\s*\[(.+?)\]\((https?://(?:www\.)?instagram\.com/(?:p|reel)/[^)\s]+)\)\s*·\s*(20\d\d-\d\d-\d\d)')


def ig_facts(url):
    md = L.firecrawl(url)
    if not md:
        return '', '', '', ''
    posts = POST_RE.findall(md)
    if not posts:
        return '', '', '', ''
    posts = sorted(posts, key=lambda x: x[2], reverse=True)[:3]
    lines = '\n'.join(f"{d}: {re.sub(chr(10), ' ', c).strip()}" for c, _u, d in posts)
    newest = posts[0][2]
    gap = (dt.date.today() - dt.date.fromisoformat(newest)).days
    title = ''
    m = re.match(r'#\s*([^\n(@]+?)\s*(?:\(@|\n)', md)
    if m:
        title = m.group(1).strip()
    return lines, newest, str(gap), title


def enrich_one(item):
    out = {}
    guess = first_name_from_email(item.get('em', ''))      # only kept if the same word shows up on the coach's own pages
    name, text = '', ''
    if item.get('site'):
        n2, summ, text = site_facts(L.dom(item['site']))
        if summ:
            out['Site summary'] = summ
        name = n2
    title = ''
    if item.get('ig'):
        lines, newest, gap, title = ig_facts(item['ig'])
        if lines:
            out['Latest IG posts'] = lines
            out['Last IG post date'] = newest
            out['Posting gap days'] = gap
        text += ' ' + title + ' ' + lines
    if not name and guess and re.search(r'\b' + re.escape(guess) + r'\b', text):
        name = guess
    words = title.split()
    if not name and 2 <= len(words) <= 3 and all(re.fullmatch(r"[A-Z][a-z'.\-]{1,15}", w) for w in words) \
            and not any(w.lower() in BIZ_WORDS or w.lower() in NOT_NAMES for w in words):
        name = words[0]                        # a two or three word capitalised title with no business word is a person's name
    if name:
        out['Owner first name'] = name
    return out


def ensure_columns(base, tables):
    live = L.at('GET', f'meta/bases/{base}/tables')['tables']
    made = 0
    for t in live:
        if t['name'] not in LEAD_TABLES:
            continue
        have = {f['name'] for f in t['fields']}
        for name, typ in COLS.items():
            if name not in have:
                L.at('POST', f"meta/bases/{base}/tables/{t['id']}/fields", {'name': name, 'type': typ})
                made += 1
    global FIELDS_OF
    FIELDS_OF = {t['name']: {f['name'] for f in t['fields']} | (set(COLS) if made else set()) for t in live if t['name'] in LEAD_TABLES}
    return {t['name']: t['id'] for t in live if t['name'] in LEAD_TABLES}, made


def seed(base, tids):
    q = {}
    for name, tid in tids.items():
        off = None
        while True:
            want = [c for c in ('Business', 'Website', 'Instagram', 'Email', 'Enriched on') if c in FIELDS_OF.get(name, set())]   # tables lack some columns
            qq = {'pageSize': 100, 'fields[]': want}
            if off:
                qq['offset'] = off
            d = L.at('GET', f'{base}/{tid}', query=qq)
            for r in d['records']:
                f = r['fields']
                if f.get('Enriched on') or not (f.get('Website') or f.get('Instagram')):
                    continue
                q[r['id']] = {'t': name, 'site': f.get('Website', ''), 'ig': f.get('Instagram', ''), 'em': f.get('Email', '')}
            off = d.get('offset')
            if not off:
                break
    return q


def main():
    cfg = L.config(); base = cfg['airtable']['base']
    L.FC_CAP = IG_READS
    if '--sample' in sys.argv:
        rows = json.load(open('/tmp/socials_only_rows.json')) if os.path.exists('/tmp/socials_only_rows.json') else []
        pick = [r['fields'] for r in rows if r['fields'].get('Website') and r['fields'].get('Instagram')][:3]
        for f in pick:
            print(json.dumps(enrich_one({'site': f['Website'], 'ig': f['Instagram'], 'em': f.get('Email', '')}), indent=1)[:1500])
        return
    tids, made = ensure_columns(base, cfg['airtable']['tables'])
    if made:
        log('columns added', made)
    st = common.load('enrich_state', {'q': {}, 'seeded': False, 'done': 0})
    if not st.get('seeded'):
        st['q'].update(seed(base, tids)); st['seeded'] = True
        log('queue seeded', len(st['q']))
    ids = list(st['q'])[:ENRICH_PER_RUN]
    log('queue', len(st['q']), 'taking', len(ids))
    with cf.ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda i: enrich_one(st['q'][i]), ids))
    today = dt.date.today().isoformat()
    by = {}
    for i, r in zip(ids, res):
        r = dict(r); r['Enriched on'] = today
        by.setdefault(st['q'][i]['t'], []).append({'id': i, 'fields': r})
    wrote, ok_ids = 0, set()
    for t, recs in by.items():
        tid = tids.get(t)
        if not tid:
            continue
        for k in range(0, len(recs), 10):
            try:
                L.at('PATCH', f'{base}/{tid}', {'records': recs[k:k + 10], 'typecast': True})
                wrote += len(recs[k:k + 10]); ok_ids |= {x['id'] for x in recs[k:k + 10]}
            except Exception as e:
                log('patch failed, kept in queue', type(e).__name__)
    for i in ok_ids:
        st['q'].pop(i, None)
    st['done'] = st.get('done', 0) + wrote
    common.save('enrich_state', st)
    common.commit('enrich state')
    have = lambda k: sum(1 for r in res if r.get(k))
    log('ENRICHED', wrote, '| owner name', have('Owner first name'), '| site summary', have('Site summary'), '| instagram posts', have('Latest IG posts'),
        '| left in queue', len(st['q']), '| instagram reads', L._fc['n'])


if __name__ == '__main__':
    main()
