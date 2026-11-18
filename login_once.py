"""Run ONCE by the owner on the Mac (and again only if an alert says a login expired).
Opens the sender's own browser. the owner logs into Instagram and Facebook in it (password, codes:
all typed by him). When both are logged in, the browser's state is encrypted with DM_STATE_KEY
(~/.secrets/api-keys.env, same value as the STATE_KEY secret), saved to state/browser.enc and pushed.
Nothing is printed: no cookie, no key."""
import json, os, subprocess, sys, time
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'dmsender'))
from send import ua_for  # same browser identity as the cloud runs

LIMIT = 15 * 60


def logged_in(ctx):
    names = {(c['domain'].lstrip('.'), c['name']) for c in ctx.cookies()}
    ig = ('instagram.com', 'sessionid') in names
    fb = ('facebook.com', 'c_user') in names and ('facebook.com', 'xs') in names
    return ig, fb


def main():
    with sync_playwright() as p:
        br = p.chromium.launch(headless=False, args=['--disable-blink-features=AutomationControlled'])
        ctx = br.new_context(user_agent=ua_for(br), viewport={'width': 1440, 'height': 900}, locale='en-US')
        ig_page = ctx.new_page()
        ig_page.goto('https://www.instagram.com/accounts/login/')
        fb_page = ctx.new_page()
        fb_page.goto('https://www.facebook.com/login/')
        print('A browser window opened with two tabs. Log into Instagram (tab 1) and Facebook (tab 2) yourself.')
        print('Use the sending accounts. Enter any code they send. This window closes by itself when both are in.')
        t0 = time.time()
        while time.time() - t0 < LIMIT:
            ig, fb = logged_in(ctx)
            if ig and fb:
                time.sleep(8)  # let both sites finish setting their cookies
                break
            time.sleep(4)
        else:
            ig, fb = logged_in(ctx)
            print(f'Timed out. Instagram logged in: {ig}. Facebook logged in: {fb}. Nothing saved; run it again.')
            br.close()
            return 1
        state = json.dumps(ctx.storage_state()).encode()
        br.close()
    sys.path.insert(0, os.path.join(ROOT, 'dmsender'))
    from common import state_key  # DM_STATE_KEY in ~/.secrets/api-keys.env, same value as the STATE_KEY secret
    os.makedirs(os.path.join(ROOT, 'state'), exist_ok=True)
    with open(os.path.join(ROOT, 'state', 'browser.enc'), 'wb') as f:
        f.write(state_key().encrypt(state))
    subprocess.run(['git', 'add', 'state/browser.enc'], cwd=ROOT, check=True)
    subprocess.run(['git', 'commit', '-q', '-m', 'state update'], cwd=ROOT, check=True)
    subprocess.run(['git', 'pull', '-q', '--rebase'], cwd=ROOT, check=True)
    subprocess.run(['git', 'push', '-q'], cwd=ROOT, check=True)
    print('Saved. Instagram and Facebook are logged in inside the sender browser. Tell Claude "done".')
    return 0


if __name__ == '__main__':
    sys.exit(main())
