#!/usr/bin/env python3
"""Daily free coach lead finder (Ayo, 21 Nov 2026: "scrape leads free everyday on schedule and I can see them on Airtable").

Engine: TinyFish Search (free, no card) running Google style dorks, plus a plain fetch of each coach's own site.
Nothing here writes emails or DMs and nothing is sent. It only files NEW leads into the Cold Outreach tables.

  python3 leadgen.py            find leads and write them to Airtable
  python3 leadgen.py --dry      find leads, print a count, write nothing

PUBLIC REPO: only 'PUBLIC:' lines are meant for the Actions log (counts only). Lead details stay in Airtable.
Rules carried over from /coach-outreach: decision maker emails only (email_check), US first, dedupe on email, site and name
across all four tables plus the Replied No table, never store a shared inbox.
"""
import json, os, re, sys, time, ssl, datetime as dt, urllib.request, urllib.parse, urllib.error
import concurrent.futures as cf
import html as _html

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import email_check
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'dmsender'))
import common   # encrypted state files committed back to the repo (same helper the DM sender uses)

DRY = '--dry' in sys.argv
DAILY_CAP = 150           # new leads written per run (Ayo 21 Nov: 100 to 150 a day)
PAIRS_PER_RUN = 20        # (kind, city) pairs per run, each searched with 4 dorks + 2 platform footprints
FOLLOWUPS_PER_RUN = 20    # extra searches that look up a LinkedIn person's own site
FIELDS_OK = {'Business', 'City', 'Website', 'Email', 'Instagram', 'Facebook', 'LinkedIn', 'Claude cross-check', 'Found on', 'Last posted'}
EXA_CAP = 40              # Exa free plan = $10 a month, about 47 searches a day at $0.007
FC_CAP = 60               # Firecrawl page reads per run (4,000 credits a month across the keys)
# Directories only count when their pages show the coach's full details (website, email or socials). Tested 22 Nov 2026: Noomii, Bark,
# Coach.me, Psychology Today, TherapyDen, BBB and the Chamber of Commerce show none of these, so none is used. Google Maps is the directory
# that passes (website, phone, rating, reviews).
DIRECTORY_SITES = ()
SERP_CAP = 8               # SerpApi free plan = 250 searches a month; 8 a day stays inside it
MAPS_SEARCHES = 5          # Google Maps searches per run
MAPS_PER_SEARCH = 40       # places per Maps search; Apify free credit costs about $0.005 a place
# Maps only has results for plain coach types in bigger cities ('executive coach' in Virginia Beach returned 3 places), so it gets its own list.
MAPS_KINDS = ['life coach', 'business coach', 'career coach', 'health coach', 'wellness coach']
MAX_IDLE_DAYS = 90        # an Instagram page with no post for this long is treated as dead

KINDS = ['life coach', 'business coach', 'career coach', 'ADHD coach', 'health coach', 'executive coach',
         'mindset coach', 'wellness coach', 'relationship coach', 'leadership coach']
CITIES = ['Austin TX', 'Denver CO', 'Atlanta GA', 'Phoenix AZ', 'Seattle WA', 'Portland OR', 'San Diego CA', 'Nashville TN',
          'Charlotte NC', 'Raleigh NC', 'Tampa FL', 'Miami FL', 'Orlando FL', 'Dallas TX', 'Houston TX', 'Chicago IL',
          'Minneapolis MN', 'Boston MA', 'Philadelphia PA', 'Pittsburgh PA', 'Columbus OH', 'Cleveland OH', 'Detroit MI',
          'Salt Lake City UT', 'Las Vegas NV', 'Sacramento CA', 'San Francisco CA', 'Los Angeles CA', 'San Jose CA',
          'Kansas City MO', 'St. Louis MO', 'Indianapolis IN', 'Milwaukee WI', 'Baltimore MD', 'Washington DC',
          'Richmond VA', 'Virginia Beach VA', 'Louisville KY', 'Oklahoma City OK', 'San Antonio TX', 'Boise ID',
          'Albuquerque NM', 'Tucson AZ', 'Honolulu HI', 'Omaha NE', 'Cincinnati OH', 'Jacksonville FL', 'New Orleans LA',
          'Brooklyn NY', 'Scottsdale AZ']

# Directories, social and big sites are never a coach's own website.
SKIP_DOMAINS = {'linkedin.com', 'facebook.com', 'instagram.com', 'twitter.com', 'x.com', 'youtube.com', 'tiktok.com', 'pinterest.com',
                'yelp.com', 'indeed.com', 'glassdoor.com', 'psychologytoday.com', 'thumbtack.com', 'bark.com', 'upwork.com',
                'fiverr.com', 'eventbrite.com', 'meetup.com', 'groupon.com', 'wikipedia.org', 'reddit.com', 'quora.com',
                'medium.com', 'forbes.com', 'inc.com', 'healthline.com', 'betterhelp.com', 'talkspace.com', 'coachfederation.org',
                'coachingfederation.org', 'noomii.com', 'coach.me', 'ziprecruiter.com', 'linktr.ee', 'amazon.com', 'zocdoc.com',
                'expertise.com', 'nextdoor.com', 'mapquest.com', 'bbb.org', 'chamberofcommerce.com', 'manta.com', 'alignable.com',
                'deseret.com', 'goodtherapy.org', 'therapyden.com', 'teamwork.com', 'sba.gov', 'canva.com', 'substack.com', 'patch.com'}

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'
NON_TARGET = re.compile(r'\b(gym|fitness|personal train|trainer|bodybuild|yoga|pilates|crossfit|ifbb|hypno|football|basketball|soccer|baseball|volleyball|softball|hockey|lacrosse|swim|sports?|athlet|mma|boxing|golf|tennis|dance|martial|barre|cycling|running|weight ?loss|keto|macros?|physique|muscle|bikini|powerlifting|strength|credit score|credit repair|doula|mortgage|insurance|loan officer|realtor|real estate|dentist|chiropract|massage|tax prep|bookkeep)\b', re.I)
BAD_TITLE = re.compile(r'(\btop \d*|\bbest \d*|\d+ (best|top)|compared|browse|directory|near me|reviews?\b|how to|find a |list of|\bpost\b|photos and videos|instagram|facebook|\.\.\.|\bwelcome\b|just got|excited|foundation|^about\b|^home\b|\bjobs?\b|hiring|certified coaches)', re.I)
FREE_MAIL = ('gmail.com', 'yahoo.com', 'outlook.com', 'icloud.com', 'hotmail.com', 'proton.me', 'protonmail.com', 'aol.com', 'live.com', 'me.com')
ROLE_EXTRA = ('notice', 'notices', 'studentcare', 'care', 'sales', 'billing', 'press', 'media', 'help', 'service', 'services', 'orders', 'enquiries', 'inquiries',
              'booking', 'bookings', 'appointments', 'reception', 'privacy', 'legal', 'webmaster', 'noreply', 'donotreply', 'registrar', 'admissions', 'hr', 'jobs',
              'assist', 'assistant', 'coaches', 'coachinfo', 'inquire', 'enquire', 'connect', 'reach', 'talk', 'ask', 'bookme', 'booknow', 'client', 'admin', 'manager', 'support', 'office', 'mail', 'email', 'hello', 'hi', 'info', 'contact', 'team')
BAD_REGION = re.compile(r'\b(india|pakistan|bangladesh|nigeria|lagos|karachi|mumbai|delhi|dhaka)\b', re.I)
EMRE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
BADEM = ('example', 'sentry', 'wixpress', 'domain.com', 'email.com', 'yourname', '.png', '.jpg', '.webp', '.gif', '.svg', 'godaddy',
         'squarespace', '@2x', 'name@', 'test@', 'user@', 'you@', 'your@', 'johndoe', 'janedoe', 'sample@')


def log(*a):
    print(*a, flush=True)


def secret(name):
    v = os.environ.get(name, '').strip()
    if v:
        return v
    f = os.path.expanduser('~/.secrets/api-keys.env')
    if os.path.exists(f):
        for line in open(f):
            m = re.match(r'\s*(?:export\s+)?' + name + r'=(.+)', line)
            if m:
                return m.group(1).strip().strip('"\'')
    return ''


def config():
    raw = os.environ.get('PRIVATE_CONFIG', '').strip()
    if not raw:
        p = os.path.join(os.path.dirname(HERE), 'private_config.json')
        raw = open(p).read() if os.path.exists(p) else '{}'
    return json.loads(raw)


# ---------------- search ----------------
_last = [0.0]


def search(q):
    """TinyFish Search (free, 30 a minute). Returns [{title, url, snippet}]."""
    if over(SEARCH_STOP + 1):
        return []
    wait = 2.4 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    u = 'https://api.search.tinyfish.ai?' + urllib.parse.urlencode({'query': q})
    for i in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, headers={'X-API-Key': secret('TINYFISH_API_KEY'), 'User-Agent': 'leadgen'}), timeout=40)
            return json.load(r).get('results', [])
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < 2:
                time.sleep(20)
                continue
            log('PUBLIC: search http', e.code)
            return []
        except Exception as e:
            if i < 2:
                time.sleep(4)
                continue
            log('PUBLIC: search error', type(e).__name__)
            return []
    return []


def dom(u):
    m = re.search(r'(?:https?://)?(?:www\.)?([^/\s?#]+)', u or '')
    return m.group(1).lower() if m else ''


FREE_PLATFORMS = ('wixsite.com', 'squarespace.com', 'webflow.io', 'framer.website', 'carrd.co', 'godaddysites.com', 'wordpress.com', 'myshopify.com', 'weebly.com', 'site123.me', 'business.site')


def root(d):
    if any(d == f or d.endswith('.' + f) for f in FREE_PLATFORMS):
        return d          # a free-platform site is its own business, never collapse to wixsite.com
    p = d.split('.')
    return '.'.join(p[-2:]) if len(p) >= 2 else d


def norm(n):
    return re.sub(r'[^a-z0-9]', '', (n or '').lower())


def clean_title(t):
    t = re.sub(r'\s+', ' ', _html.unescape(t or '')).strip()
    t = re.split(r'\s\(@', t)[0]
    t = re.split(r'\s[-|–—·]\s|,\s', t)[0]
    return t.strip(' -|')[:80]


def social_base(u, host):
    """Profile root only (no post, reel or photo links)."""
    p = urllib.parse.urlparse(u)
    parts = [x for x in p.path.split('/') if x]
    if not parts:
        return ''
    bad = ('p', 'reel', 'reels', 'explore', 'stories', 'posts', 'photo', 'photos', 'groups', 'events', 'watch', 'share', 'sharer', 'tr', 'plugins', 'dialog', 'hashtag', 'public', 'pages', 'directory', 'pulse', 'jobs', 'feed', 'company')
    if parts[0].lower() in bad:
        if host == 'facebook.com' and parts[0] == 'pages' and len(parts) > 2:
            return f'https://www.facebook.com/{parts[1]}/{parts[2]}'
        return ''
    h0 = parts[0].lower()
    if h0 in ('profile.php', '2008', 'tr', 'login', 'home.php') or (host == 'facebook.com' and h0.isdigit() and len(h0) < 10):
        return ''
    if host == 'linkedin.com':
        return f'https://www.linkedin.com/{parts[0]}/{parts[1]}' if parts[0] == 'in' and len(parts) > 1 else ''
    return f'https://www.{host}/{parts[0]}'


# ---------------- site check ----------------
def get(u):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': UA}), timeout=6, context=CTX)
        return r.read(700000).decode('utf-8', 'ignore')
    except Exception:
        return ''


def soc(html, pat, host):
    for m in re.findall(r'https?://(?:www\.)?' + pat + r'[^"\'\s<>)\\]+', html):
        b = social_base(m.split('?')[0], host)
        if b:
            return b
    return ''


EXTRA_PATHS = ('/contact', '/contact-us', '/contact-me', '/about', '/about-me', '/about-us', '/work-with-me', '/connect', '/team',
               '/meet-the-team', '/hello', '/book', '/book-a-call', '/coaching', '/get-in-touch', '/privacy-policy', '/terms')
LINK_WORDS = re.compile(r'(about|contact|team|meet|connect|work[- ]with|book|hire|reach|touch|hello|privacy|terms)', re.I)
AT_WORD = re.compile(r'([A-Za-z0-9._%+-]+)\s*[\[\(\{<]\s*at\s*[\]\)\}>]\s*([A-Za-z0-9.-]+)\s*[\[\(\{<]\s*dot\s*[\]\)\}>]\s*([A-Za-z]{2,})', re.I)
CFE = re.compile(r'data-cfemail="([0-9a-fA-F]+)"|email-protection#([0-9a-fA-F]+)')


def _cf_decode(h):
    try:
        k = int(h[:2], 16)
        return ''.join(chr(int(h[i:i + 2], 16) ^ k) for i in range(2, len(h), 2))
    except Exception:
        return ''


def _quick_owner_email(base, text):
    hostroot = root(dom(base))
    for e in EMRE.findall(text) + re.findall(r'mailto:([^"\'?<>\s]+)', text):
        e = re.sub(r'^(?:u00a0|u200b|%20)+', '', e.lower().strip('.'))
        if not re.fullmatch(EMRE.pattern, e) or any(b in e for b in BADEM) or e.endswith(('.edu', '.gov')):
            continue
        local, ed = e.split('@')
        if re.sub(r'[._\-+0-9]+', '', local) in ROLE_EXTRA:
            continue
        if ed in FREE_MAIL or root(ed) == hostroot:
            return True
    return False


def wide_pages(base, html):
    """Home page plus the pages where a coach puts their own address: contact, about, team, privacy, terms, and any link on the home page that
    looks like one. Plain free HTTP. Returns text with Cloudflare-protected and 'name [at] site [dot] com' addresses turned into normal ones."""
    paths = list(EXTRA_PATHS)
    for h in re.findall(r'href=["\']([^"\'#?]+)', html)[:300]:
        if h.startswith('/') and not h.startswith('//') and LINK_WORDS.search(h) and h not in paths and len(h) < 60:
            paths.append(h)
        elif h.startswith(base) and LINK_WORDS.search(h[len(base):]) and h[len(base):] not in paths and len(h) < 100:
            paths.append(h[len(base):])
    out = html
    got = 0
    t0 = time.time()
    for p in paths[:22]:
        if time.time() - t0 > 30:
            break          # one slow site must not hold the run
        t = get(base + p)
        if t:
            out += '\n' + t
            got += 1
            if _quick_owner_email(base, _html.unescape(out)):
                break          # an owner address is on the page: no need to read more of this site
        if got >= 9:
            break
    out = _html.unescape(out)
    extra = ' '.join(_cf_decode(a or b) for a, b in CFE.findall(out))
    extra += ' ' + ' '.join(f'{a}@{b}.{c}' for a, b, c in AT_WORD.findall(out))
    return out + '\n' + extra


START = time.time()
# 25 Nov 2026: the 04:23 run ran past the 55 minute job limit, was cancelled, and everything it had found was lost (leads are only written at the end).
# Every phase now has a time budget (minutes from the start), so the run always reaches the save step. LEADGEN_BUDGET_SCALE shrinks them for tests.
SCALE = float(os.environ.get('LEADGEN_BUDGET_SCALE', '1'))
SEARCH_STOP = 22          # no new search, Exa, Maps, SerpApi or follow up query after this many minutes
DIRS_STOP = 27            # no directory discovery or crawl after this
FC_STOP = 29              # no Firecrawl reads after this
SITE_STOP = 36            # no new coach site is opened after this (sites already opened finish)
HARD_STOP = 51            # only if the run is somehow still before the Airtable write at this minute: fail loudly (the write itself needs about 2 minutes of the 55)
SITE_BUDGET = SITE_STOP * 60 * SCALE
WRITING = [False]


def over(minutes):
    return time.time() - START > minutes * 60 * SCALE


def mins():
    return round((time.time() - START) / 60 / SCALE, 1)


def check_site(host):
    if time.time() - START > SITE_BUDGET:
        return None
    try:
        return _check_site(host)
    except Exception:
        return None   # one broken page must never stop the daily run


def _check_site(host):
    base = 'https://' + host
    html = get(base) or get('http://' + host)
    if not html:
        return None
    pages = wide_pages(base, html)
    text = re.sub(r'<[^>]+>', ' ', html).lower()
    coach, therapy = text.count('coach'), text.count('therap') + text.count('counsel')
    ems = []
    for e in EMRE.findall(pages) + re.findall(r'mailto:([^"\'?<>\s]+)', pages):
        e = re.sub(r'^(?:u00a0|u200b|%20|\xa0|\u200b)+', '', e.lower().strip('.'))
        if e.endswith(('.edu', '.gov', '.mil')) or '.edu.' in e:
            continue
        if not re.fullmatch(EMRE.pattern, e) or any(b in e for b in BADEM) or e in ems:
            continue
        ems.append(e)
    def ok_email(e):
        if not email_check.check(e)[0]:
            return False
        local, ed = e.split('@')
        if re.sub(r'[._\-+0-9]+', '', local) in ROLE_EXTRA or any(local.startswith(r) and len(local) <= len(r) + 3 for r in ('assist', 'coach')):
            return False
        return ed in FREE_MAIL or root(ed) == root(host)   # an address on someone else's domain is not theirs
    personal = [e for e in ems if ok_email(e)]
    shared = [e for e in ems if not ok_email(e) and root(e.split('@')[1]) == root(host)]
    problems = []
    if any(host == f or host.endswith('.' + f) for f in FREE_PLATFORMS):
        problems.append('free platform address, no custom domain')
    if 'name="viewport"' not in html.lower() and "name='viewport'" not in html.lower():
        problems.append('no mobile layout tag')
    low = pages.lower()
    if '<form' not in low and 'calendly' not in low and 'acuity' not in low and 'book' not in text:
        problems.append('no contact form or booking link found')
    yrs = [int(y) for y in re.findall(r'(?:\u00a9|&copy;|copyright)[^0-9]{0,30}(20[0-3][0-9])', html, re.I)]
    if yrs and max(yrs) < dt.date.today().year - 1:
        problems.append(f'footer says {max(yrs)}, site looks not updated')
    t = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
    return {'host': host, 'problems': problems, 'coach': coach, 'therapy': therapy, 'email': personal[0] if personal else '', 'shared': shared[0] if shared else '',
            'ig': soc(pages, r'instagram\.com/', 'instagram.com'), 'fb': soc(pages, r'facebook\.com/', 'facebook.com'),
            'li': soc(pages, r'linkedin\.com/in/', 'linkedin.com'), 'title': clean_title(re.sub(r'<[^>]+>', '', t.group(1))) if t else ''}


# ---------------- airtable ----------------
def at(method, path, body=None, query=None):
    url = 'https://api.airtable.com/v0/' + path + ('?' + urllib.parse.urlencode(query, doseq=True) if query else '')
    for i in range(3):
        try:
            req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                         headers={'Authorization': 'Bearer ' + secret('AIRTABLE_TOKEN'), 'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < 2:
                time.sleep(31 if e.code == 429 else 5)
                continue
            raise RuntimeError(f'Airtable {method} HTTP {e.code}: {e.read().decode()[:200]}')
        except (urllib.error.URLError, OSError, ValueError) as e:   # dropped connection, timeout, truncated body
            if i < 2:
                time.sleep(4 + i * 4)
                continue
            raise RuntimeError(f'Airtable {method} network error: {type(e).__name__}')


def read_table(base, tid):
    out, off = [], None
    while True:
        q = {'pageSize': 100, 'fields[]': ['Business', 'Website', 'Email']}
        if off:
            q['offset'] = off
        d = at('GET', f'{base}/{tid}', query=q)
        out += d['records']
        off = d.get('offset')
        if not off:
            return out


def ensure_found_on(base, tables):
    """Adds a 'Found on' date column once, so Ayo can sort or filter the new leads. Needs schema scope; silently skipped without it."""
    try:
        meta = at('GET', f'meta/bases/{base}/tables')
        for t in meta['tables']:
            if t['id'] in tables.values() and 'Found on' not in [f['name'] for f in t['fields']]:
                at('POST', f"meta/bases/{base}/tables/{t['id']}/fields", {'name': 'Found on', 'type': 'singleLineText'})
                log('PUBLIC: added Found on column')
        return True
    except Exception as e:
        log('PUBLIC: Found on column not available (' + str(e)[:60] + ')')
        return False



# ---------------- Exa (free plan) and Firecrawl ----------------
_exa = {'n': 0, 'dead': False}


def exa(q, category=None, n=10):
    if _exa['dead'] or _exa['n'] >= EXA_CAP or not secret('EXA_API_KEY') or over(SEARCH_STOP + 1):
        return []
    _exa['n'] += 1
    body = {'query': q, 'numResults': n, 'type': 'auto'}
    if category:
        body['category'] = category
    for i in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request('https://api.exa.ai/search', data=json.dumps(body).encode(),
                                       headers={'x-api-key': secret('EXA_API_KEY'), 'Content-Type': 'application/json'}), timeout=60)
            return json.load(r).get('results', [])
        except urllib.error.HTTPError as e:
            if e.code in (402, 403):
                _exa['dead'] = True      # out of free credit: skip Exa for the rest of the run
                log('PUBLIC: Exa out of credit, skipped')
                return []
            if e.code == 429 and i < 2:
                time.sleep(5); continue
            return []
        except Exception:
            if i < 2:
                time.sleep(3); continue
            return []
    return []


_fc = {'n': 0}


def firecrawl(url):
    """One page as markdown (Instagram profile with recent posts, LinkedIn profile). Facebook is not supported by Firecrawl."""
    if _fc['n'] >= FC_CAP:
        return ''
    keys = [secret(k) for k in ('FIRECRAWL_API_KEY', 'FIRECRAWL_API_KEY_2', 'FIRECRAWL_API_KEY_3', 'FIRECRAWL_API_KEY_4')]
    for k in [k for k in keys if k]:
        _fc['n'] += 1
        try:
            r = urllib.request.urlopen(urllib.request.Request('https://api.firecrawl.dev/v1/scrape', data=json.dumps({'url': url, 'formats': ['markdown']}).encode(),
                                       headers={'Authorization': 'Bearer ' + k, 'Content-Type': 'application/json'}), timeout=90)
            return (json.load(r).get('data') or {}).get('markdown', '') or ''
        except urllib.error.HTTPError as e:
            if e.code == 402:
                continue            # this key is out of credit, try the next
            return ''
        except Exception:
            return ''
    return ''


def real_links(md):
    out = []
    for u in re.findall(r'https?://[^\s)\]>"\']+', md):
        h = root(dom(u))
        if h in SKIP_DOMAINS or h in ('linktr.ee', 'beacons.ai', 'bio.site', 'lnk.bio', 'campsite.bio') or h.endswith(('.gov', '.edu')):
            continue
        if any(x in h for x in ('instagram', 'facebook', 'linkedin', 'google', 'youtube', 'apple.com', 'spotify', 'calendly', 'tiktok', 'cdninstagram', 'fbcdn')):
            continue
        out.append(dom(u))
    return out


def enrich_ig(c):
    """Instagram profile via Firecrawl: bio link (own website), email in bio, followers, date of the newest post."""
    md = firecrawl(c['profile'])
    if not md:
        return
    posts = re.findall(r'(20\d\d-\d\d-\d\d)', md)
    if posts:
        c['last_post'] = max(posts)
    bio = md.split('## Recent posts')[0]
    c['bio'] = re.sub(r'\s+', ' ', bio)[:300]
    for e in EMRE.findall(bio):
        e = e.lower().strip('.')
        if not any(b in e for b in BADEM) and email_check.check(e)[0]:
            c['bio_email'] = e; break
    if not c.get('host'):
        links = real_links(bio)
        if links:
            c['host'] = links[0]
    if BAD_REGION.search(bio):
        c['skip'] = 'bio location'


def enrich_li(c):
    md = firecrawl(c['profile'])
    if not md:
        return
    head = md[:1500]
    if BAD_REGION.search(head):
        c['skip'] = 'linkedin location'; return
    if not c.get('host'):
        links = real_links(md[:6000])
        if links:
            c['host'] = links[0]
    for e in EMRE.findall(md[:6000]):
        e = e.lower().strip('.')
        if not any(b in e for b in BADEM) and email_check.check(e)[0]:
            c['bio_email'] = e; break


GENERIC_NAME = re.compile(r'^(contact( us)?|testimonials?|home|about( me| us)?|services?|welcome|coaching|life coach(ing)?|business coach(ing)?|career coach(ing)?|health coach(ing)?|executive coach(ing)?|wellness coach(ing)?|mindset coach(ing)?|relationship coach(ing)?|leadership coach(ing)?|1-?on-?1 coaching|coach|my story|blog|book( now| a call)?|work with me|programs?)$', re.I)


def tidy_name(name, host, kind):
    """Search titles often come back as page names ('Testimonials', 'Contact Amber Farooqui'). Strip the filler,
    and fall back to the website name when nothing real is left."""
    n = re.sub(r'^(contact|meet|about|hire|work with|welcome to)\s+', '', name or '', flags=re.I).strip(' :-|')
    if GENERIC_NAME.match(n) or len(n) < 3:
        if kind == 'web' and host:
            label = root(host).split('.')[0] if root(host) == host else host.split('.')[0]
            n = re.sub(r'[-_]+', ' ', label).strip().title()
            return n if len(n) >= 4 else ''
        return ''
    return n


_ap = {'i': 0, 'dead': set()}


def apify_tokens():
    raw = os.environ.get('APIFY_TOKENS', '').strip()
    if raw:
        return [t.strip() for t in raw.split(',') if t.strip()]
    f = os.path.expanduser('~/.secrets/api-keys.env')
    out = []
    if os.path.exists(f):
        for line in open(f):
            m = re.match(r'\s*(?:export\s+)?APIFY_TOKEN_(\d+)=(.+)', line)
            if m and int(m.group(1)) >= 5:        # tokens 1 to 4 are dead accounts
                out.append(m.group(2).strip().strip('"\''))
    return out


def maps(kind, city, limit):
    """Google Maps through Apify (free monthly credit per account). Rotates to the next token when one is out of credit."""
    toks = [t for t in apify_tokens() if t not in _ap['dead']]
    body = {'searchStringsArray': [kind], 'locationQuery': city + ', USA', 'maxCrawledPlacesPerSearch': limit,
            'language': 'en', 'scrapeContacts': False, 'skipClosedPlaces': True}
    for t in toks[_ap['i'] % max(len(toks), 1):] + toks[:_ap['i'] % max(len(toks), 1)]:
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                'https://api.apify.com/v2/acts/compass~crawler-google-places/run-sync-get-dataset-items?token=' + t + '&timeout=170',
                data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}), timeout=190)
            return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (402, 403, 429):
                _ap['dead'].add(t); continue    # out of credit on this token, try the next
            log('PUBLIC: maps http', e.code); return []
        except Exception as e:
            log('PUBLIC: maps error', type(e).__name__); return []
    log('PUBLIC: maps out of credit on every token')
    return []


_serp = {'n': 0}


def serp(q, start):
    """Real Google results pages 2 to 5 through SerpApi (free plan). The 'link' field is often a Google redirect, so the site comes from displayed_link."""
    if _serp['n'] >= SERP_CAP or not secret('SERPAPI_API_KEY'):
        return []
    _serp['n'] += 1
    try:
        d = json.load(urllib.request.urlopen('https://serpapi.com/search.json?' + urllib.parse.urlencode(
            {'engine': 'google', 'q': q, 'start': start, 'num': 10, 'api_key': secret('SERPAPI_API_KEY')}), timeout=60))
    except Exception as e:
        log('PUBLIC: serpapi error', type(e).__name__); return []
    out = []
    for r in d.get('organic_results', []):
        shown = (r.get('displayed_link') or '').split(' \u203a ')[0].strip()
        if re.match(r'^https?://[\w.-]+\.[a-z]{2,}', shown):
            out.append({'url': shown, 'title': r.get('title') or '', 'snippet': r.get('snippet') or ''})
    return out


# ---------------- main ----------------
def todays_pairs():
    n = dt.date.today().toordinal()
    out = []
    for i in range(PAIRS_PER_RUN):
        k = n * PAIRS_PER_RUN + i
        out.append((KINDS[k % len(KINDS)], CITIES[(k // len(KINDS) + k * 7) % len(CITIES)]))
    return out


# ---------------- coach directories (found 24 Nov 2026, list in directories.json) ----------------
DIRS_PER_RUN = 10          # directories crawled per run, rotating through the whole list
DIR_PAGES = 12             # pages followed inside one directory
DIR_JUNK = ('gmpg.org', 'hubspot.com', 'jsdelivr.net', 'typekit.net', 'optimizecdn.com', 'cdn.', 'w3.org', 'mapbox.com', 'googletagmanager.com',
            'facebook.net', 'wordpress.com', 'fonts.', 'gstatic', 'cloudfront', 'imagekit', 'jotform', 'ftc.gov', 'wildapricot', 'addtoany.com',
            'leadconnectorhq', 'unpkg.com', 'cdnjs', 'akamai', 'bootstrap', 'recaptcha', 'zoom.us', 'eventbrite', 'meetup.com', 'spotify',
            'apple.co', 'goo.gl', 'forms.gle', 'docs.google', 'drive.google', 'amzn', 'nih.gov', 'wikipedia', 'forbes.com', 'indeed.com',
            'coursera', 'udemy', 'psychologytoday', 'clubexpress', 'coachingfederation', 'calendly', 'mailchimp', 'squarespace.com', 'wix.com',
            'weebly.com', 'shopify', 'paypal', 'stripe', 'vimeo', 'github', 'bit.ly', 'linktr.ee', 'google.', 'apple.com', 'microsoft')


def crawl_directory(d):
    """One directory: its listing page plus the pagination or sibling listing pages, same site only. Returns (set of outside sites, set of emails)."""
    start = d['url']
    h = dom(start)
    seen, queue, sites, mails = set(), [start], set(), set()
    prefix = '/' + '/'.join([x for x in urllib.parse.urlparse(start).path.split('/') if x][:1])
    while queue and len(seen) < DIR_PAGES:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        t = _html.unescape(get(u))
        if not t:
            continue
        for m in re.findall(r'href=["\'](https?://[^"\'#\s]+)', t):
            x = dom(m)
            if x and root(x) != root(h) and not any(j in x for j in DIR_JUNK) and '.' in x and not any(x == s2 or x.endswith('.' + s2) for s2 in SKIP_DOMAINS):
                sites.add(x)
        for e in EMRE.findall(t) + re.findall(r'mailto:([^"\'?<>\s]+)', t):
            e = e.lower().strip('.')
            if re.fullmatch(EMRE.pattern, e) and not any(b in e for b in BADEM) and not e.endswith(('.edu', '.gov')):
                mails.add(e)
        for m in re.findall(r'href=["\']([^"\'#\s]+)', t):
            a = urllib.parse.urljoin(u, m).split('#')[0]
            p = urllib.parse.urlparse(a)
            if p.netloc.replace('www.', '') == h.replace('www.', '') and a not in seen and a not in queue \
                    and (re.search(r'[?&/](page|pg|paged|p|start|offset)[=/]?\d+|page_id=\d+|directory|coach|member|find|specialty|categor|webdirectory', a, re.I)) \
                    and not re.search(r'\.(css|js|png|jpg|pdf|svg|zip)(\?|$)', a):
                queue.append(a)
    return sites, mails


# ---------------- daily discovery of new directories (so the list grows by itself) ----------------
DISC_QUERIES_PER_RUN = 12
DISC_BAD = re.compile(r'(football|athletic|k12|\.edu|\.gov|hockey|basketball|soccer|baseball|sports|chamber|school|college|univers|church|\.co\.uk|\.uk$|\.in$|\.pk$|\.ng$)', re.I)
DISC_STATES = ['Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado', 'Connecticut', 'Delaware', 'Florida', 'Georgia', 'Hawaii', 'Idaho', 'Illinois',
               'Indiana', 'Iowa', 'Kansas', 'Kentucky', 'Louisiana', 'Maine', 'Maryland', 'Massachusetts', 'Michigan', 'Minnesota', 'Mississippi', 'Missouri',
               'Montana', 'Nebraska', 'Nevada', 'New Hampshire', 'New Jersey', 'New Mexico', 'New York', 'North Carolina', 'North Dakota', 'Ohio', 'Oklahoma',
               'Oregon', 'Pennsylvania', 'Rhode Island', 'South Carolina', 'South Dakota', 'Tennessee', 'Texas', 'Utah', 'Vermont', 'Virginia', 'Washington',
               'West Virginia', 'Wisconsin', 'Wyoming']
DISC_NICHES = ['life coach', 'business coach', 'executive coach', 'career coach', 'ADHD coach', 'health coach', 'wellness coach', 'mindset coach', 'relationship coach',
               'leadership coach', 'spiritual coach', 'money coach', 'confidence coach', 'transformational coach', 'parenting coach', 'nutrition coach', 'holistic coach',
               'small business coach', 'sales coach', 'burnout coach', 'grief coach', 'menopause coach', 'christian life coach', 'mentor coach', 'NLP coach',
               'divorce coach', 'retirement coach', 'writing coach', 'public speaking coach', 'trauma informed coach', 'women entrepreneur coach', 'productivity coach']


def disc_queries(n):
    """The day's search phrases: a different slice every day, state chapters and niche directories mixed."""
    out = []
    for i in range(DISC_QUERIES_PER_RUN):
        k = n * DISC_QUERIES_PER_RUN + i
        if k % 2 == 0:
            st_ = DISC_STATES[(k // 2) % len(DISC_STATES)]
            out.append([f'ICF {st_} chapter find a coach member directory', f'{st_} life coach directory members list website',
                        f'{st_} business coaches directory association members', f'{st_} coaches association member directory find a coach'][(k // 2 // len(DISC_STATES)) % 4])
        else:
            nc = DISC_NICHES[(k // 2) % len(DISC_NICHES)]
            out.append([f'{nc} directory find a {nc} member listings', f'list of {nc}s directory website email contact',
                        f'{nc} certified practitioners directory United States'][(k // 2 // len(DISC_NICHES)) % 3])
    return out


def discover_directories(known):
    """Searches for directory pages, opens the top page of each new site, and keeps it if it shows enough coach websites or emails.
    Returns the list of new directories [{domain, url, score}] and the set of every domain looked at today."""
    seen_now, urls = set(), {}
    for q in disc_queries(dt.date.today().toordinal()):
        for r in search(q):
            h = dom(r['url'])
            if not h or h in known or root(h) in known or DISC_BAD.search(h) or any(h == d or h.endswith('.' + d) for d in SKIP_DOMAINS):
                continue
            urls.setdefault(h, r['url'])
    def test(h):
        seen_now.add(h)
        u = urls[h]
        t = _html.unescape(get(u))
        if not t:
            return None
        sites, mails = set(), set()
        for m in re.findall(r'href=["\'](https?://[^"\'#\s]+)', t):
            x = dom(m)
            if x and root(x) != root(h) and not any(j in x for j in DIR_JUNK) and '.' in x and not any(x == s2 or x.endswith('.' + s2) for s2 in SKIP_DOMAINS):
                sites.add(x)
        for e in EMRE.findall(t) + re.findall(r'mailto:([^"\'?<>\s]+)', t):
            e = e.lower().strip('.')
            if re.fullmatch(EMRE.pattern, e) and not any(b in e for b in BADEM) and not e.endswith(('.edu', '.gov')) \
                    and re.sub(r'[._\-+0-9]+', '', e.split('@')[0]) not in ROLE_EXTRA and root(e.split('@')[1]) != root(h):
                mails.add(e)
        score = len(sites) + 2 * len(mails)
        low = t.lower()
        if score >= 30 and ('coach' in low) and low.count('coach') >= 10 and not NON_TARGET.search(h):
            return {'domain': h, 'url': u, 'score': score}
        return None
    with cf.ThreadPoolExecutor(16) as ex:
        found = [x for x in ex.map(test, list(urls)) if x]
    return found, seen_now


def harvest(cands, have_dom, have_em, extra=()):
    """Adds the coaches' own sites found in the day's directories as site candidates; the normal site check then reads their emails and socials."""
    p = os.path.join(HERE, 'directories.json')
    if not os.path.exists(p):
        return 0, 0
    dirs = json.load(open(p)) + list(extra)
    n = dt.date.today().toordinal()
    pick = [dirs[(n * DIRS_PER_RUN + i) % len(dirs)] for i in range(min(DIRS_PER_RUN, len(dirs)))]
    with cf.ThreadPoolExecutor(10) as ex:
        res = list(ex.map(crawl_directory, pick))
    sites_all, mails_all = set(), set()
    for s_, m_ in res:
        sites_all |= s_; mails_all |= m_
    added = 0
    for x in sites_all:
        if root(x) in have_dom or ('w:' + x) in cands:
            continue
        cands['w:' + x] = {'type': 'web', 'host': x, 'city': 'Directory', 'name': 'Home', 'q': 'directory'}   # name is replaced by the site title
        added += 1
    for e in mails_all:
        ed = e.split('@')[1]
        if ed in FREE_MAIL or e in have_em:
            continue
        r_ = root(ed)
        if r_ in have_dom or ('w:' + ed) in cands or any(j in ed for j in DIR_JUNK):
            continue
        cands['w:' + ed] = {'type': 'web', 'host': ed, 'city': 'Directory', 'name': 'Home', 'q': 'directory'}
        added += 1
    return len(pick), added


# ---------------- backfill: socials-only leads that have a website ----------------
SOCIAL_TABLE, EMAIL_TABLE = 'Socials Only (no email)', 'Email and Socials'
BACKFILL_PER_RUN = 60
MOVE_FIELDS = ('Business', 'City', 'Website', 'Cold DM', 'Instagram', 'Facebook', 'LinkedIn', 'Source record ID')


def seed_pending(base, tables):
    """One time: every socials-only row with a website, not yet drafted for DM and not approved, waits in line to be re-checked."""
    out, off = {}, None
    while True:
        q = {'pageSize': 100, 'fields[]': ['Business', 'Website', 'DM source post', 'Approve', 'Approve DM', 'Scheduled on']}
        if off:
            q['offset'] = off
        d = at('GET', f'{base}/{tables[SOCIAL_TABLE]}', query=q)
        for r in d['records']:
            f = r['fields']
            if f.get('Website') and not (f.get('DM source post') or f.get('Approve') or f.get('Approve DM') or f.get('Scheduled on')):
                out[r['id']] = dom(f['Website'])
        off = d.get('offset')
        if not off:
            return out


def backfill(base, tables, live, pend):
    """Re-check waiting socials-only leads with the wide site scan. A lead that turns up an owner email moves to Email and Socials: copy,
    read back, then delete the old row. Rows ticked, scheduled, sent or approved for DM are never touched. Returns (checked, moved)."""
    if not pend or EMAIL_TABLE not in tables or SOCIAL_TABLE not in tables:
        return 0, 0
    locked = set()
    try:
        locked = set(common.load('approved.json', [])) | {v.get('rid') for v in common.load('sent.json', {}).values()}
    except Exception as e:
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            log('PUBLIC: backfill skipped, state unreadable', type(e).__name__)
            return 0, 0          # never move a row without knowing what is approved or sent
    ids = [i for i in list(pend)[:BACKFILL_PER_RUN] if i not in locked]
    for i in list(pend)[:BACKFILL_PER_RUN]:
        if i in locked:
            pend.pop(i, None)
    with cf.ThreadPoolExecutor(24) as ex:
        scans = dict(zip(ids, ex.map(lambda i: check_site(pend[i]), ids)))
    dst_cols = live.get(tables[EMAIL_TABLE], set())
    moved = 0
    for i in ids:
        s = scans.get(i)
        pend.pop(i, None)            # every row is looked at once
        if not s or not s['email'] or s['coach'] < 3 or s['therapy'] > s['coach']:
            continue
        try:
            old = at('GET', f'{base}/{tables[SOCIAL_TABLE]}/{i}')['fields']
            if old.get('Approve') or old.get('Approve DM') or old.get('Scheduled on') or old.get('DM source post'):
                continue
            nf = {k: old[k] for k in MOVE_FIELDS if old.get(k)}
            nf['Email'] = s['email']
            nf['Source record ID'] = i
            nf['Claude cross-check'] = (old.get('Claude cross-check', '') + f" | Email {s['email']} found on the site by the wide scan {dt.date.today().isoformat()} (moved from {SOCIAL_TABLE})")[:9000]
            nf = {k: v for k, v in nf.items() if k in dst_cols}
            if '--bf-dry' in sys.argv:
                log('PUBLIC: would move', old.get('Business', '')[:30], s['email']); moved += 1; continue
            new = at('POST', f'{base}/{tables[EMAIL_TABLE]}', {'records': [{'fields': nf}], 'typecast': True})['records'][0]['id']
            chk = at('GET', f'{base}/{tables[EMAIL_TABLE]}/{new}')['fields']
            if chk.get('Email') != s['email'] or chk.get('Business') != old.get('Business'):
                log('PUBLIC: backfill copy did not read back, old row kept')
                continue
            at('DELETE', f'{base}/{tables[SOCIAL_TABLE]}/{i}')
            moved += 1
        except Exception as e:
            log('PUBLIC: backfill error', type(e).__name__)
    return len(ids), moved


def main():
    cfg = config(); A = cfg['airtable']; base = A['base']; tables = A['tables']
    if not secret('TINYFISH_API_KEY') or not secret('AIRTABLE_TOKEN'):
        log('PUBLIC: missing TINYFISH_API_KEY or AIRTABLE_TOKEN'); sys.exit(1)
    if '--bf-dry' in sys.argv:        # read-only rehearsal of the socials-only catch-up
        live = {t['id']: {f['name'] for f in t['fields']} for t in at('GET', f'meta/bases/{base}/tables')['tables']}
        pend = seed_pending(base, tables); log('PUBLIC: queue', len(pend))
        log('PUBLIC: checked, would move', backfill(base, tables, live, pend)); return
    # Known leads live in an encrypted state file, so a daily run costs Airtable only the write calls.
    # Full re-read of Airtable at most once a week (picks up rows Ayo added, moved or deleted by hand).
    st = common.load('leads_seen', None)
    stale = (not st) or (dt.date.today() - dt.date.fromisoformat(st.get('refreshed', '2000-01-01'))).days >= 7
    have_em, have_dom, have_nm = (set(st['em']), set(st['dom']), set(st['nm'])) if st else (set(), set(), set())
    if stale:
        # 22 Nov: tables can be deleted or added by hand (No Email or Socials went, Sent was added), so read the live list
        # by name. Sent holds every lead already emailed or messaged, so the finder must still know them.
        names = ('Email and Socials', 'Email Only', 'Socials Only (no email)', 'Replied No', 'Replied Yes', 'Sent')
        tids = [t['id'] for t in at('GET', f'meta/bases/{base}/tables')['tables'] if t['name'] in names]
        for tid in tids:
            for r in read_table(base, tid):
                f = r['fields']
                if f.get('Email'): have_em.add(f['Email'].lower())
                if f.get('Website'): have_dom.add(root(dom(f['Website'])))
                if f.get('Business'): have_nm.add(norm(f['Business']))
        log('PUBLIC: refreshed known leads from Airtable')
    log('PUBLIC: known leads', len(have_nm))

    cands, queries = {}, 0   # key -> lead dict
    pairs = todays_pairs()
    for kind, city in pairs:
        if over(SEARCH_STOP):
            log('PUBLIC: search time budget reached, minute', mins(), 'searches so far', queries)
            break
        short = city.rsplit(' ', 1)[0]
        dorks = [
            ('web', f'"{kind}" "{city}" ("free discovery call" OR "book a call" OR "work with me") -site:linkedin.com -site:facebook.com -site:instagram.com -site:yelp.com'),
            ('li', f'site:linkedin.com/in "{kind}" (founder OR owner OR "CEO") "{short}"'),
            ('ig', f'site:instagram.com "{kind}" "{short}" "link in bio" OR "DM me" OR "book"'),
            ('fb', f'site:facebook.com "{kind}" "{short}" coaching page -groups -events'),
        ]
        k0 = dt.date.today().toordinal() + len(dorks) * len(pairs)
        for j in range(2):
            plat = FREE_PLATFORMS[(k0 + j + len(short)) % len(FREE_PLATFORMS)]
            dorks.append(('fp', f'site:{plat} "{kind}" "{short}"'))
        for kindtag, q in dorks:
            queries += 1
            for res in search(q):
                url, title, snip = res.get('url', ''), res.get('title', ''), res.get('snippet', '')
                blob = (title + ' ' + snip).lower()
                if 'coach' not in blob or NON_TARGET.search(blob) or BAD_REGION.search(blob):
                    continue
                host = root(dom(url))
                if kindtag == 'fp':
                    full = dom(url)
                    if root(full) != full or not any(full.endswith('.' + f) for f in FREE_PLATFORMS):
                        continue
                    cands.setdefault('w:' + full, {'type': 'web', 'host': full, 'city': city, 'name': clean_title(title), 'q': 'footprint'})
                elif kindtag == 'web':
                    if host in SKIP_DOMAINS or any(host.endswith('.' + s) for s in SKIP_DOMAINS) or host.endswith(('.gov', '.edu')):
                        continue
                    cands.setdefault('w:' + host, {'type': 'web', 'host': dom(url), 'city': city, 'name': clean_title(title), 'q': kindtag})
                else:
                    site = {'li': 'linkedin.com', 'ig': 'instagram.com', 'fb': 'facebook.com'}[kindtag]
                    if host != site:
                        continue
                    prof = social_base(url.split('?')[0], site)
                    if not prof:
                        continue
                    key = kindtag + ':' + prof.lower()
                    cands.setdefault(key, {'type': kindtag, 'profile': prof, 'city': city, 'name': clean_title(title), 'q': kindtag})
    # Exa (free plan): coaches' own sites and LinkedIn profiles, cleaner than dorks.
    for kind, city in pairs:
        if over(SEARCH_STOP):
            break
        short = city.rsplit(' ', 1)[0]
        for res in exa(f'independent {kind} in {short} personal coaching website book a free discovery call'):
            url, title = res.get('url', ''), res.get('title') or ''
            host = root(dom(url))
            if host in SKIP_DOMAINS or host.endswith(('.gov', '.edu')) or NON_TARGET.search(title) or BAD_REGION.search(title):
                continue
            cands.setdefault('w:' + (dom(url) if root(dom(url)) == dom(url) else host), {'type': 'web', 'host': dom(url), 'city': city, 'name': clean_title(title), 'q': 'exa'})
        for res in exa(f'{kind} founder {short}', category='linkedin profile'):
            url, title = res.get('url', ''), res.get('title') or ''
            prof = social_base(url.split('?')[0], 'linkedin.com') if root(dom(url)) == 'linkedin.com' else ''
            if prof and 'coach' in title.lower() and not NON_TARGET.search(title) and not BAD_REGION.search(title):
                cands.setdefault('li:' + prof.lower(), {'type': 'li', 'profile': prof, 'city': city, 'name': clean_title(title), 'q': 'exa'})
    # Google Maps (Apify): real local coach listings with website, phone, rating and reviews.
    nmaps = 0
    big = CITIES[:30]
    n0 = dt.date.today().toordinal()
    for i in range(MAPS_SEARCHES):
        if over(SEARCH_STOP):
            break
        kind, city = MAPS_KINDS[(n0 + i) % len(MAPS_KINDS)], big[(n0 * MAPS_SEARCHES + i * 7) % len(big)]
        for it in maps(kind, city, MAPS_PER_SEARCH):
            title, cat = it.get('title') or '', it.get('categoryName') or ''
            blob = (title + ' ' + cat).lower()
            site = it.get('website') or ''
            if not site or 'coach' not in blob or NON_TARGET.search(blob) or BAD_REGION.search(blob):
                continue
            host = root(dom(site))
            if host in SKIP_DOMAINS or host.endswith(('.gov', '.edu')):
                continue
            nmaps += 1
            key = 'w:' + (dom(site) if root(dom(site)) == dom(site) else host)
            c0 = cands.setdefault(key, {'type': 'web', 'host': dom(site), 'city': city, 'name': clean_title(title), 'q': 'maps'})
            c0['maps'] = {'phone': it.get('phone') or '', 'score': it.get('totalScore'), 'reviews': it.get('reviewsCount')}
    log('PUBLIC: maps places kept', nmaps, 'minute', mins())

    # SerpApi: Google results pages 2 to 5, where small coaches with weak sites sit (Scouting Systems PDF, page 20).
    nserp = 0
    for kind, city in pairs[:2]:
        if over(SEARCH_STOP):
            break
        q = f'"{kind}" "{city}" ("free discovery call" OR "book a call" OR "work with me") -site:linkedin.com -site:facebook.com -site:instagram.com -site:yelp.com'
        for start in (10, 20, 30, 40):
            for res in serp(q, start):
                url, title, snip = res['url'], res['title'], res['snippet']
                blob = (title + ' ' + snip).lower()
                if 'coach' not in blob or NON_TARGET.search(blob) or BAD_REGION.search(blob):
                    continue
                host = root(dom(url))
                if host in SKIP_DOMAINS or host.endswith(('.gov', '.edu')):
                    continue
                nserp += 1
                cands.setdefault('w:' + (dom(url) if root(dom(url)) == dom(url) else host), {'type': 'web', 'host': dom(url), 'city': city, 'name': clean_title(title), 'q': 'serpapi'})
    log('PUBLIC: serpapi results kept', nserp, 'minute', mins())

    # Directories: names of coaches listed on coaching directories; the follow-up search below finds their own website.
    for kind, city in pairs[:4]:
        if over(SEARCH_STOP):
            break
        short = city.rsplit(' ', 1)[0]
        for dsite in DIRECTORY_SITES:
            queries += 1
            for res in search(f'site:{dsite} {kind} {short}'):
                title, snip = res.get('title', ''), res.get('snippet', '')
                if root(dom(res.get('url', ''))) != dsite or 'coach' not in (title + snip).lower() or NON_TARGET.search(title + snip) or BAD_REGION.search(title + snip):
                    continue
                nm = clean_title(title)
                if nm and not BAD_TITLE.search(nm) and not GENERIC_NAME.match(nm) and 1 < len(nm.split()) <= 4:
                    cands.setdefault('d:' + norm(nm), {'type': 'dir', 'city': city, 'name': nm, 'q': 'directory'})
    log('PUBLIC: searches', queries, 'exa', _exa['n'], 'candidates', len(cands), 'minute', mins())

    # LinkedIn people: one more search to find their own site / Instagram.
    fu = 0
    for key, c in list(cands.items()):
        if c['type'] not in ('li', 'ig', 'fb', 'dir') or c.get('host') or fu >= FOLLOWUPS_PER_RUN or not c['name'] or norm(c['name']) in have_nm or over(SEARCH_STOP):
            continue
        fu += 1
        for res in search(f'"{c["name"]}" coach {c["city"].rsplit(" ", 1)[0]} website OR instagram'):
            host = root(dom(res.get('url', '')))
            if host == 'instagram.com' and not c.get('ig') and 'coach' in (res.get('title', '') + res.get('snippet', '')).lower():
                c['ig'] = social_base(res['url'].split('?')[0], 'instagram.com')
            elif host not in SKIP_DOMAINS and not c.get('host') and 'coach' in (res.get('title', '') + res.get('snippet', '')).lower() and not NON_TARGET.search(res.get('title', '') + res.get('snippet', '')):
                c['host'] = dom(res['url'])
        queries += 1

    known_dirs = {d['domain'] for d in json.load(open(os.path.join(HERE, 'directories.json')))} | {d['domain'] for d in (st or {}).get('dirs_extra', [])} | set((st or {}).get('dirs_seen', []))
    new_dirs, dirs_looked = ([], set()) if over(DIRS_STOP) else discover_directories(known_dirs)
    dirs_extra = list((st or {}).get('dirs_extra', [])) + new_dirs
    dirs_seen = sorted(set((st or {}).get('dirs_seen', [])) | dirs_looked)[-3000:]
    log('PUBLIC: directory search looked at', len(dirs_looked), 'new sites, kept', len(new_dirs), 'as directories, list now', len(dirs_extra) + len(json.load(open(os.path.join(HERE, 'directories.json')))))
    dirs_n, dirs_added = (0, 0) if over(DIRS_STOP) else harvest(cands, have_dom, have_em, dirs_extra)
    log('PUBLIC: directories crawled', dirs_n, 'new sites from them', dirs_added, 'minute', mins())

    # Firecrawl: Instagram bio link, email and newest post date; LinkedIn location and links. Thin leads first.
    todo = [c for c in cands.values() if c['type'] in ('ig', 'li') and norm(c['name']) not in have_nm]
    todo.sort(key=lambda c: (bool(c.get('host')), c['type'] != 'ig'))
    todo = [] if over(FC_STOP) else todo[:FC_CAP]
    with cf.ThreadPoolExecutor(6) as ex:
        list(ex.map(lambda c: enrich_ig(c) if c['type'] == 'ig' else enrich_li(c), todo))
    log('PUBLIC: firecrawl reads', _fc['n'], 'minute', mins())

    # Check each coach's own site for links and a personal email.
    hosts = {c['host'] for c in cands.values() if c.get('host') and root(c['host']) not in have_dom}
    with cf.ThreadPoolExecutor(64) as ex:
        sites = dict(zip(hosts, ex.map(check_site, hosts)))
    log('PUBLIC: sites checked', len(hosts), 'readable', sum(1 for v in sites.values() if v), 'minute', mins())

    leads, seen_nm = [], set()
    for key, c in cands.items():
        s = sites.get(c.get('host')) if c.get('host') else None
        if c['type'] == 'web' and not s:
            continue
        if c.get('skip'):
            continue
        if c['type'] == 'dir' and not c.get('host'):
            continue
        if c.get('last_post') and (dt.date.today() - dt.date.fromisoformat(c['last_post'])).days > MAX_IDLE_DAYS:
            continue   # Instagram page has gone quiet
        if s and (s['coach'] < 3 or s['therapy'] > s['coach']):
            if c['type'] in ('web', 'dir'):
                continue   # not a coach site (directory, clinic, school)
            s = None; c = dict(c, host='')   # a social lead keeps going without the wrong website
        name = c['name']
        if BAD_TITLE.search(name or '') and s and s['title'] and not BAD_TITLE.search(s['title']):
            name = s['title']
        if not name or len(name) < 3 or len(name.split()) > 8 or BAD_TITLE.search(name) or NON_TARGET.search(name):
            continue
        if s and NON_TARGET.search(s['title']):
            continue
        name = tidy_name(name, c.get('host') or (s['host'] if s else ''), c['type'])
        if not name:
            continue
        L = {'Business': name, 'City': c['city'], 'Found on': dt.date.today().isoformat()}
        if c.get('host'): L['Website'] = 'https://' + c['host']
        ig = c.get('ig') or (c['profile'] if c['type'] == 'ig' else '') or (s['ig'] if s else '')
        fb = (c['profile'] if c['type'] == 'fb' else '') or (s['fb'] if s else '')
        li = (c['profile'] if c['type'] == 'li' else '') or (s['li'] if s else '')
        if ig: L['Instagram'] = ig
        if fb: L['Facebook'] = fb
        if li: L['LinkedIn'] = li
        note = f"AUTO {dt.date.today().isoformat()} | found by {c['q']} dork in {c['city']}"
        if c.get('maps'):
            mm = c['maps']; note += ' | Google Maps' + (f" {mm['score']} stars, {mm['reviews']} reviews" if mm.get('score') else '') + (f" | phone {mm['phone']}" if mm.get('phone') else '')
        if s and s['problems']: note += ' | Problem seen: ' + '; '.join(s['problems'])
        if c.get('last_post'): L['Last posted'] = c['last_post']
        if s and s['email']: L['Email'] = s['email']
        elif c.get('bio_email'): L['Email'] = c['bio_email']
        elif s and s['shared']: note += f" | Shared inbox {s['shared']} NOT stored (decision maker only). Needs owner email."
        L['Claude cross-check'] = note
        em = L.get('Email', '').lower()
        d = root(dom(L.get('Website', ''))) if L.get('Website') else ''
        if (em and em in have_em) or (d and d in have_dom) or norm(name) in have_nm or norm(name) in seen_nm:
            continue
        seen_nm.add(norm(name)); have_nm.add(norm(name))
        if d: have_dom.add(d)
        if em: have_em.add(em)
        leads.append(L)
    def score(L):
        return (5 if L.get('Email') else 0) + (2 if L.get('Website') else 0) + (2 if L.get('Instagram') else 0) + (1 if L.get('Facebook') else 0) \
            + (1 if L.get('LinkedIn') else 0) + (1 if L.get('Last posted') else 0) + (1 if 'exa dork' in L['Claude cross-check'] or 'found by exa' in L['Claude cross-check'] else 0)
    # 22 Nov: the 'No Email or Socials' table was removed, so a lead needs an email or an Instagram/Facebook page to be kept.
    leads = [L for L in leads if L.get('Email') or L.get('Instagram') or L.get('Facebook')]
    leads.sort(key=score, reverse=True)     # best leads first, so the daily cap never cuts the richest rows
    leads = leads[:DAILY_CAP]

    def table_for(L):
        so = bool(L.get('Instagram') or L.get('Facebook'))
        em = bool(L.get('Email'))
        return 'Email and Socials' if em and so else 'Email Only' if em else 'Socials Only (no email)' if so else 'No Email or Socials'

    by = {}
    for L in leads:
        by.setdefault(table_for(L), []).append(L)
    counts = {k: len(v) for k, v in by.items()}
    if DRY:
        log('PUBLIC: DRY RUN new leads', len(leads), counts)
        if '--show' in sys.argv:
            for L in leads: print(json.dumps(L))
        return
    if over(HARD_STOP):
        log('PUBLIC: HARD STOP at minute', mins(), 'before saving, found', len(leads), 'leads; failing so the email goes out')
        sys.exit(3)
    WRITING[0] = True
    has_found = (st or {}).get('found_on', False)
    # 22 Nov: columns can be deleted by hand in Airtable, and an unknown field name rejects the whole batch.
    # Read the live column names once and write only those.
    live = {t['id']: {f['name'] for f in t['fields']} for t in at('GET', f'meta/bases/{base}/tables')['tables']}
    new_pend = {}
    new_enrich = {}
    for t, rows in by.items():
        cols = live.get(tables.get(t))
        if not cols:
            log('PUBLIC: table missing in Airtable, skipped', t, len(rows))
            continue
        for i in range(0, len(rows), 10):
            batch = [{'fields': {k: v for k, v in L.items() if k in FIELDS_OK and k in cols}} for L in rows[i:i + 10]]
            made = at('POST', f'{base}/{tables[t]}', {'records': batch, 'typecast': True}).get('records', [])
            for L_, rec in zip(rows[i:i + 10], made):
                if L_.get('Website') or L_.get('Instagram'):
                    new_enrich[rec['id']] = {'t': t, 'site': L_.get('Website', ''), 'ig': L_.get('Instagram', ''), 'em': L_.get('Email', '')}
            if t == SOCIAL_TABLE:
                for L, rec in zip(rows[i:i + 10], made):
                    if L.get('Website'):
                        new_pend[rec['id']] = dom(L['Website'])
    # Socials-only leads that have a website wait in line for the wide email scan (the seed run lines up the old ones once).
    pend = dict(st['pend']) if st and 'pend' in st else seed_pending(base, tables)
    if not (st and 'pend' in st):
        log('PUBLIC: backfill queue seeded', len(pend))
    for k in list(new_pend):
        pend.setdefault(k, new_pend[k])
    checked, moved = backfill(base, tables, live, pend)
    if new_enrich:                     # the enrich step (enrich.py) fills owner name, site summary and Instagram posts for these
        es = common.load('enrich_state', {'q': {}, 'seeded': False, 'done': 0})
        es['q'].update(new_enrich)
        common.save('enrich_state', es)
    # Save state only after Airtable accepted the rows, so a failed run is retried next time.
    common.save('leads_seen', {'em': sorted(have_em), 'dom': sorted(have_dom), 'nm': sorted(have_nm), 'pend': pend, 'dirs_extra': dirs_extra, 'dirs_seen': dirs_seen,
                               'refreshed': dt.date.today().isoformat() if stale else st['refreshed'], 'found_on': has_found})
    common.commit('lead finder state')
    log('PUBLIC: NEW LEADS ADDED', len(leads), counts, 'minute', mins())
    new_em = sum(1 for L in leads if L.get('Email'))
    log('PUBLIC: EMAILS TODAY', new_em + moved, f'(new leads with email {new_em}, socials-only leads that turned up an email {moved} of {checked} re-checked; target 100)')
    if not leads:
        log('PUBLIC: ZERO NEW LEADS SAVED, counted as a failed run so the email goes out')
        sys.exit(2)


if __name__ == '__main__':
    main()
