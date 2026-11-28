"""Monday reply table (28 Nov 2026, owner's go): replies counted by wording, so one change at a time can be judged.

Emails: the writer routine stores a wording code in `Version` (s1 c3 p2 = style 1, closing 3, problem line 2; s2 p1 =
style 2, problem line 1). Rows written before 28 Nov have no code and count as "before 28 Nov".
Replies come from Lumina (GET /api/campaigns/replies, read only). Also counts the coaches who said yes, so the
"free for five coaches" line is never sent after five have taken it.
About 6 to 10 Airtable reads a week. PUBLIC REPO: the table goes to the owner by email only."""
import collections, datetime as dt, json, os, sys, urllib.request
from common import CFG, _at, email, now

START = '2026-11-28T00:00:00Z'


def lumina_replies():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from tick import lumina
    s, d = lumina('GET', f'/api/campaigns/replies?since={START}&limit=200')
    if s != 200:
        raise RuntimeError(f'Lumina replies read failed ({s})')
    return d.get('replies', [])


def rows(table_name, formula, fields):
    a = CFG['airtable']
    tables = {t['name']: t['id'] for t in _at('GET', f"meta/bases/{a['base']}/tables")['tables']}
    if table_name not in tables:
        return []
    out, offset = [], None
    while True:
        q = {'pageSize': 100, 'filterByFormula': formula, 'fields[]': fields}
        if offset:
            q['offset'] = offset
        j = _at('GET', f"{a['base']}/{tables[table_name]}", query=q)
        out += [r['fields'] for r in j['records']]
        offset = j.get('offset')
        if not offset:
            return out


def build():
    sent_by, ver_of = collections.Counter(), {}
    for t in ('Sent', 'Email and Socials', 'Email Only'):
        for f in rows(t, "AND({Email}!='', {Version}!='')", ['Email', 'Version']):
            v = f['Version'].strip()
            em = f['Email'].strip().lower()
            if em not in ver_of:
                ver_of[em] = v
                sent_by[v] += 1
    rep = lumina_replies()
    rep_by = collections.Counter(ver_of.get((r.get('email') or '').lower(), 'before 28 Nov') for r in rep)
    yes = rows('Replied Yes', "{Business}!=''", ['Business'])
    lines = ['Replies by wording since 28 Nov 2026 (emails written, replies, reply rate):', '']
    for v in sorted(set(sent_by) | set(rep_by)):
        n, r = sent_by.get(v, 0), rep_by.get(v, 0)
        rate = f'{100 * r / n:.1f}%' if n else '-'
        lines.append(f'{v:<14} written {n:>4}   replies {r:>3}   {rate}')
    lines += ['', 'Style s1 = no sample post, s2 = sample post. c = closing number, p = problem line number.',
              'Emails written count when the writer saved them; some may still be waiting on your page.', '',
              f'Coaches who said yes so far: {len(yes)} of the five free spots.']
    if len(yes) >= 4:
        lines.append('The free spots are nearly gone. The emails and DMs still say free for five coaches: tell me what to change.')
    return lines, len(yes)


def run():
    lines, yes = build()
    email(f'Weekly replies by wording ({now().date().isoformat()})', '\n'.join(lines))
    print('PUBLIC: weekly table sent, yes count', yes)


if __name__ == '__main__':
    print('\n'.join(build()[0]))
