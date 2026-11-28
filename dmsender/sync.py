"""Once a day: push pending Approve DM ticks to Airtable, then pull the unsent DM queue.
Airtable free plan = 1,000 calls a month, so this is the ONLY place that reads the base (about 4 to 8 calls)."""
import re, sys
from common import CFG, _at, load, save, commit, airtable_rows, airtable_tick, email, run_link, daily_cap, today

FIELDS = ['Business', 'Email', 'Cold DM', 'Approve DM', 'Do not contact', 'Instagram', 'Facebook']


def platforms_of(f):
    """the owner 17 Nov 2026: a coach gets the DM on BOTH Instagram and Facebook when both links exist, else on the one
    that exists. Never LinkedIn. The DM itself is written from their most active account."""
    return [(p, f[k]) for p, k in (('instagram', 'Instagram'), ('facebook', 'Facebook')) if f.get(k)]


def ticks_due(rows, sent):
    """Tick Approve DM once every platform of a coach is done (sent or skipped) and at least one was sent."""
    due = []
    for r in rows:
        f = r['f']
        if f.get('Approve DM'):
            continue
        keys = [f"{r['id']}:{p}" for p, _ in platforms_of(f)]
        if keys and all(k in sent for k in keys) and any(sent[k].get('status') == 'sent' for k in keys):
            due.append((r['tid'], r['id']))
    return due


def replied_keys():
    """28 Nov: everyone in the Replied Yes / Replied No tables, plus every email address Lumina says replied (state from
    replies.py), so a coach who answered on any channel never gets a DM. Two Airtable reads a day."""
    a = CFG['airtable']
    keys, calls = set(), 0
    norm = lambda u: (u or '').strip().lower().rstrip('/').split('?')[0]
    try:
        tables = {t['name']: t['id'] for t in _at('GET', f"meta/bases/{a['base']}/tables")['tables']}
        calls += 1
        for name in ('Replied Yes', 'Replied No'):
            if name not in tables:
                continue
            offset = None
            while True:
                q = {'pageSize': 100, 'fields[]': ['Email', 'Instagram', 'Facebook', 'Original record ID']}
                if offset:
                    q['offset'] = offset
                j = _at('GET', f"{a['base']}/{tables[name]}", query=q)
                calls += 1
                for r in j['records']:
                    f = r['fields']
                    for k in ('Email', 'Instagram', 'Facebook', 'Original record ID'):
                        if f.get(k):
                            keys.add(norm(f[k]))
                offset = j.get('offset')
                if not offset:
                    break
    except Exception as e:
        print('replied tables read failed:', type(e).__name__)
    keys |= set(load('replies.json', {}).get('emails', []))
    keys.discard('')
    return keys, calls, norm


def main():
    sent = load('sent.json', {})
    rows, c2 = airtable_rows(FIELDS)
    replied, c3, norm = replied_keys()
    c2 += c3
    due = ticks_due(rows, sent)
    ok, c1 = airtable_tick(due) if due else (set(), 0)
    missing = [rid for _, rid in due if rid not in ok]
    if missing:
        raise RuntimeError(f'{len(missing)} Approve DM ticks did not read back: {missing[:5]}')
    ticked = len(ok)
    queue, skipped, waiting = [], [], []
    n_replied = 0
    approved = set(load('approved.json', []))
    for r in rows:
        f = r['f']
        dm = (f.get('Cold DM') or '').strip()
        done_here = any(v.get('rid') == r['id'] for v in sent.values())
        # A row the owner ticked by hand (sent manually) is finished; one the sender started keeps its other platform.
        if not dm or f.get('Do not contact') or (f.get('Approve DM') and not done_here):
            continue
        if replied & {norm(f.get(k)) for k in ('Email', 'Instagram', 'Facebook')} or r['id'] in replied:
            n_replied += 1   # counted, not alerted: the owner already has their reply
            continue
        plats = [(p, u) for p, u in platforms_of(f) if f"{r['id']}:{p}" not in sent]
        if not plats:
            if not platforms_of(f):
                skipped.append(f"{f.get('Business')}: no Instagram or Facebook link")
            continue
        if r['id'] not in approved:  # the owner's go not given yet for this DM (approve.py adds it on his launch go)
            waiting.append(f.get('Business'))
            continue
        if len([p for p in dm.split('\n\n') if p.strip()]) < 4:
            skipped.append(f"{f.get('Business')}: DM is not paragraphed")
            continue
        for plat, url in plats:
            queue.append({'key': f"{r['id']}:{plat}", 'id': r['id'], 'tid': r['tid'], 'table': r['table'], 'pos': r['pos'],
                          'business': f.get('Business'), 'platform': plat, 'url': url, 'dm': dm})
    order = list(CFG['airtable']['tables'])
    queue.sort(key=lambda q: (order.index(q['table']), q['pos'], q['platform']))
    save('queue.json', {'synced': today(), 'items': queue, 'skipped': skipped, 'waiting_for_go': waiting})
    usage = load('airtable_calls.json', {})
    month = today()[:7]
    usage[month] = usage.get(month, 0) + c1 + c2
    save('airtable_calls.json', usage)
    commit(f'sync {today()}: {len(queue)} queued, {ticked} ticked')
    print(f'PUBLIC: ticked {ticked}, queued {len(queue)}, waiting {len(waiting)}, skipped {len(skipped)}, replied (no DM) {n_replied}')
    print(f'ticked {ticked}, queued {len(queue)}, waiting for go {len(waiting)}, skipped {len(skipped)}, airtable calls this month by sender {usage[month]}')
    cap = daily_cap()
    nco = len({i['id'] for i in queue})
    if cap and nco < cap * CFG['low_supply_days']:
        email(f'DM sender: only {nco} coaches left ({nco // max(cap, 1)} days)',
              f'The queue has {nco} approved coaches not yet messaged ({len(queue)} DMs). At {cap} coaches a day that is under {CFG["low_supply_days"]} days.\n'
              f'Approve more coaches so the sender does not run dry.\n\nRun: {run_link()}')
    if skipped:
        email(f'DM sender: {len(skipped)} DMs skipped at sync',
              'These approved DMs cannot be sent as written:\n' + '\n'.join(skipped) + f'\n\nRun: {run_link()}')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        email('DM sender ERROR: daily sync failed', f'{type(e).__name__}: {e}\n\nNothing was sent from this step. Run: {run_link()}')
        raise
