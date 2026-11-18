"""Bundle the run log and screenshots into private.enc (Fernet, STATE_KEY) so the public repo's
artifacts and logs show nothing. Unpack on the Mac: python3 dmsender/pack.py unpack <private.enc>"""
import io, os, sys, tarfile
from common import ROOT, state_key


def pack():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as t:
        for p in ('run.log', 'shots'):
            full = os.path.join(ROOT, p)
            if os.path.exists(full):
                t.add(full, arcname=p)
    with open(os.path.join(ROOT, 'private.enc'), 'wb') as f:
        f.write(state_key().encrypt(buf.getvalue()))


def unpack(path):
    out = os.path.join(ROOT, 'unpacked')
    data = state_key().decrypt(open(path, 'rb').read())
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as t:
        try:
            t.extractall(out, filter='data')
        except TypeError:  # Python < 3.12 on the Mac
            t.extractall(out)
    print('unpacked to', out)


if __name__ == '__main__':
    unpack(sys.argv[2]) if sys.argv[1:2] == ['unpack'] else pack()
