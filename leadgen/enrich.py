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
import sys, os, re, json, time, datetime as dt, concurrent.futures as cf
import html as _html
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import leadgen as L
import common

ENRICH_PER_RUN = 200            # sites are free to read; Instagram is the limited part (IG_READS)
IG_READS = 40                      # Firecrawl reads for Instagram per run (4,000 credits a month across the keys, the finder uses 60 a day)
POSTS_PER_RUN = int(os.environ.get('POSTS_PER_RUN', '60'))   # leads whose newest posts are read through Apify per run (about $0.015 a lead, 26 Nov test)
POST_BATCH = 15                                              # profile URLs per Apify call
FIELDS_OF = {}
LEAD_TABLES = ('Email and Socials', 'Email Only', 'Socials Only (no email)')
COLS = {'Owner first name': 'singleLineText', 'Site summary': 'multilineText', 'Latest IG posts': 'multilineText',
        'Last IG post date': 'singleLineText', 'Posting gap days': 'singleLineText', 'Enriched on': 'singleLineText',
        'Latest FB posts': 'multilineText', 'Last FB post date': 'singleLineText',
        'Latest LinkedIn posts': 'multilineText', 'Last LinkedIn post date': 'singleLineText',
        'Newest post platform': 'singleLineText', 'Newest post date': 'singleLineText', 'Posts researched on': 'singleLineText'}
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


# ---------------- newest posts on Facebook, Instagram and LinkedIn through Apify (added 26 Nov 2026) ----------------
_ap_dead = set()
POSTS_BUDGET_SEC = 14 * 60            # the whole job has 55 minutes and the finder takes about 30; leads not reached keep no marker and go first next run
_t0 = [None]
APIFY_ACTORS = {'fb': 'apify~facebook-posts-scraper', 'li': 'harvestapi~linkedin-profile-posts', 'ig': 'apify~instagram-post-scraper'}


def apify_run(kind, urls):
    """One Apify call for a batch of profile URLs. Walks the token pool; a token that answers 401/402/403/429 is skipped for the rest of the run."""
    body = {'fb': {'startUrls': [{'url': u} for u in urls], 'resultsLimit': 2},
            'li': {'targetUrls': urls, 'maxPosts': 2},
            'ig': {'username': urls, 'resultsLimit': 2}}[kind]
    toks = [t for t in L.apify_tokens() if t not in _ap_dead]
    for t in toks:
        try:
            r = L.urllib.request.urlopen(L.urllib.request.Request(
                f'https://api.apify.com/v2/acts/{APIFY_ACTORS[kind]}/run-sync-get-dataset-items?token={t}&timeout=280',
                data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}), timeout=300)
            return json.load(r)
        except L.urllib.error.HTTPError as e:
            if e.code in (401, 402, 403, 429):
                _ap_dead.add(t); continue
            log('apify http', kind, e.code); return None
        except Exception as e:
            log('apify error', kind, type(e).__name__); return None
    log('apify: no token with credit left')
    return None


def _slug(u):
    return re.sub(r'[?#].*$', '', u or '').rstrip('/').split('/')[-1].lower()


def _fmt(posts):
    posts = sorted(posts, key=lambda x: x[0], reverse=True)[:2]
    return '\n'.join(d + ': ' + re.sub(r'\s+', ' ', t).strip()[:500] for d, t in posts), posts[0][0]


def post_research(items):
    """items: {record id: {'fb','li','ig' urls}}. Returns {record id: fields}. Own posts only; a record with no readable post just gets the marker."""
    found = {k: {} for k in ('fb', 'li', 'ig')}                 # url -> [(date, text)]
    _t0[0] = time.time(); stopped = set()
    for kind in ('fb', 'li', 'ig'):
        urls = sorted({v[kind] for v in items.values() if v.get(kind)})
        for k in range(0, len(urls), POST_BATCH):
            if time.time() - _t0[0] > POSTS_BUDGET_SEC:
                stopped.add(kind); break
            chunk = urls[k:k + POST_BATCH]
            data = apify_run(kind, chunk)
            for r in data or []:
                if r.get('error'):
                    continue
                if kind == 'fb':
                    key, d, t = r.get('inputUrl') or r.get('topLevelUrl') or '', (r.get('time') or '')[:10], r.get('text') or ''
                elif kind == 'ig':
                    key, d, t = r.get('inputUrl') or '', (r.get('timestamp') or '')[:10], r.get('caption') or ''
                else:
                    q = r.get('query') or {}
                    key = q.get('targetUrl') or ''
                    d, t = ((r.get('postedAt') or {}).get('date') or '')[:10], r.get('content') or ''
                    me = _slug(key)
                    au = r.get('author') or {}
                    if (au.get('publicIdentifier') or _slug(au.get('linkedinUrl'))).lower() != me:
                        continue                                 # someone else's post that the profile shared, not their own words
                if key and d and t.strip():
                    found[kind].setdefault(key, []).append((d, t))
    names = {'fb': 'FB', 'li': 'LinkedIn', 'ig': 'IG'}
    out = {}
    for rid, v in items.items():
        f, newest = {}, []
        for kind in ('fb', 'li', 'ig'):
            posts = found[kind].get(v.get(kind) or '')
            if posts:
                lines, nd = _fmt(posts)
                if kind != 'ig':
                    f[f'Latest {names[kind]} posts'] = lines
                    f[f'Last {names[kind]} post date'] = nd
                elif not v.get('has_ig'):                        # Firecrawl read already filled Instagram; Apify only fills the gap
                    f['Latest IG posts'] = lines
                    f['Last IG post date'] = nd
                    f['Posting gap days'] = str((dt.date.today() - dt.date.fromisoformat(nd)).days)
                newest.append((nd, names[kind]))
        if newest:
            nd, nm = max(newest)
            f['Newest post platform'] = nm
            f['Newest post date'] = nd
        if any(v.get(k) for k in stopped):
            continue                                         # a platform was not reached in time: leave the lead unmarked for the next run
        f['Posts researched on'] = dt.date.today().isoformat()
        out[rid] = f
    return out


def posts_queue(base, tids):
    """Leads with a Facebook, Instagram or LinkedIn link and no 'Posts researched on' date. Clearing that date puts a lead back in line."""
    q = {}
    for name, tid in tids.items():
        have = FIELDS_OF.get(name, set())
        want = [c for c in ('Instagram', 'Facebook', 'LinkedIn', 'Latest IG posts', 'Posts researched on') if c in have]
        socials = [c for c in ('Facebook', 'LinkedIn', 'Instagram') if c in have]
        if not socials or 'Posts researched on' not in have:
            continue
        formula = 'AND(NOT({Posts researched on}),OR(' + ','.join('{%s}' % c for c in socials) + '))'
        d = L.at('GET', f'{base}/{tid}', query={'pageSize': min(100, POSTS_PER_RUN), 'fields[]': want, 'filterByFormula': formula})
        for r in d['records']:
            f = r['fields']
            q[r['id']] = {'t': name, 'fb': f.get('Facebook', ''), 'li': f.get('LinkedIn', ''), 'ig': f.get('Instagram', ''), 'has_ig': bool(f.get('Latest IG posts'))}
    return dict(list(q.items())[:POSTS_PER_RUN])


def err_tag(e):
    """Public-log safe: the HTTP code and Airtable's error type only, never the message (it can quote a value)."""
    m = str(e)
    code = re.search(r'HTTP (\d+)', m); typ = re.search(r'"type":\s*"([A-Z_]+)"', m)
    return type(e).__name__ + ' ' + (code.group(1) if code else '-') + ' ' + (typ.group(1) if typ else '-')


def patch_safely(base, tid, recs):
    """Write 10 at a time; if a batch fails, write its records one by one so one bad record cannot sink the rest."""
    ok = set()
    for k in range(0, len(recs), 10):
        batch = recs[k:k + 10]
        try:
            L.at('PATCH', f'{base}/{tid}', {'records': batch, 'typecast': True}); ok |= {x['id'] for x in batch}
        except Exception as e:
            log('patch failed, retrying one by one', err_tag(e))
            for x in batch:
                try:
                    L.at('PATCH', f'{base}/{tid}', {'records': [x], 'typecast': True}); ok.add(x['id'])
                except Exception as e2:
                    log('record patch failed', err_tag(e2), '| fields', ','.join(sorted(x['fields'])))
                    if 'HTTP 404' in str(e2) or 'ROW_DOES_NOT_EXIST' in str(e2):
                        ok.add(x['id'])                      # the record was deleted or moved: drop it from the queue so it cannot block the next run
    return ok


def run_posts(base, tids):
    q = posts_queue(base, tids)
    log('posts queue taking', len(q))
    if not q:
        return
    res = post_research(q)
    by = {}
    for rid, f in res.items():
        by.setdefault(q[rid]['t'], []).append({'id': rid, 'fields': f})
    wrote = 0
    for t, recs in by.items():
        wrote += len(patch_safely(base, tids[t], recs))
    have = lambda k: sum(1 for f in res.values() if f.get(k))
    log('POSTS RESEARCHED', wrote, '| facebook', have('Latest FB posts'), '| linkedin', have('Latest LinkedIn posts'), '| instagram filled', have('Latest IG posts'),
        '| with a newest post', have('Newest post platform'), '| dead tokens', len(_ap_dead))


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
        got = patch_safely(base, tid, recs)
        wrote += len(got); ok_ids |= got
    for i in ok_ids:
        st['q'].pop(i, None)
    st['done'] = st.get('done', 0) + wrote
    common.save('enrich_state', st)
    common.commit('enrich state')
    if '--no-posts' not in sys.argv:
        try:
            run_posts(base, tids)
        except Exception as e:
            log('posts step failed', type(e).__name__)
    have = lambda k: sum(1 for r in res if r.get(k))
    log('ENRICHED', wrote, '| owner name', have('Owner first name'), '| site summary', have('Site summary'), '| instagram posts', have('Latest IG posts'),
        '| left in queue', len(st['q']), '| instagram reads', L._fc['n'])


if __name__ == '__main__':
    main()
