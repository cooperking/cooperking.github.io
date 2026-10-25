"""Puts _sotd/worker/worker.js in place as the Worker sotd-playlists, keeping its bindings and secrets
(Cloudflare's "Replace Worker Script Content": the code only, config untouched). Run by .github/workflows/worker.yml.

Needs the repository secrets CLOUDFLARE_API_TOKEN (a token with Account > Workers Scripts > Edit) and
CLOUDFLARE_ACCOUNT_ID. Without the token it does nothing.

1. Checks the code parses (node --check).
2. Notes the version running now and lists the Worker's bindings (names and types only, never values).
3. Uploads the code.
4. Checks the Worker: /status still answers (the old part), /covers.json answers JSON (the members part).
   If /status fails, the version from step 2 is put back and the job fails.
"""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request, uuid

API = os.environ.get('CF_API', 'https://api.cloudflare.com/client/v4')   # (another address only for testing)
NAME = 'sotd-playlists'
LIVE = os.environ.get('CF_LIVE', 'https://sotd-playlists.cooperking.workers.dev')
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'worker', 'worker.js')
TOKEN = os.environ.get('CF_TOKEN', '').strip()
ACCOUNT = os.environ.get('CF_ACCOUNT', '').strip()


class Refused(Exception):
    pass


def cf(method, path, body=None, ctype='application/json'):
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': ctype})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: out = json.loads(e.read())
        except Exception: out = {'success': False, 'errors': [{'message': 'HTTP %d' % e.code}]}
    if not out.get('success'):
        raise Refused('Cloudflare said no to %s %s: %s' % (method, path, '; '.join(
            '%s (%s)' % (x.get('message'), x.get('code')) for x in out.get('errors') or [])))
    return out.get('result')


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'sotd-deploy-check'}), timeout=30) as r:
            return r.status, r.headers.get('Content-Type', ''), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('Content-Type', ''), e.read()
    except Exception as e:
        return 0, '', str(e).encode()


def main():
    if not TOKEN:
        print('No CLOUDFLARE_API_TOKEN secret yet: nothing done.')
        return
    global ACCOUNT
    if not ACCOUNT:
        try:
            accts = cf('GET', '/accounts') or []
        except Refused:
            accts = []
        if len(accts) != 1:
            raise SystemExit('Add the repository secret CLOUDFLARE_ACCOUNT_ID (the token sees %d accounts).' % len(accts))
        ACCOUNT = accts[0]['id']
    base = '/accounts/%s/workers/scripts/%s' % (ACCOUNT, NAME)

    # 1. the code parses
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(SRC, os.path.join(d, 'worker.mjs'))
        chk = subprocess.run(['node', '--check', os.path.join(d, 'worker.mjs')], capture_output=True, text=True)
        if chk.returncode:
            raise SystemExit('worker.js does not parse; nothing uploaded.\n' + chk.stderr)
    print('Code parses.')

    # 2. what's running now, and its bindings
    before = []
    try:
        deps = (cf('GET', base + '/deployments') or {}).get('deployments') or []
        if deps:
            latest = max(deps, key=lambda d: d.get('created_on') or '')
            before = [{'version_id': v['version_id'], 'percentage': v.get('percentage', 100)} for v in latest.get('versions') or []]
    except Refused as e:
        print('(No list of versions, so no automatic undo:', e, ')')
    print('Running now:', ', '.join('%s (%s%%)' % (v['version_id'], v['percentage']) for v in before) or 'unknown')
    try:
        settings = cf('GET', base + '/settings') or {}
        binds = {b.get('name'): b.get('type') for b in settings.get('bindings') or []}
        print('Bindings:', ', '.join('%s (%s)' % kv for kv in sorted(binds.items())) or 'none')
        for need, what in (('SOTD', 'KV'), ('GH_TOKEN', 'secret'), ('ADMIN_KEY', 'secret')):
            if need not in binds:
                print('WARNING: no %s %s binding; parts of the Worker that use it will fail.' % (what, need))
        if 'MEDIA' not in binds:
            print('WARNING: no MEDIA (R2) binding yet: the members part will answer but can\'t keep or give files. '
                  'Cloudflare: Workers & Pages > sotd-playlists > Settings > Bindings > Add > R2 bucket, MEDIA, sotd-members.')
    except Refused as e:
        print('(Couldn\'t read the bindings:', e, ')')

    # 3. the code
    code = open(SRC, 'rb').read()
    bnd = uuid.uuid4().hex
    body = b''.join([
        b'--' + bnd.encode() + b'\r\nContent-Disposition: form-data; name="metadata"\r\nContent-Type: application/json\r\n\r\n',
        json.dumps({'main_module': 'worker.js'}).encode(), b'\r\n',
        b'--' + bnd.encode() + b'\r\nContent-Disposition: form-data; name="worker.js"; filename="worker.js"\r\n'
        b'Content-Type: application/javascript+module\r\n\r\n', code, b'\r\n',
        b'--' + bnd.encode() + b'--\r\n'])
    cf('PUT', base + '/content', body, 'multipart/form-data; boundary=' + bnd)
    print('Uploaded worker.js (%d bytes).' % len(code))

    # 4. the Worker answers
    ok_status = ok_new = False
    for _ in range(12):
        time.sleep(5)
        s, _t, _b = get(LIVE + '/status')
        n, t, _b = get(LIVE + '/covers.json?check=' + str(int(time.time())))
        ok_status, ok_new = s == 200, n == 200 and 'json' in t
        if ok_status and ok_new:
            break
    print('/status:', 'answers' if ok_status else 'FAILS', '| /covers.json (members part):', 'answers' if ok_new else 'not yet')
    if not ok_status:
        if before:
            cf('POST', base + '/deployments', {'strategy': 'percentage', 'versions': before})
            print('Put the previous version back.')
        raise SystemExit('The Worker stopped answering after the upload.')
    if not ok_new:
        raise SystemExit('Uploaded, but the new code isn\'t answering yet (/covers.json).')
    print('Done: the Worker is running this code.')


if __name__ == '__main__':
    try:
        main()
    except Refused as e:
        raise SystemExit(str(e))
