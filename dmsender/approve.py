"""Run by Claude on the owner's launch go, never before. Adds every DM now in Airtable (Cold DM filled, not sent,
not Do not contact) to state/approved.json, so the sender may send it. DMs not in that file are never sent.
  python3 dmsender/approve.py          lists what would be approved (no change)
  python3 dmsender/approve.py --yes    approves them, commits and pushes"""
import json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load, save, airtable_rows, ROOT

rows, calls = airtable_rows(['Business', 'Cold DM', 'Approve DM', 'Do not contact'])
approved = set(load('approved.json', []))
sent = load('sent.json', {})
new = [r for r in rows if (r['f'].get('Cold DM') or '').strip() and not r['f'].get('Approve DM')
       and not r['f'].get('Do not contact') and r['id'] not in sent and r['id'] not in approved]
for r in new:
    print('-', r['f'].get('Business'))
print(f'{len(new)} new DMs; {len(approved)} already approved; {calls} Airtable calls')
if '--yes' in sys.argv and new:
    save('approved.json', sorted(approved | {r['id'] for r in new}))
    subprocess.run(['git', 'add', 'state/approved.json'], cwd=ROOT, check=True)
    subprocess.run(['git', 'commit', '-q', '-m', f'approve {len(new)} DMs (the owner launch go)'], cwd=ROOT, check=True)
    subprocess.run(['git', 'pull', '-q', '--rebase'], cwd=ROOT, check=True)
    subprocess.run(['git', 'push', '-q'], cwd=ROOT, check=True)
    print('approved and pushed')
