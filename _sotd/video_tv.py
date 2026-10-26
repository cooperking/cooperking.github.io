#!/usr/bin/env python3
"""A TV-friendly copy of each of John's own videos (the members' videos), for Fire TV Sticks, older iPhones and Androids.

    python3 _sotd/video_tv.py                  every video that needs one and hasn't got one (or has an old one)
    python3 _sotd/video_tv.py TRACK            just this one (the Worker starts this after each video upload)
    python3 _sotd/video_tv.py --shard 1/4      a quarter of them (the job runs four at once)

Many uploads are AV1 or VP9 (YouTube's formats) and some are 4K: light files, but too hard for a Fire TV Stick to decode,
so they glitch there. Each one that isn't already plain H.264 (1080p or less, 8 Mbps or less, 8-bit) gets a copy at
h/<track>.mp4 in the same R2 bucket: H.264 High, 1080p at most, about 4 to 5 Mbps (CRF 21, at most 6 Mbps), AAC 160k,
index first (faststart). Each copy is checked before it goes up: H.264, with sound if the original has sound, and as long
as it within half a second; one that isn't stays off and the original plays. The Worker plays the copy wherever there is one (/m/v/...), and drops it when a new video is
uploaded (this then makes a new one). This never changes or deletes an original; media_originals.py (by hand) deletes the
originals whose copy has been made, after which the copy is the video (the folder upload still knows it by the catalog).

Reaches R2 through its S3 API: the repository secrets R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY (an R2 API token with
Object Read & Write on the bucket), R2_BUCKET and CLOUDFLARE_ACCOUNT_ID. Without them it says so and does nothing.
"""
import json, os, subprocess, sys, tempfile, time, zlib

KEY_ID = os.environ.get('R2_ACCESS_KEY_ID', '').strip()
SECRET = os.environ.get('R2_SECRET_ACCESS_KEY', '').strip()
BUCKET = os.environ.get('R2_BUCKET', '').strip()
ACCOUNT = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '').strip()
BUDGET = float(os.environ.get('BUDGET_MINUTES', '330')) * 60   # stop starting new ones after this (the job's limit is 6 hours)


def needs_copy(info):
    """Why this video needs a TV copy ('' if it plays fine as it is)."""
    v = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not v:
        return ''
    why = []
    if v.get('codec_name') != 'h264':
        why.append(v.get('codec_name') or 'unknown codec')
    if int(v.get('height') or 0) > 1080 or int(v.get('width') or 0) > 1920:
        why.append('%sx%s' % (v.get('width'), v.get('height')))
    if (v.get('pix_fmt') or 'yuv420p') not in ('yuv420p', 'yuvj420p'):
        why.append(v.get('pix_fmt'))
    br = int((info.get('format') or {}).get('bit_rate') or 0)
    if br > 8_000_000:
        why.append('%d Mbps' % round(br / 1e6))
    return ', '.join(why)


def main(args):
    if not (KEY_ID and SECRET and BUCKET and ACCOUNT):
        print('Not set up: add the repository secrets R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY and R2_BUCKET (see _sotd/MEMBERS.md).')
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

    shard = None
    if '--shard' in args:
        i = args.index('--shard'); a, b = args[i + 1].split('/'); shard = (int(a), int(b)); args = args[:i] + args[i + 2:]
    originals, copies = listing('v/'), listing('h/')
    tracks = [k[2:24] for k in sorted(originals)]
    if args:
        tracks = [t for t in tracks if t in args]
    if shard:
        tracks = [t for t in tracks if zlib.crc32(t.encode()) % shard[1] == shard[0] - 1]
    start, made, fine, failed = time.time(), [], 0, []
    for t in tracks:
        o, h = originals['v/%s.mp4' % t], copies.get('h/%s.mp4' % t)
        if h and h['LastModified'] >= o['LastModified']:
            continue   # it has an up-to-date copy
        if time.time() - start > BUDGET:
            print('Out of time for this run; the rest go next time.')
            break
        url = s3.generate_presigned_url('get_object', Params={'Bucket': BUCKET, 'Key': o['Key']}, ExpiresIn=3 * 3600)
        try:
            info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', url],
                                             capture_output=True, text=True, timeout=120).stdout or '{}')
        except Exception as e:
            failed.append((t, 'ffprobe: %s' % e)); continue
        why = needs_copy(info)
        if not why:
            fine += 1
            if h:   # an old copy of a video that no longer needs one
                s3.delete_object(Bucket=BUCKET, Key=h['Key'])
            continue
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, 'in.mp4'), os.path.join(d, 'out.mp4')
            try:
                s3.download_file(BUCKET, o['Key'], src)
                cmd = ['ffmpeg', '-v', 'error', '-y', '-i', src, '-map', '0:v:0', '-map', '0:a:0?',
                       '-vf', "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p",
                       '-c:v', 'libx264', '-preset', 'medium', '-profile:v', 'high', '-level:v', '4.1', '-crf', '21',
                       '-maxrate', '6M', '-bufsize', '12M', '-r', str(eval_fps(info)),
                       '-c:a', 'aac', '-b:a', '160k', '-ac', '2', '-movflags', '+faststart', dst]
                subprocess.run(cmd, check=True, timeout=3600)
                bad = copy_wrong(info, dst)
                if bad:   # not put up: the original keeps playing (and an old copy, made from an earlier upload, goes)
                    if h:
                        s3.delete_object(Bucket=BUCKET, Key=h['Key'])
                    failed.append((t, 'the copy came out wrong (%s), so the original plays' % bad))
                    continue
                s3.upload_file(dst, BUCKET, 'h/%s.mp4' % t, ExtraArgs={'ContentType': 'video/mp4'})
                made.append((t, why, os.path.getsize(src), os.path.getsize(dst)))
                print('made %s (%s): %.0f MB -> %.0f MB' % (t, why, os.path.getsize(src) / 1e6, os.path.getsize(dst) / 1e6), flush=True)
            except Exception as e:
                failed.append((t, str(e)[:200]))
    note = lambda m: print(m + ('\n::notice title=Videos for the TV::' + m if os.environ.get('GITHUB_ACTIONS') else ''), flush=True)
    note('%d copies made, %d fine as they are, %d failed' % (len(made), fine, len(failed)))
    for t, e in failed:
        note('failed: %s %s' % (t, e))
    if failed and not made:
        sys.exit(1)


def copy_wrong(info, dst):
    """'' if the copy at dst can stand in for the original (ffprobe info): H.264, with sound if the original has sound,
    and as long as it within half a second (the same checks media_originals.py makes before an original is deleted)."""
    out = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', dst],
                                    capture_output=True, text=True, timeout=120).stdout or '{}')
    kind = lambda i, k: next((x for x in i.get('streams', []) if x.get('codec_type') == k and not (x.get('disposition') or {}).get('attached_pic')), None)
    dur = lambda i: float((i.get('format') or {}).get('duration') or 0)
    v = kind(out, 'video')
    if not v or v.get('codec_name') != 'h264':
        return 'not H.264'
    if kind(info, 'audio') and not kind(out, 'audio'):
        return 'no sound'
    if not dur(info) or abs(dur(out) - dur(info)) > 0.5:
        return '%.2f s long against %.2f s' % (dur(out), dur(info))
    return ''


def eval_fps(info):
    """The video's frame rate (kept, up to 30: a 50 or 60 fps upload is halved, which a TV stick copes with better)."""
    v = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), {})
    try:
        a, b = (v.get('avg_frame_rate') or v.get('r_frame_rate') or '30/1').split('/')
        fps = float(a) / float(b or 1)
    except Exception:
        fps = 30
    if fps > 31:
        fps = fps / 2
    return round(fps, 3) if fps > 0 else 30


if __name__ == '__main__':
    main(sys.argv[1:])
