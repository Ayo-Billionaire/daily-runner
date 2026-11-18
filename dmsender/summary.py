"""End of day: tick today's sends in Airtable, email the summary, and raise an alarm if nothing ran."""
from common import CFG, load, save, commit, email, run_link, daily_cap, is_send_day, today
from common import airtable_rows, airtable_tick
from sync import ticks_due, FIELDS


def main():
    sent = load('sent.json', {})
    q = load('queue.json', {'items': []})
    pause = load('pause.json', {})
    rows, c1 = airtable_rows(FIELDS)
    due = ticks_due(rows, sent)
    ok_ids, c2 = airtable_tick(due) if due else (set(), 0)
    ticked = len(ok_ids)
    usage = load('airtable_calls.json', {})
    usage[today()[:7]] = usage.get(today()[:7], 0) + c1 + c2
    save('airtable_calls.json', usage)
    commit(f'summary {today()}: {ticked} ticked')
    t = [v for v in sent.values() if v.get('date') == today()]
    ok = [v for v in t if v['status'] == 'sent']
    sk = [v for v in t if v['status'] == 'skipped']
    left = len([i for i in q['items'] if i['key'] not in sent])
    cap = daily_cap()
    coaches = len({v.get('rid') for v in ok})
    lines = [f"Coaches messaged today: {coaches} of {cap} ({len(ok)} messages)", f"Skipped: {len(sk)}", f"Left in queue: {left} messages", ""]
    lines += [f"  {v['platform']:9} {v['business']}" for v in ok]
    if sk:
        lines += ["", "Skipped:"] + [f"  {v['business']}: {v.get('reason')}" for v in sk]
    if pause.get('until', '') >= today():
        lines += ["", f"STOPPED today: {pause.get('reason')}"]
    lines += ["", f"Approve DM ticked in Airtable: {ticked}", f"Run: {run_link()}"]
    alarm = is_send_day() and cap and not ok and left and pause.get('until', '') < today()
    subject = (f"DM sender ALERT: 0 DMs went out today" if alarm else
               f"DM sender: {coaches}/{cap} coaches messaged today" + (" (stopped)" if pause.get('until', '') >= today() else ""))
    if is_send_day() and cap:
        email(subject, '\n'.join(lines))
    print('\n'.join(lines))
    print(f'PUBLIC: summary done, {ticked} ticked')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        email('DM sender ERROR: daily summary failed', f'{type(e).__name__}: {e}\n\nRun: {run_link()}')
        raise
