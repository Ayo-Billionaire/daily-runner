"""One sending run: a few DMs from the queue, paced like a person, stops the day on any warning.
Usage: python send.py [--limit N] [--dry-run] [--only instagram|facebook]"""
import argparse, json, os, random, re, sys, time, datetime as dt
from common import CFG, STATE, state_key, load, save, commit, email, run_link, daily_cap, is_send_day, now, today

SHOTS = os.path.join(os.path.dirname(STATE), 'shots')
os.makedirs(SHOTS, exist_ok=True)
def ua_for(br):
    # Same identity on the owner's Mac (login_once.py) and in the cloud: a Mac Chrome UA with the real engine version.
    major = br.version.split('.')[0]
    return f'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36'

STOP_TEXT = re.compile(r"try again later|we restrict certain activity|action blocked|temporarily blocked|"
                       r"you can.t use this feature|confirm it.s you|suspicious (login|activity)|we limit how often|"
                       r"your account has been (locked|disabled|suspended)|help us confirm|enter your pin|"
                       r"you.re temporarily restricted|log in to continue", re.I)
STOP_URL = re.compile(r'/challenge|/checkpoint|/accounts/login|/login\.php|/login/\?|two_step|/suspended', re.I)


class Stop(Exception):
    """Meta pushed back: stop sending for the rest of the day."""


class Skip(Exception):
    """This one coach cannot be messaged; move on."""


def header_cookies(env, dom):
    """IG_COOKIE_HEADER / FB_COOKIE_HEADER = the full `cookie:` request header copied from Chrome DevTools
    (Network tab). Carries every cookie, including the device ones (mid, ig_did, datr) Instagram checks."""
    raw = os.environ.get(env, '').strip()
    if raw.lower().startswith('cookie:'):
        raw = raw[7:].strip()
    out = []
    for part in raw.split(';'):
        if '=' not in part:
            continue
        name, value = part.strip().split('=', 1)
        if name:
            out.append({'name': name, 'value': value, 'domain': dom, 'path': '/', 'secure': True,
                        'httpOnly': name in ('sessionid', 'xs', 'fr', 'datr', 'ig_did', 'mid', 'rur'), 'sameSite': 'None'})
    return out


def cookies():
    full = header_cookies('IG_COOKIE_HEADER', '.instagram.com') + header_cookies('FB_COOKIE_HEADER', '.facebook.com')
    have = {(c['domain'], c['name']) for c in full}
    c = list(full)
    ig = {'sessionid': 'IG_SESSIONID', 'csrftoken': 'IG_CSRFTOKEN', 'ds_user_id': 'IG_DS_USER_ID'}
    fb = {'c_user': 'FB_C_USER', 'xs': 'FB_XS', 'datr': 'FB_DATR', 'fr': 'FB_FR'}
    for dom, m in (('.instagram.com', ig), ('.facebook.com', fb)):
        for name, env in m.items():
            v = os.environ.get(env, '').strip()
            if v and (dom, name) not in have:
                c.append({'name': name, 'value': v, 'domain': dom, 'path': '/', 'secure': True,
                          'httpOnly': name in ('sessionid', 'xs', 'fr', 'datr'), 'sameSite': 'None'})
    return c


def guard(page, where):
    if STOP_URL.search(page.url):
        raise Stop(f'{where}: Meta sent the browser to {page.url} (login or security check)')
    try:
        body = page.inner_text('body', timeout=5000)
    except Exception:
        body = ''
    m = STOP_TEXT.search(body)
    if m:
        raise Stop(f'{where}: page says "{m.group(0)}"')


HOME = {'instagram': 'https://www.instagram.com/', 'facebook': 'https://www.facebook.com/'}


def logged_out(page):
    """True when the page is a login wall: password box, 'Log in' form, or the 'Continue as <you>' picker."""
    try:
        if page.locator('input[name="password"], input[name="pass"], input[type="password"]').count() > 0:
            return 'password'
        if page.get_by_text(re.compile(r'^Use another profile$|^Switch accounts$', re.I)).count() > 0:
            return 'picker'
        if page.get_by_role('button', name=re.compile(r'^Log in$', re.I)).count() > 0:
            return 'login'
    except Exception:
        pass
    return None


def ensure_logged_in(page, platform):
    """Open the home page once per run. 'Continue as you' (saved session, no password) is clicked once.
    Anything that wants a password or a security check stops the day and emails the owner."""
    goto(page, HOME[platform])
    time.sleep(random.uniform(4, 6))
    guard(page, f'{platform} home')
    state = logged_out(page)
    if state == 'picker':
        btn = page.get_by_role('button', name=re.compile(r'^Continue', re.I)).first
        if btn.count() == 0:
            btn = page.get_by_text(re.compile(r'^Continue', re.I)).first
        btn.click()
        time.sleep(random.uniform(6, 9))
        guard(page, f'{platform} continue')
        state = logged_out(page)
    if state:
        shot(page, f'{platform}-login-needed')
        raise Stop(f'{platform}: not logged in ({state} screen). The saved login needs re-exporting (README steps).')


def shot(page, name):
    p = os.path.join(SHOTS, f"{now().strftime('%H%M%S')}-{re.sub(r'[^a-z0-9]+', '-', name.lower())[:40]}.png")
    try:
        page.screenshot(path=p)
    except Exception:
        pass
    return p


def type_dm(page, box, dm):
    box.click()
    paras = [p.strip() for p in dm.split('\n\n') if p.strip()]
    for i, p in enumerate(paras):
        for line_i, line in enumerate(p.split('\n')):
            if line_i:
                page.keyboard.press('Shift+Enter')
            page.keyboard.type(line, delay=random.randint(18, 45))
        if i < len(paras) - 1:
            page.keyboard.press('Shift+Enter')
            page.keyboard.press('Shift+Enter')
        time.sleep(random.uniform(0.4, 1.2))
    typed = box.inner_text().replace(' ', ' ')
    last = paras[-1]
    if paras[0] not in typed or last not in typed:
        raise Skip('typed text did not land in the message box as written')
    return last


BOX_SELS = ['div[role="textbox"][aria-label*="Message" i]', 'div[contenteditable="true"][aria-label*="Message" i]',
            'div[role="textbox"][contenteditable="true"]', 'p[contenteditable="true"]']


def visible_boxes(page):
    """Every visible message box on the page, de duplicated (one box can match several selectors)."""
    seen, out = set(), []
    for sel in BOX_SELS:
        loc = page.locator(sel)
        for k in range(loc.count()):
            el = loc.nth(k)
            try:
                if not el.is_visible():
                    continue
                h = el.evaluate('e => { e.dataset.dmsBox = e.dataset.dmsBox || Math.random().toString(36).slice(2); return e.dataset.dmsBox }')
            except Exception:
                continue
            if h not in seen:
                seen.add(h); out.append(el)
    return out


def close_open_chats(page):
    """17 Nov 2026: a chat window left open from an earlier send (Jay Izso) sat beside the new one, and the old
    find_box took the LAST box on the page, so Amy's DM went to Jay. Close every open chat window first
    (Facebook 'Close chat'; Instagram's chat panel 'Close')."""
    for _ in range(3):
        btns = page.locator('[aria-label="Close chat"], [aria-label="Close Chat"], div[role="button"]:has(svg[aria-label="Close"]), [role="button"][aria-label="Close"]')
        clicked = False
        for k in range(btns.count()):
            b = btns.nth(k)
            try:
                if b.is_visible():
                    b.click(timeout=3000); time.sleep(0.6); clicked = True; break
            except Exception:
                pass
        if not clicked:
            return


GENERIC_HEADINGS = {'facebook', 'chats', 'notifications', 'messenger', 'details', 'featured', 'posts', 'about', 'intro', 'photos', 'friends',
                    'reels', 'followers', 'links', 'more', 'home', 'search', 'people also like', 'reviews', 'services', 'products', 'events'}


def target_name(page, item):
    """Who this chat must be with, read from the profile BEFORE clicking Message.
    Instagram: the handle in the URL. Facebook: the page or profile name in the first visible h1."""
    if item['platform'] == 'instagram':
        m = re.search(r'instagram\.com/([^/?#]+)', page.url)
        return m.group(1).lower() if m else ''
    # Facebook pages have no h1 and the tab title is just "Facebook" (checked live 17 Nov 2026). The page name is
    # the largest text near the top (32px). Poll up to 25s because the relayed phone link renders slowly.
    # 24 Nov 2026: read too early, the headings of the page body ("Details", "Featured") are the biggest text and were taken
    # as the coach name (Skillway: "Write to Details"), or nothing was found yet (B Well, Untapped Learning). So: skip generic
    # headings, look lower down (tall cover banners push the name below 450 px), and only accept a name seen 3 times in a row.
    js = """() => { let best = null, bs = 0;
        for (const e of document.querySelectorAll('h1,h2,span,div,a')) { const r = e.getBoundingClientRect();
            if (r.top < 50 || r.top > 650 || r.height === 0) continue; const t = (e.innerText || '').trim();
            if (!t || t.includes('\\n') || t.length > 80) continue; const fs = parseFloat(getComputedStyle(e).fontSize);
            if (GENERIC.indexOf(t.toLowerCase()) >= 0) continue;
            if (fs > bs) { bs = fs; best = t; } }
        return bs >= 20 ? best : ''; }""".replace('GENERIC', json.dumps(sorted(GENERIC_HEADINGS)))
    deadline = time.time() + 30
    last, seen = '', 0
    while time.time() < deadline:
        try:
            t = (page.evaluate(js) or '').strip()
        except Exception:
            t = ''
        if t and t.lower() not in GENERIC_HEADINGS:
            seen = seen + 1 if t == last else 1
            last = t
            if seen >= 3:
                return t
        else:
            last, seen = '', 0
        time.sleep(1)
    hs = page.locator('h1')
    for k in range(hs.count()):
        try:
            t = hs.nth(k).inner_text(timeout=2000).strip()
        except Exception:
            continue
        if t and t.lower() not in GENERIC_HEADINGS:
            return t
    # Fallback: the tab title ("(3) Creative Catapult Coaching | Facebook") is set before the page body renders.
    t = re.sub(r'^\(\d+\)\s*', '', page.title() or '')
    t = re.sub(r'\s*\|\s*Facebook\s*$', '', t).strip()
    return '' if t.lower() in ('', 'facebook', 'log in or sign up to view') else t


def chat_has_name(box, name):
    """True when the small chat window that holds this box shows the target's name (header or intro card).
    Walks up from the box while the element stays chat sized (700 px wide or less), so the profile page
    behind the chat window, which also shows the name, cannot make it pass."""
    key = re.sub(r'\s+', ' ', name).strip().lower()[:12]
    if not key:
        return False
    text = box.evaluate("""e => { let n = e, best = '';
        while (n && n.parentElement) { n = n.parentElement; const r = n.getBoundingClientRect();
            if (r.width > 700) break; best = n.innerText || ''; }
        return best; }""")
    return key in re.sub(r'\s+', ' ', text or '').lower()


def find_box(page):
    """Return the ONE message box on the page. Zero or more than one = do not type anything (Skip)."""
    deadline = time.time() + 40  # chat windows load slowly over the relayed phone link (17 Nov 2026)
    boxes = []
    while time.time() < deadline:
        boxes = visible_boxes(page)
        if boxes:
            break
        time.sleep(0.5)
    if len(boxes) == 1:
        return boxes[0]
    if len(boxes) > 1:
        raise Skip(f'{len(boxes)} chat windows open at once; refusing to guess which one is this coach')
    return None


def goto(page, url):
    """The phone link can drop for a moment (ERR_SOCKS_CONNECTION_FAILED, 17 Nov 2026): wait and retry twice."""
    for attempt in range(3):
        try:
            return page.goto(url, wait_until='domcontentloaded', timeout=90000)
        except Exception as e:
            if not ('net::ERR_' in str(e) or 'Timeout' in str(e)) or attempt == 2:
                raise
            time.sleep(30)


def norm(t):
    return re.sub(r'[^a-z0-9]+', '', (t or '').lower())


def find_fb_box(page, name, wait=40):
    """17 Nov 2026: two DMs went out as PUBLIC COMMENTS because the post comment box ("Comment as <account name>") was
    the first typing box on the page. Facebook labels the Messenger box "Write to <name>" (checked live on a page
    chat and a person chat). Only that box, for THIS coach, is ever typed into."""
    key = norm(name)[:12]
    deadline = time.time() + wait
    while time.time() < deadline:
        loc = page.locator('[contenteditable="true"][aria-label^="Write to "]')
        for k in range(loc.count()):
            el = loc.nth(k)
            try:
                if not el.is_visible():
                    continue
                who = norm((el.get_attribute('aria-label') or '')[len('Write to '):])
            except Exception:
                continue
            if key and (who.startswith(key) or key.startswith(who[:12])) and len(who) >= 4:
                return el
        time.sleep(1)
    return None


def in_chat_window(box, text):
    """True when `text` shows inside the chat window that holds this box (not anywhere else on the page)."""
    return bool(box.evaluate("""(e, t) => { let n = e;
        for (let i = 0; i < 25 && n; i++) { if (n.querySelector && n.querySelector('[aria-label="Close chat"]')) break; n = n.parentElement; }
        return !!n && (n.innerText || '').includes(t); }""", text))


def open_chat(page, item):
    goto(page, item['url'])
    time.sleep(random.uniform(3, 6))
    guard(page, 'profile')
    lo = logged_out(page)
    if lo:
        raise Stop(f"{item['platform']}: login wall ({lo}) on {item['business']}'s profile; session dropped")
    if item['platform'] == 'instagram' and 'instagram.com/' not in page.url:
        raise Skip(f'Instagram link opened {page.url}')
    try:   # the header is drawn when the Message button is: wait for it before reading the name (slow phone link, 24 Nov 2026)
        page.get_by_role('button', name=re.compile(r'^(Message|Send message)$', re.I)).first.wait_for(state='visible', timeout=25000)
    except Exception:
        pass
    name = target_name(page, item)
    if not name:
        raise Skip('could not read the coach name on the profile, so the chat cannot be checked')
    close_open_chats(page)
    btn = page.get_by_role('button', name=re.compile(r'^(Message|Send message)$', re.I)).first
    try:
        btn.wait_for(state='visible', timeout=12000)
    except Exception:
        btn = page.get_by_role('link', name=re.compile(r'^(Message|Send message)$', re.I)).first
        try:
            btn.wait_for(state='visible', timeout=5000)
        except Exception:
            raise Skip('no Message button on the profile (private, page without messaging, or not found)')
    btn.click()
    time.sleep(random.uniform(3, 6))
    guard(page, 'chat')
    if item['platform'] == 'facebook':
        box = find_fb_box(page, name, wait=15)
        if box is None:
            # 24 Nov 2026 (Untapped Learning): some Pages open the chat on a "Get started" screen with no typing box until it is
            # tapped (it only sends Facebook's own start button, no text of ours). Tap it, then wait for the real box.
            gs = page.locator('[role="button"]:has-text("Get started"), [aria-label="Get started"]').first
            try:
                gs.wait_for(state='visible', timeout=3000)
                gs.click()
                time.sleep(random.uniform(3, 5))
                box = find_fb_box(page, name)
            except Exception:
                box = find_fb_box(page, name, wait=25)
        if box is None:
            raise Skip(f'message box did not appear (no Messenger box labelled Write to {name})')
        return box
    box = find_box(page)
    if box is None:
        raise Skip('message box did not appear')
    if not chat_has_name(box, name):
        raise Skip(f'the open chat window does not show {name}; refusing to type into it')
    return box


def send_one(page, item):
    box = open_chat(page, item)
    first_sentence = item['dm'].split('\n\n')[1][:60] if '\n\n' in item['dm'] else item['dm'][:60]
    try:
        if page.get_by_text(first_sentence, exact=False).count() > 0:
            raise Skip('this DM is already in the thread (sent before)')
    except Skip:
        raise
    except Exception:
        pass
    last = type_dm(page, box, item['dm'])
    time.sleep(random.uniform(1.0, 2.5))
    page.keyboard.press('Enter')
    time.sleep(random.uniform(4, 7))
    # 18 Nov 2026: over the phone relay Facebook still showed "Sending" at the screenshot. Wait for it to clear
    # (up to 45 s) and never record a message Meta says failed.
    for _ in range(15):
        if page.get_by_text('Sending', exact=True).count() == 0:
            break
        time.sleep(3)
    for bad in ('Failed to send', 'Not sent', "Couldn't send"):
        if page.get_by_text(bad, exact=False).count() > 0:
            raise Skip(f'Meta showed "{bad}" after pressing send')
    guard(page, 'after send')
    left = box.inner_text().strip() if box.is_visible() else ''
    if item['platform'] == 'facebook':
        if left or not in_chat_window(box, last[:50]):
            raise Skip('could not see the sent message inside the Messenger chat window after pressing send')
    else:
        bubble = page.get_by_text(last[:50], exact=False)
        if left or bubble.count() == 0:
            raise Skip('could not see the sent message in the thread after pressing send')
    return shot(page, f"sent-{item['business']}")


def first_day(sent):
    """rid -> date of the first DM attempt to that coach."""
    out = {}
    for v in sent.values():
        r, d = v.get('rid'), v.get('date', '')
        if r and (r not in out or d < out[r]):
            out[r] = d
    return out


def plan_today(sent):
    """Cap counts COACHES started today, not messages (the owner 17 Nov 2026: "20 coaches and not 20 messages")."""
    cap = daily_cap()
    fd = first_day(sent)
    done = len({v['rid'] for v in sent.values() if v.get('status') == 'sent' and fd.get(v.get('rid')) == today()})
    slots = [dt.datetime.combine(now().date(), dt.time.fromisoformat(s), tzinfo=dt.timezone.utc) for s in CFG['slots_utc']]
    left_slots = max(1, sum(1 for s in slots if s >= now() - dt.timedelta(minutes=15)))
    remaining = max(0, cap - done)
    return cap, done, -(-remaining // left_slots)


def pick(items, sent, n):
    """1) the other platform of coaches messaged at least 3 hours ago (they do not count again),
    2) then up to n new coaches, ONE platform each (their other platform follows 3+ hours later),
    alternating Instagram and Facebook. At most max_messages_per_run in total."""
    gap = dt.timedelta(hours=CFG['second_platform_after_hours'])
    last = {}
    for v in sent.values():
        r = v.get('rid')
        at = dt.datetime.fromisoformat(v['at']) if v.get('at') else dt.datetime.fromisoformat(v.get('date', '2000-01-01') + 'T00:00:00+00:00')
        last[r] = max(last.get(r, at), at)
    out = [i for i in items if i['id'] in last and now() - last[i['id']] >= gap]
    seen, new, use = set(last), [], {'instagram': 0, 'facebook': 0}
    for i in out:
        use[i['platform']] += 1
    by = {}
    for i in items:
        if i['id'] not in seen:
            by.setdefault(i['id'], []).append(i)
    for rid, opts in by.items():
        if len(new) >= n:
            break
        opts.sort(key=lambda i: use[i['platform']])
        use[opts[0]['platform']] += 1
        new.append(opts[0])
    return (out + new)[:CFG['max_messages_per_run']]


def public_counts(results):
    """The only lines the public Actions log shows (the workflow greps PUBLIC:). Counts, never names."""
    c = {w: sum(1 for r in results if r.startswith(w)) for w in ('SENT', 'SKIP', 'RETRY')}
    print(f"PUBLIC: sent {c['SENT']}, skipped {c['SKIP']}, retry later {c['RETRY']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--only')
    ap.add_argument('--force', action='store_true', help='ignore day/window/cap (manual test only)')
    a = ap.parse_args()
    sent = load('sent.json', {})
    q = load('queue.json', {'items': []})
    pause = load('pause.json', {})
    if pause.get('until', '') >= today() and not a.force:
        print('paused until', pause['until'], pause.get('reason'))
        print('PUBLIC: paused')
        return
    lo, hi = CFG['window_utc']
    hm = now().strftime('%H:%M')
    if not a.force and (not is_send_day() or not (lo <= hm <= hi)):
        print('outside send days/window', hm)
        print('PUBLIC: outside send window')
        return
    cap, done, n = plan_today(sent)
    if a.limit is not None:
        n = a.limit if a.force else min(a.limit, n)
    enabled = CFG.get('platforms_enabled', ['instagram', 'facebook'])
    items = pick([i for i in q['items'] if i['key'] not in sent and (a.force or i['platform'] in enabled) and (not a.only or i['platform'] == a.only)], sent, n)
    if a.limit is not None:
        items = items[:a.limit]  # a manual limit caps ALL messages, follow-up platforms included (18 Nov test sent 2 on limit 1)
    print(f'cap {cap} coaches, new coaches today {done}, this run {len(items)} messages')
    print(f'PUBLIC: cap {cap}, started today {done}, this run {len(items)}' + (' (dry run)' if a.dry_run else ''))
    if not items:
        return
    if a.dry_run:
        for i in items:
            print('DRY', i['platform'], i['business'], i['url'])
        return
    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as p:
        launch = {'headless': True, 'args': ['--disable-blink-features=AutomationControlled']}
        if os.environ.get('DM_PROXY'):
            launch['proxy'] = {'server': os.environ['DM_PROXY']}
        br = p.chromium.launch(**launch)
        enc = os.path.join(STATE, 'browser.enc')
        saved = None
        if os.path.exists(enc):
            saved = json.loads(state_key().decrypt(open(enc, 'rb').read()))
        ctx = br.new_context(user_agent=ua_for(br), viewport={'width': 1440, 'height': 900}, locale='en-US', storage_state=saved)
        if not saved:
            ctx.add_cookies(cookies())

        def keep_browser():
            # Save the browser back after the run so every run is the same device with fresh cookies.
            if saved is not None:
                with open(enc, 'wb') as f:
                    f.write(state_key().encrypt(json.dumps(ctx.storage_state()).encode()))
        page = ctx.new_page()
        checked = set()
        for k, item in enumerate(items):
            try:
                if item['platform'] not in checked:
                    ensure_logged_in(page, item['platform'])
                    checked.add(item['platform'])
                proof = send_one(page, item)
                sent[item['key']] = {'rid': item['id'], 'tid': item['tid'], 'business': item['business'], 'platform': item['platform'],
                                    'date': today(), 'at': now().isoformat(timespec='seconds'), 'status': 'sent', 'ticked': False}
                results.append(f"SENT  {item['platform']:9} {item['business']}")
            except Skip as e:
                slow = any(x in str(e) for x in ('message box did not appear', 'could not read the coach name'))
                att = load('attempts.json', {})
                att[item['key']] = att.get(item['key'], 0) + 1
                save('attempts.json', att)
                if slow and att[item['key']] < 3:
                    # A slow page is not a reason to give up on a coach: try again in a later run (max 3 tries).
                    results.append(f"RETRY {item['platform']:9} {item['business']}: {e} (try {att[item['key']]} of 3)")
                    shot(page, f"retry-{item['business']}")
                    keep_browser()
                    commit(f"{today()} retry later {item['business']}")
                    continue
                sent[item['key']] = {'rid': item['id'], 'tid': item['tid'], 'business': item['business'], 'platform': item['platform'],
                                    'date': today(), 'at': now().isoformat(timespec='seconds'), 'status': 'skipped', 'reason': str(e)}
                results.append(f"SKIP  {item['platform']:9} {item['business']}: {e}")
                shot(page, f"skip-{item['business']}")
                email(f"DM sender: skipped {item['business']}",
                      f"{item['platform']} {item['url']}\nReason: {e}\n\nIt will not be retried automatically. Run: {run_link()}")
            except Stop as e:
                shot(page, 'stopped')
                warn = [w for w in load('warnings.json', []) if w >= (now().date() - dt.timedelta(days=14)).isoformat()]
                warn.append(today())
                save('warnings.json', warn)
                if len(warn) >= 2:
                    until, what = '9999-12-31', 'This is the second warning in 14 days, so it stays STOPPED until you tell Claude to start it again.'
                else:
                    until = (now().date() + dt.timedelta(days=CFG['pause_days_on_warning'])).isoformat()
                    what = f"Paused until {until}. Then it restarts one step slower."
                rp = load('ramp.json', {})
                rp['shift_days'] = rp.get('shift_days', 0) + 7 + CFG['pause_days_on_warning']
                save('ramp.json', rp)
                save('pause.json', {'until': until, 'reason': str(e), 'at': now().isoformat(timespec='seconds')})
                save('sent.json', sent)
                keep_browser()
                commit(f'stop {today()}: {e}')
                email('DM sender STOPPED: Meta pushed back',
                      f"{e}\n\nWhile sending to: {item['business']} ({item['platform']}, {item['url']})\n{what}\n"
                      f"If it says login, code or PIN: on your Mac run  cd ~/Documents/Agentic\\ Workflows/WorkSpace/daily-runner && git pull -q && python3 login_once.py  and log in again, then tell Claude.\n\n"
                      f"Run (screenshots attached as an artifact): {run_link()}")
                print('\n'.join(results))
                public_counts(results)
                print('PUBLIC: stopped for the day (details emailed)')
                return
            except Exception as e:
                shot(page, f"error-{item['business']}")
                save('sent.json', sent)
                commit(f'error {today()}')
                raise RuntimeError(f"while sending to {item['business']} ({item['url']}): {type(e).__name__}: {e}")
            save('sent.json', sent)
            keep_browser()
            commit(f"{today()} {sent[item['key']]['status']} {item['business']}")
            if k < len(items) - 1:
                time.sleep(random.uniform(*CFG['gap_seconds']))
        br.close()
    print('\n'.join(results))
    public_counts(results)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        email('DM sender ERROR: a sending run crashed', f'{type(e).__name__}: {e}\n\nRun (screenshots in artifacts): {run_link()}')
        raise
