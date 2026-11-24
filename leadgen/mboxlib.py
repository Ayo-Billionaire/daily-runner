"""Mailbox helpers (mboxlib.py, named so it never shadows the standard library mailbox) (24 Nov 2026): the four noqodepro mailboxes on Hostinger, one shared password.

Drafts go into INBOX.Drafts and are never sent by code. Ayo opens each mailbox, reads the draft and sends it himself.
The Sent folder is the record of what really went out, so follow-up timing is read from it.

PUBLIC REPO: addresses come from PRIVATE_CONFIG (key `mailboxes`), the password from MB_AYODESH. Nothing here prints an address or text.
"""
import imaplib, os, re, time, email, email.utils, datetime as dt
from email.message import EmailMessage
from email.header import decode_header, make_header

HOST, PORT = 'imap.hostinger.com', 993
SENT, DRAFTS, INBOX = 'INBOX.Sent', 'INBOX.Drafts', 'INBOX'


def password():
    pw = os.environ.get('MB_AYODESH', '').strip()
    if pw:
        return pw
    f = os.path.expanduser('~/.secrets/api-keys.env')
    if os.path.exists(f):
        m = re.search(r'^MB_AYODESH=(.+)$', open(f).read(), re.M)
        if m:
            return m.group(1).strip().strip('"\'')
    raise RuntimeError('no MB_AYODESH')


def connect(user):
    M = imaplib.IMAP4_SSL(HOST, PORT)
    M.login(user, password())
    return M


def _h(v):
    try:
        return str(make_header(decode_header(v or '')))
    except Exception:
        return v or ''


def addr(v):
    return (email.utils.parseaddr(v or '')[1] or '').lower()


def headers(M, folder, since_days=60, fields='TO CC DATE MESSAGE-ID SUBJECT FROM IN-REPLY-TO'):
    """[(uid, {to, cc, date, message-id, subject, from})] for messages in the last since_days."""
    t, d = M.select('"%s"' % folder, readonly=True)
    if t != 'OK':
        return []
    since = (dt.date.today() - dt.timedelta(days=since_days)).strftime('%d-%b-%Y')
    t, ids = M.uid('SEARCH', None, 'SINCE', since)
    out = []
    uids = ids[0].split() if t == 'OK' and ids and ids[0] else []
    for i in range(0, len(uids), 100):
        chunk = b','.join(uids[i:i + 100])
        t, data = M.uid('FETCH', chunk, '(UID BODY.PEEK[HEADER.FIELDS (%s)])' % fields)
        if t != 'OK':
            continue
        for part in data:
            if not isinstance(part, tuple):
                continue
            m = re.search(rb'UID (\d+)', part[0])
            msg = email.message_from_bytes(part[1])
            try:
                when = email.utils.parsedate_to_datetime(msg.get('Date'))
            except Exception:
                when = None
            out.append((m.group(1).decode() if m else '', {
                'to': [a for a in re.findall(r'[\w.+\-]+@[\w.\-]+\.\w+', (msg.get('To') or '') + ' ' + (msg.get('Cc') or '').lower())],
                'date': when, 'mid': (msg.get('Message-ID') or '').strip(), 'subject': _h(msg.get('Subject')),
                'from': addr(msg.get('From')), 'irt': (msg.get('In-Reply-To') or '').strip()}))
    return out


def sent_to(M, since_days=60):
    """{lead address: [(date, message-id, subject)] oldest first} for everything in the Sent folder."""
    res = {}
    for _uid, h in headers(M, SENT, since_days):
        for a in {x.lower() for x in h['to']}:
            res.setdefault(a, []).append((h['date'], h['mid'], h['subject']))
    for a in res:
        res[a].sort(key=lambda x: x[0] or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    return res


def draft_targets(M):
    """Addresses that already have a draft waiting, so none is written twice."""
    out = set()
    for _uid, h in headers(M, DRAFTS, 365):
        out |= {x.lower() for x in h['to']}
    return out


def drafts_count(M):
    t, d = M.select('"%s"' % DRAFTS, readonly=True)
    return int(d[0]) if t == 'OK' else 0


def heard_from(M, since_days=45):
    """Everyone who wrote to this mailbox lately: real replies, auto replies and bounce notices all count as 'do not chase'."""
    seen = set()
    for _uid, h in headers(M, INBOX, since_days, fields='FROM DATE SUBJECT IN-REPLY-TO'):
        if h['from']:
            seen.add(h['from'])
    return seen


def bounced(M, since_days=45):
    """Addresses named in delivery failure notices (the lead's address is in the body of the notice)."""
    t, d = M.select('"%s"' % INBOX, readonly=True)
    out = set()
    if t != 'OK':
        return out
    since = (dt.date.today() - dt.timedelta(days=since_days)).strftime('%d-%b-%Y')
    for who in ('mailer-daemon', 'postmaster'):
        t, ids = M.uid('SEARCH', None, 'SINCE', since, 'FROM', who)
        for u in (ids[0].split() if t == 'OK' and ids and ids[0] else [])[:80]:
            t, data = M.uid('FETCH', u, '(BODY.PEEK[TEXT]<0.3000>)')
            if t == 'OK' and data and isinstance(data[0], tuple):
                out |= {x.lower() for x in re.findall(r'[\w.+\-]+@[\w.\-]+\.\w+', data[0][1].decode('latin1'))}
    return out


def append_draft(M, sender, to, subject, body, in_reply_to='', references=''):
    m = EmailMessage()
    m['From'] = sender
    m['To'] = to
    m['Subject'] = subject
    m['Date'] = email.utils.formatdate(localtime=True)
    m['Message-ID'] = email.utils.make_msgid(domain='noqodepro.com')
    if in_reply_to:
        m['In-Reply-To'] = in_reply_to
        m['References'] = (references + ' ' + in_reply_to).strip()
    m.set_content(body)
    t, d = M.append(DRAFTS, '\\Draft', imaplib.Time2Internaldate(time.time()), m.as_bytes())
    return t == 'OK'
