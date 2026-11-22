#!/usr/bin/env python3
"""Decision maker email gate. Ayo, 16 Nov 2026: "never give me customer service email or company email".
An address passes only if it looks like a PERSON's own mailbox. Shared, role, support and company inboxes fail.
Usage: python3 -I email_check.py addr [addr...]      (prints PASS or FAIL with the reason)
       import email_check; email_check.check('a@b.com') -> (ok, reason)
A pass is not proof the person owns it: the address must also have been SEEN on their site, profile or a finder tool
and verified (SMTP or a verifier). Never guess name@domain and send."""
import re, sys
GENERIC = {
 'info','hello','hi','hey','contact','contactus','contact-us','support','help','helpdesk','admin','administrator','office','team','sales','mail','email','enquiries','enquiry','inquiries','inquiry',
 'bookings','booking','book','appointments','appointment','schedule','scheduling','service','services','customerservice','customer','customers','care','customercare','clientcare','clients','client',
 'general','reception','frontdesk','front','accounts','accounting','billing','finance','hr','jobs','careers','press','media','marketing','pr','news','newsletter','subscribe','orders','order','shop','store',
 'webmaster','web','website','noreply','no-reply','donotreply','do-not-reply','feedback','questions','ask','askus','connect','welcome','studio','coaching','coach','wellness','life','business','consulting',
 'getstarted','start','join','members','membership','community','events','event','partners','partnerships','sponsor','speaking','speaker','invoices','payments','privacy','legal','abuse','postmaster','root'
}
BAD_PREFIX = ('info','contact','support','hello','admin','office','team','sales','booking','service','customer','enquir','inquir','noreply','no-reply','donotreply')
def check(addr):
    a = (addr or '').strip().lower()
    if not re.match(r'^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$', a): return False, 'not a valid address'
    local, dom = a.split('@')
    base = re.sub(r'[._\-+0-9]+', '', local)
    if local in GENERIC or base in GENERIC: return False, f'shared inbox ({local}@)'
    if any(local.startswith(p) and len(local) <= len(p) + 4 for p in BAD_PREFIX): return False, f'looks like a shared inbox ({local}@)'
    if re.match(r'^(info|contact|support|hello|admin|office|team|sales)[a-z]{3,}$', local) and local.startswith(('info','contact','support')):
        return False, f'role style name ({local}@), e.g. infovahanacoaching'
    if len(base) < 2: return False, 'too short to be a name'
    return True, 'looks personal'
if __name__ == '__main__':
    if '--selftest' in sys.argv:
        good = ['ayo@x.com','amanda.beilke@x.com','kristin@vottacoaching.com','jenhanson@nautiluslifecoach.com','laura@expandyourpossibilities.net','erich@myblueshore.com']
        bad = ['info@x.com','hello@amandahelen.com','contact@x.com','support@kandywindom.com','office@kingdomcoaching.pro','infovahanacoaching@gmail.com','admin@x.com','team@x.com','bookings@x.com','customerservice@x.com','help@x.com','hi@x.com']
        fails = [g for g in good if not check(g)[0]] + [b for b in bad if check(b)[0]]
        print('selftest', 'FAIL ' + str(fails) if fails else 'PASS'); sys.exit(1 if fails else 0)
    for x in sys.argv[1:]:
        ok, why = check(x); print(('PASS ' if ok else 'FAIL ') + x + ' | ' + why)
