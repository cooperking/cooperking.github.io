#!/usr/bin/env python3
"""A lighter copy of each of John's own songs (the members' MP3s), for streaming and for keeping offline.

    python3 _sotd/audio_light.py              every song that needs one and hasn't got one (or has an old one)
    python3 _sotd/audio_light.py TRACK        just this one (the Worker starts this after each MP3 upload)

The uploads are VBR MP3s at about 255 kbps (48 kHz, stereo; _sotd/data/audio-report.json). Each one gets a copy at
l/<track>.m4a in the same R2 bucket: AAC-LC at 192 kbps, the same sample rate, stereo, index first (faststart). AAC at
192 kbps is heard as the same as the MP3 by practically everyone, and plays on everything the calendar runs on
(browsers, iPhones, Android, the Fire TV, Alexa); it's about a quarter smaller, so a song kept offline takes about a
quarter less room. The Worker plays the copy wherever there is one (/m/a/...), and drops it when a new MP3 is uploaded
(this then makes a new one). This never changes or deletes an MP3; media_originals.py (by hand) deletes the MP3s whose
copy has been made, after which the copy is the song.

Each copy is checked before it goes up: its length within a fifth of a second of the MP3's (the same place in the song
stays the same place), and smaller than the MP3 (a song already below 192 kbps keeps its MP3 only).

Reaches R2 through its S3 API with the same secrets as video_tv.py (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET,
CLOUDFLARE_ACCOUNT_ID). Run by .github/workflows/audio-light.yml.
"""
import json, os, subprocess, sys, tempfile, time

KEY_ID = os.environ.get('R2_ACCESS_KEY_ID', '').strip()
SECRET = os.environ.get('R2_SECRET_ACCESS_KEY', '').strip()
BUCKET = os.environ.get('R2_BUCKET', '').strip()
ACCOUNT = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '').strip()
BUDGET = float(os.environ.get('BUDGET_MINUTES', '50')) * 60
KBPS = 192


def length(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', path],
                         capture_output=True, text=True, timeout=120).stdout or '{}'
    return float((json.loads(out).get('format') or {}).get('duration') or 0)


def make(src, dst):
    """Encodes src (an MP3) to dst (AAC in .m4a); returns (ok, why)."""
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', src, '-map', '0:a:0', '-c:a', 'aac', '-b:a', '%dk' % KBPS, '-ac', '2',
                    '-map_metadata', '-1', '-movflags', '+faststart', dst], check=True, timeout=900)
    a, b = length(src), length(dst)
    if not a or abs(a - b) > 0.2:
        return False, 'length %.2f s against the MP3\'s %.2f s' % (b, a)
    if os.path.getsize(dst) >= os.path.getsize(src):
        return False, 'not smaller than the MP3'
    return True, ''


def main(args):
    if not (KEY_ID and SECRET and BUCKET and ACCOUNT):
        print('Not set up: the repository secrets R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET and CLOUDFLARE_ACCOUNT_ID.')
        return
    import boto3
    from botocore.config import Config
    s3 = boto3.client('s3', endpoint_url=os.environ.get('R2_ENDPOINT') or 'https://%s.r2.cloudflarestorage.com' % ACCOUNT, aws_access_key_id=KEY_ID,
                      aws_secret_access_key=SECRET, region_name='auto', config=Config(signature_version='s3v4', retries={'max_attempts': 5}))

    def listing(prefix):
        out, token = {}, None
        while True:
            kw = {'Bucket': BUCKET, 'Prefix': prefix}
            if token:
                kw['ContinuationToken'] = token
            r = s3.list_objects_v2(**kw)
            for o in r.get('Contents', []):
                out[o['Key']] = o
            if not r.get('IsTruncated'):
                return out
            token = r['NextContinuationToken']

    originals, copies = listing('a/'), listing('l/')
    tracks = [k[2:24] for k in sorted(originals) if k.endswith('.mp3')]
    # (a copy whose MP3 has gone stays: media_originals.py deletes the MP3s that have a copy, and the copy is then the song;
    # taking a song away on the Songs tab takes its copy too, in the Worker)
    if args:
        tracks = [t for t in tracks if t in args]
    start, made, kept, failed, before, after = time.time(), 0, [], [], 0, 0
    for t in tracks:
        o, c = originals['a/%s.mp3' % t], copies.get('l/%s.m4a' % t)
        if c and c['LastModified'] >= o['LastModified']:
            continue   # an up-to-date copy
        if time.time() - start > BUDGET:
            print('Out of time for this run; the rest go next time.')
            break
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, 'in.mp3'), os.path.join(d, 'out.m4a')
            try:
                s3.download_file(BUCKET, o['Key'], src)
                ok, why = make(src, dst)
                if not ok:
                    kept.append((t, why))
                    if c:
                        s3.delete_object(Bucket=BUCKET, Key=c['Key'])
                    print('kept the MP3 only: %s (%s)' % (t, why), flush=True)
                    continue
                s3.upload_file(dst, BUCKET, 'l/%s.m4a' % t, ExtraArgs={'ContentType': 'audio/mp4'})
                made += 1; before += os.path.getsize(src); after += os.path.getsize(dst)
                print('made %s: %.1f MB -> %.1f MB' % (t, os.path.getsize(src) / 1e6, os.path.getsize(dst) / 1e6), flush=True)
            except Exception as e:
                failed.append((t, str(e)[:200]))
    note = lambda m: print(m + ('\n::notice title=Songs, lighter::' + m if os.environ.get('GITHUB_ACTIONS') else ''))   # (also on the run's page)
    if made:
        note('%d copies made: %.0f MB of MP3s -> %.0f MB (%.0f%% smaller)' % (made, before / 1e6, after / 1e6, 100 - after / before * 100))
    note('%d made, %d kept as MP3 only, %d failed' % (made, len(kept), len(failed)))
    for t, e in kept:
        note('kept as MP3 only: %s (%s)' % (t, e))
    for t, e in failed:
        note('failed: %s %s' % (t, e))
    if failed and not made:
        sys.exit(1)


if __name__ == '__main__':
    main(sys.argv[1:])
