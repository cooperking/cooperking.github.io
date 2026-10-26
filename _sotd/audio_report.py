#!/usr/bin/env python3
"""What John's own songs (the members' MP3s, a/<track>.mp3 in the R2 bucket) are, for streaming: read-only.

    python3 _sotd/audio_report.py      writes _sotd/data/audio-report.json and prints a summary

For each MP3 it reads only the start of the file (the ID3 tag's size and the first frame's Xing/Info/VBRI header) and
what ffprobe reads through a short-lived link: length, bitrate, sample rate, channels, any cover picture inside it.
Then it sets each song's length beside its video's (_sotd/data/media-report.json), so the songs whose video is longer
or shorter than the MP3 (an intro or a different ending) are listed. Nothing in the bucket is changed.

Streaming, briefly: a browser starts playing once it has the tag at the front of the file and the first frames, so a
large tag (usually a cover picture) delays the start; a VBR file without its Xing header can't be seeked accurately
and its length is a guess; 128 to 320 kbps all stream easily, the higher ones being larger to download and keep.

Reaches R2 through its S3 API with the same secrets as video_tv.py (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET,
CLOUDFLARE_ACCOUNT_ID). Run by .github/workflows/audio-report.yml.
"""
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'data', 'audio-report.json')
VIDEOS = os.path.join(HERE, 'data', 'media-report.json')
KEY_ID = os.environ.get('R2_ACCESS_KEY_ID', '').strip()
SECRET = os.environ.get('R2_SECRET_ACCESS_KEY', '').strip()
BUCKET = os.environ.get('R2_BUCKET', '').strip()
ACCOUNT = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '').strip()


def id3_size(head):
    """The ID3v2 tag's length in bytes, header (and footer) included; 0 if there's none."""
    if len(head) < 10 or head[:3] != b'ID3':
        return 0
    size = (head[6] & 0x7f) << 21 | (head[7] & 0x7f) << 14 | (head[8] & 0x7f) << 7 | (head[9] & 0x7f)
    return size + 10 + (10 if head[5] & 0x10 else 0)


def seek_header(frame):
    """The first frame's header: 'Xing' (VBR), 'Info' (CBR, written by LAME), 'VBRI' (Fraunhofer VBR) or ''."""
    for tag in (b'Xing', b'Info', b'VBRI'):
        if tag in frame[:200]:
            return tag.decode()
    return ''


def main():
    if not (KEY_ID and SECRET and BUCKET and ACCOUNT):
        sys.exit('Not set up: the repository secrets R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET and CLOUDFLARE_ACCOUNT_ID.')
    import boto3
    from botocore.config import Config
    s3 = boto3.client('s3', endpoint_url='https://%s.r2.cloudflarestorage.com' % ACCOUNT, aws_access_key_id=KEY_ID,
                      aws_secret_access_key=SECRET, region_name='auto', config=Config(signature_version='s3v4', retries={'max_attempts': 5}))
    keys, token = [], None
    while True:
        kw = {'Bucket': BUCKET, 'Prefix': 'a/'}
        if token:
            kw['ContinuationToken'] = token
        r = s3.list_objects_v2(**kw)
        keys += [(o['Key'], o['Size']) for o in r.get('Contents', []) if o['Key'].endswith('.mp3')]
        if not r.get('IsTruncated'):
            break
        token = r['NextContinuationToken']
    songs = {}
    for key, size in sorted(keys):
        t = key[2:-4]
        e = {'size': size}
        try:
            head = s3.get_object(Bucket=BUCKET, Key=key, Range='bytes=0-9')['Body'].read()
            e['id3'] = id3_size(head)
            frame = s3.get_object(Bucket=BUCKET, Key=key, Range='bytes=%d-%d' % (e['id3'], e['id3'] + 255))['Body'].read()
            e['seek'] = seek_header(frame)
            url = s3.generate_presigned_url('get_object', Params={'Bucket': BUCKET, 'Key': key}, ExpiresIn=600)
            info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', url],
                                             capture_output=True, text=True, timeout=120).stdout or '{}')
            a = next((x for x in info.get('streams', []) if x.get('codec_type') == 'audio'), {})
            pic = [x for x in info.get('streams', []) if (x.get('disposition') or {}).get('attached_pic')]
            fmt = info.get('format') or {}
            e.update({'codec': a.get('codec_name'), 'hz': int(a.get('sample_rate') or 0), 'ch': int(a.get('channels') or 0),
                      'kbps': round(int(a.get('bit_rate') or fmt.get('bit_rate') or 0) / 1000),
                      'dur': round(float(fmt.get('duration') or a.get('duration') or 0), 1),
                      'art': '%sx%s %s' % (pic[0].get('width'), pic[0].get('height'), pic[0].get('codec_name')) if pic else ''})
        except Exception as x:
            e['err'] = str(x)[:160]
        try:   # its lighter copy (audio_light.py), if it has one
            lk = 'l/%s.m4a' % t
            lsize = s3.head_object(Bucket=BUCKET, Key=lk)['ContentLength']
            lurl = s3.generate_presigned_url('get_object', Params={'Bucket': BUCKET, 'Key': lk}, ExpiresIn=600)
            li = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', lurl],
                                           capture_output=True, text=True, timeout=120).stdout or '{}')
            la = next((x for x in li.get('streams', []) if x.get('codec_type') == 'audio'), {})
            e['light'] = {'size': lsize, 'codec': la.get('codec_name'), 'kbps': round(int(la.get('bit_rate') or 0) / 1000),
                          'dur': round(float((li.get('format') or {}).get('duration') or 0), 2)}
        except Exception:
            pass
        songs[t] = e
        print(t, e, flush=True)
    # beside the videos' lengths
    vids = (json.load(open(VIDEOS)).get('videos') or {}) if os.path.exists(VIDEOS) else {}
    for t, e in songs.items():
        v = vids.get(t)
        if v and v.get('dur') and e.get('dur'):
            e['video'] = round(v['dur'] - e['dur'], 1)   # how much longer the video is (negative: shorter)
    ok = [e for e in songs.values() if 'err' not in e]
    count = lambda f: sum(1 for e in ok if f(e))
    summary = {
        'songs': len(songs), 'unreadable': len(songs) - len(ok),
        'total MB': round(sum(e['size'] for e in songs.values()) / 1e6),
        'kbps': {k: count(lambda e, k=k: e.get('kbps') == k) for k in sorted({e.get('kbps') for e in ok})},
        'sample rates': {k: count(lambda e, k=k: e.get('hz') == k) for k in sorted({e.get('hz') for e in ok})},
        'mono': count(lambda e: e.get('ch') == 1),
        'VBR (Xing/VBRI)': count(lambda e: e.get('seek') in ('Xing', 'VBRI')),
        'CBR with an Info header': count(lambda e: e.get('seek') == 'Info'),
        'no seek header': count(lambda e: not e.get('seek')),
        'a cover picture inside': count(lambda e: e.get('art')),
        'tag over 300 KB at the front': count(lambda e: e.get('id3', 0) > 300_000),
        'largest tag KB': round(max([e.get('id3', 0) for e in ok] or [0]) / 1000),
        'video within 2 s of the song': count(lambda e: 'video' in e and abs(e['video']) <= 2),
        'video 2 to 10 s different': count(lambda e: 'video' in e and 2 < abs(e['video']) <= 10),
        'video over 10 s different': count(lambda e: 'video' in e and abs(e['video']) > 10),
        'no video to compare': count(lambda e: 'video' not in e),
        'lighter copies': count(lambda e: e.get('light')),
        'lighter copies MB': round(sum(e['light']['size'] for e in ok if e.get('light')) / 1e6),
        'their MP3s MB': round(sum(e['size'] for e in ok if e.get('light')) / 1e6),
        'largest length difference, copy against MP3 (s)': max([abs(e['light']['dur'] - e['dur']) for e in ok if e.get('light')] or [0]),
    }
    json.dump({'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'summary': summary, 'songs': songs},
              open(OUT, 'w'), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main()
