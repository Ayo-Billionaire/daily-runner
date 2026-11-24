"""Follow-up drafter (24 Nov 2026). Reads each noqodepro mailbox's Sent folder; when a lead the automation drafted for is due a follow-up,
saves it into that same mailbox's Drafts as a reply in the same thread. Ayo reads and sends it himself. Never sends.

Timing = the live Lumina campaign: follow-up 1 three days after email 1, follow-up 2 four days after follow-up 1, follow-up 3 seven days
after follow-up 2. A lead that wrote back, bounced or already has a draft waiting is left alone. After follow-up 3 the lead is finished.

The ledger (state file `mailbox_ledger`) maps lead address -> {mb, subject, done, why}. Only leads that draftbox.py drafted are in it, so leads
inside Lumina campaigns are never touched. PUBLIC REPO: counts only.

  python3 followups.py          does it
  python3 followups.py --dry    prints counts, writes nothing
"""
import sys, os, re, datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mboxlib as MB
import leadgen as L
import common

DRY = '--dry' in sys.argv
WAITS = {1: 3, 2: 4, 3: 7}      # days to wait after the previous email before follow-up N

# Approved copy: Cold Outreach/coach-owner-emails-2026-11-17/followups_owners.md (Ayo 12 Nov, no oriented endings 17 Nov).
# Follow-up 1 claims were fetched live from https://ayo.noqodepro.com on 17 Nov 2026. No greeting line. No dashes.
FU = {
    1: """Here is one already running, so you can see it work before you say yes.

Vida Plena is a nonprofit. They approve a post once, and every other day at 11am New York it goes out to Instagram and LinkedIn by itself. Then they get an email with both links. Angela, another client, said a setup like this made her life a little bit easier, and then she hired me for a second project.

The screenshots are on my page: ayo.noqodepro.com

Yours would work the same way, with the posts written from your website. Would you mind if I started on yours?

Ayo
""",
    2: """You might be wondering if the posts would sound like you.

They are built from your own words, the ones already on your website and in your posts. You read every draft first, and you can change any line or throw the whole post out.

If it helps, I can write three posts from your site and send them to you by reply, before anything is connected. Is there any reason I should not send them?

Ayo
""",
    3: """Have you given up on posting each week without writing it all yourself?

Ayo
""",
}


def log(*a):
    print('PUBLIC:', *a, flush=True)


def main():
    cfg = L.config()
    boxes = cfg.get('mailboxes', [])
    led = common.load('mailbox_ledger', {})
    ok = 0                                    # health check every day: can the runner log into each mailbox from GitHub
    for mb in boxes:
        try:
            M = MB.connect(mb); ok += 1; M.logout()
        except Exception as e:
            log('mailbox login failed', type(e).__name__)
    log('mailbox logins ok', ok, 'of', len(boxes))
    if not boxes or not led:
        log('followups: nothing to do', 'mailboxes', len(boxes), 'leads in ledger', len(led))
        return
    now = dt.datetime.now(dt.timezone.utc)
    stats = dict(waiting=0, due=0, drafted=0, replied=0, bounced=0, finished=0, already_drafted=0)
    for mb in boxes:
        mine = {a: v for a, v in led.items() if v.get('mb') == mb and not v.get('done')}
        if not mine:
            continue
        M = MB.connect(mb)
        sent = MB.sent_to(M, 75)
        waiting_drafts = MB.draft_targets(M)
        heard = MB.heard_from(M, 60)
        bad = MB.bounced(M, 60)
        for lead, v in mine.items():
            hist = sent.get(lead, [])
            hist = [h for h in hist if h[0]]
            n = len(hist)
            if n == 0:
                stats['waiting'] += 1            # email 1 is still a draft or not sent yet
                continue
            dom = lead.split('@')[1]
            if lead in bad:
                v['done'], v['why'] = True, 'bounced'; stats['bounced'] += 1; continue
            if lead in heard or any(h.endswith('@' + dom) for h in heard):
                v['done'], v['why'] = True, 'replied'; stats['replied'] += 1; continue
            if n >= 4:
                v['done'], v['why'] = True, 'all follow ups sent'; stats['finished'] += 1; continue
            step = n                              # n emails out means follow-up number n is next
            last = hist[-1][0]
            if (now - last).days < WAITS[step]:
                continue
            stats['due'] += 1
            if lead in waiting_drafts:
                stats['already_drafted'] += 1
                continue
            subj = hist[0][2]
            subject = subj if subj.lower().startswith('re:') else 'Re: ' + subj
            if DRY:
                continue
            ok = MB.append_draft(M, mb, lead, subject, FU[step], in_reply_to=hist[0][1], references=hist[0][1])
            if ok:
                stats['drafted'] += 1
                v.setdefault('fu', {})[str(step)] = now.date().isoformat()
        M.logout()
    if not DRY:
        common.save('mailbox_ledger', led)
        common.commit('mailbox ledger')
    log('followups', stats)


if __name__ == '__main__':
    main()
