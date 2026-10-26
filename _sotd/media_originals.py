#!/usr/bin/env python3
"""Deletes the originals of John's own songs and videos that are no longer played, to save room in the R2 bucket.

    python3 _sotd/media_originals.py             says what it would delete; nothing is deleted
    python3 _sotd/media_originals.py --delete    deletes them

An original is no longer played once it has a copy that the Worker plays in its place (/m/a/... and /m/v/... play the
copy wherever there is one): a/<track>.mp3 with its lighter copy l/<track>.m4a (audio_light.py), and v/<track>.mp4 with
its TV copy h/<track>.mp4 (video_tv.py). Each original goes only if its copy
  - is newer than it (made from this original, not from one uploaded before it),
  - opens (ffprobe) as the right kind: AAC for a song; H.264 for a video, with sound if the original has sound,
  - is as long as it: within 0.2 s for a song, 0.5 s for a video;
and only if the original is still the one listed at the start (one uploaded meanwhile is kept). An original without a
copy (a song already under 192 kbps, a video that plays fine as it is) is kept.

The Worker's catalog of which songs have which files (KV mediaCatalog) isn't changed: it keeps each original's size and
fingerprint, so the calendar plays the copy, the Songs tab shows the song as there, and a folder upload still knows the
file is already up. Uploading a song or video again puts a new original up and makes a new copy; this can then be run
again. A deleted original can't be got back from the bucket: its copy is then the only version there.

Reaches R2 through its S3 API with the same secrets as video_tv.py (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET,
CLOUDFLARE_ACCOUNT_ID). Run by .github/workflows/media-originals.yml, by hand only.
"""
import json, os, subprocess, sys

KEY_ID = os.environ.get('R2_ACCESS_KEY_ID', '').strip()
SECRET = os.environ.get('R2_SECRET_ACCESS_KEY', '').strip()
BUCKET = os.environ.get('R2_BUCKET', '').strip()
ACCOUNT = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '').strip()
ENDPOINT = os.environ.get('R2_ENDPOINT', '').strip() or 'https://%s.r2.cloudflarestorage.com' % ACCOUNT
KINDS = (   # original prefix, its extension, copy prefix, its extension, what it is, how close the lengths must be (s)
    ('a', '.mp3', 'l', '.m4a', 'songs', 0.2),
    ('v', '.mp4', 'h', '.mp4', 'videos', 0.5),
)


def note(m):
    print(m, flush=True)
    if os.environ.get('GITHUB_ACTIONS'):
        print('::notice title=Originals::' + m.replace('%', '%25').replace('\r', '').replace('\n', '%0A'), flush=True)


def probe(url):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', url],
                         capture_output=True, text=True, timeout=120).stdout or '{}'
    info = json.loads(out)
    streams = info.get('streams') or []
    first = lambda kind: next((s for s in streams if s.get('codec_type') == kind and not (s.get('disposition') or {}).get('attached_pic')), None)
    return {'dur': float((info.get('format') or {}).get('duration') or 0), 'audio': first('audio'), 'video': first('video')}


def why_not(kind, orig, copy, close):
    """'' if the copy can stand in for the original, else why not."""
    if not orig['dur']:
        return 'the original has no length'
    if abs(copy['dur'] - orig['dur']) > close:
        return 'the copy is %.2f s long against %.2f s' % (copy['dur'], orig['dur'])
    if kind == 'a':
        if not copy['audio'] or copy['audio'].get('codec_name') != 'aac':
            return 'the copy isn\'t AAC'
    else:
        if not copy['video'] or copy['video'].get('codec_name') != 'h264':
            return 'the copy isn\'t H.264'
        if orig['audio'] and not copy['audio']:
            return 'the copy has no sound'
    return ''


def main(args):
    delete = '--delete' in args
    if not (KEY_ID and SECRET and BUCKET and ACCOUNT):
        sys.exit('Not set up: the repository secrets R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET and CLOUDFLARE_ACCOUNT_ID.')
    import boto3
    from botocore.config import Config
    s3 = boto3.client('s3', endpoint_url=ENDPOINT, aws_access_key_id=KEY_ID, aws_secret_access_key=SECRET, region_name='auto',
                      config=Config(signature_version='s3v4', retries={'max_attempts': 5}))
    everything, token = {}, None
    while True:
        kw = {'Bucket': BUCKET}
        if token:
            kw['ContinuationToken'] = token
        r = s3.list_objects_v2(**kw)
        for o in r.get('Contents', []):
            everything[o['Key']] = o
        if not r.get('IsTruncated'):
            break
        token = r['NextContinuationToken']
    total = sum(o['Size'] for o in everything.values())
    link = lambda k: s3.generate_presigned_url('get_object', Params={'Bucket': BUCKET, 'Key': k}, ExpiresIn=900)
    gone_bytes, failed = 0, []
    for ok_, oext, ck, cext, what, close in KINDS:
        go, kept, uncopied = [], [], 0
        for key in sorted(k for k in everything if k.startswith(ok_ + '/') and k.endswith(oext)):
            t = key[2:-len(oext)]
            o, c = everything[key], everything.get('%s/%s%s' % (ck, t, cext))
            if not c:
                uncopied += 1
                continue
            if c['LastModified'] < o['LastModified']:
                kept.append((t, 'its copy is older than it'))
                continue
            try:
                why = why_not(ok_, probe(link(key)), probe(link(c['Key'])), close)
            except Exception as e:
                why = 'couldn\'t be read: %s' % str(e)[:80]
            if why:
                kept.append((t, why))
            else:
                go.append(o)
        size = sum(o['Size'] for o in go)
        if delete:
            done = 0
            for o in go:
                try:
                    now = s3.head_object(Bucket=BUCKET, Key=o['Key'])
                    if now['ETag'] != o['ETag'] or now['ContentLength'] != o['Size']:
                        kept.append((o['Key'][2:-len(oext)], 'uploaded again meanwhile')); size -= o['Size']; continue
                    cn = '%s/%s%s' % (ck, o['Key'][2:-len(oext)], cext)
                    s3.head_object(Bucket=BUCKET, Key=cn)   # its copy still there
                    s3.delete_object(Bucket=BUCKET, Key=o['Key'])
                    done += 1
                except Exception as e:
                    failed.append((o['Key'], str(e)[:120])); size -= o['Size']
            gone_bytes += size
            note('%s: %d originals deleted (%.0f MB); %d kept, as nothing plays in their place; %d kept for the reason below'
                 % (what.capitalize(), done, size / 1e6, uncopied, len(kept)))
        else:
            gone_bytes += size
            note('%s: %d originals would be deleted (%.0f MB); %d kept, as nothing plays in their place; %d kept for the reason below'
                 % (what.capitalize(), len(go), size / 1e6, uncopied, len(kept)))
        if kept:
            note('%s kept: %s' % (what.capitalize(), '; '.join('%s (%s)' % k for k in kept)[:3000]))
    note('The bucket: %.2f GB, %s %.2f GB (R2 includes 10 GB a month)'
         % (total / 1e9, 'now' if delete else 'after deleting them', (total - gone_bytes) / 1e9))
    if failed:
        note('Couldn\'t delete: %s' % '; '.join('%s (%s)' % f for f in failed)[:3000])
        sys.exit(1)
    if not delete:
        note('Nothing was deleted. To delete them: run this workflow again with delete typed in the box.')


if __name__ == '__main__':
    main(sys.argv[1:])
