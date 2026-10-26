#!/usr/bin/env python3
"""Turns John's songs and videos, before he uploads them, into what the calendar plays, so no copy is made after the
upload and there's nothing to delete afterwards. The files in the folder aren't changed.

    python3 sotd_convert.py FOLDER          writes FOLDER/converted/, then upload that folder on the Songs tab

Songs (.mp3 .m4a .wav .aif .aiff .flac): AAC at 192 kbps in .m4a, as the lighter copies are (_sotd/audio_light.py). An MP3
already under 192 kbps is copied as it is (the AAC wouldn't be smaller), as is an AAC .m4a at 200 kbps or less.
Checked: as long as the original within 0.2 s.

Videos (.mp4 .m4v .mov .mkv .webm): one already H.264, 1080p or less, 8 Mbps or less and 8-bit 4:2:0 is copied as it is;
any other becomes H.264 High, 1080p at most, CRF 21 (6 Mbps at most), 30 fps at most, AAC 160k, index first: as the TV
copies are (_sotd/video_tv.py). Checked: H.264, with sound if the original has sound, as long as it within 0.5 s.

Needs ffmpeg (and ffprobe if it's there; without it, ffmpeg's own description of each file is read). It's looked for on
the PATH, then in ~/bin, ~/.spotdl, /opt/homebrew/bin and /usr/local/bin. Without ffmpeg, songs are done with macOS's
own afconvert and afinfo, and videos are left out.
"""
import json, os, re, shutil, subprocess, sys

SONGS = ('.mp3', '.m4a', '.wav', '.aif', '.aiff', '.flac')
VIDEOS = ('.mp4', '.m4v', '.mov', '.mkv', '.webm')
HOME = os.path.expanduser('~')


def find(name):
    for d in [None, HOME + '/bin', HOME + '/.spotdl', '/opt/homebrew/bin', '/usr/local/bin']:
        p = shutil.which(name) if d is None else os.path.join(d, name)
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


FFMPEG, FFPROBE = find('ffmpeg'), find('ffprobe')
if os.environ.get('SOTD_NO_FFPROBE'):   # (for testing the other way)
    FFPROBE = None


def probe(path):
    """{dur, video: {codec, w, h, pix, fps} or None, audio: codec or None, kbps (the whole file's)}."""
    if FFPROBE:
        i = json.loads(subprocess.run([FFPROBE, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path],
                                      capture_output=True, text=True, timeout=120).stdout or '{}')
        st = [s for s in i.get('streams', []) if not (s.get('disposition') or {}).get('attached_pic')]
        v = next((s for s in st if s.get('codec_type') == 'video'), None)
        a = next((s for s in st if s.get('codec_type') == 'audio'), None)
        fmt = i.get('format') or {}
        fps = 0
        if v:
            try:
                n, d = (v.get('avg_frame_rate') or v.get('r_frame_rate') or '0/1').split('/'); fps = float(n) / float(d or 1)
            except Exception:
                fps = 0
        br = int(fmt.get('bit_rate') or 0) or int((a or {}).get('bit_rate') or 0)
        return {'dur': float(fmt.get('duration') or 0), 'kbps': round(br / 1000),
                'video': {'codec': v.get('codec_name'), 'w': int(v.get('width') or 0), 'h': int(v.get('height') or 0),
                          'pix': v.get('pix_fmt') or '', 'fps': fps} if v else None,
                'audio': a.get('codec_name') if a else None}
    if FFMPEG:   # ffmpeg's description of the file, as it prints it
        t = subprocess.run([FFMPEG, '-hide_banner', '-i', path], capture_output=True, text=True, timeout=120).stderr
        m = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', t)
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
        m = re.search(r'bitrate: (\d+) kb/s', t)
        out = {'dur': dur, 'kbps': int(m.group(1)) if m else 0, 'video': None, 'audio': None}
        for line in t.splitlines():
            if 'Stream #' not in line or '(attached pic)' in line:
                continue
            m = re.search(r'Video: (\w+)', line)
            if m and not out['video']:
                wh = re.search(r', (\d{2,5})x(\d{2,5})', line)
                pix = re.search(r'\b(yuvj?\d{3}p\w*|gbrp\w*|rgb\w*|nv12|p010\w*)\b', line)
                fps = re.search(r'([\d.]+) fps', line)
                out['video'] = {'codec': m.group(1), 'w': int(wh.group(1)) if wh else 0, 'h': int(wh.group(2)) if wh else 0,
                                'pix': pix.group(1) if pix else '', 'fps': float(fps.group(1)) if fps else 0}
            m = re.search(r'Audio: (\w+)', line)
            if m and not out['audio']:
                out['audio'] = m.group(1)
        return out
    # macOS's own: afinfo (songs only)
    t = subprocess.run(['afinfo', path], capture_output=True, text=True, timeout=60).stdout
    m = re.search(r'estimated duration: ([\d.]+)', t); b = re.search(r'bit rate: (\d+) bits per second', t)
    f = re.search(r"Data format:.*?'?(\.mp3|aac|lpcm|alac)", t)
    return {'dur': float(m.group(1)) if m else 0, 'kbps': round(int(b.group(1)) / 1000) if b else 0, 'video': None,
            'audio': {'.mp3': 'mp3', 'aac': 'aac', 'lpcm': 'pcm', 'alac': 'alac'}.get(f.group(1)) if f else None}


def video_needs(p):
    """Why the TV would need a copy of this video ('' if it plays fine as it is); the same rule as video_tv.py."""
    v = p['video']
    why = []
    if v['codec'] != 'h264':
        why.append(v['codec'] or 'unknown codec')
    if v['h'] > 1080 or v['w'] > 1920:
        why.append('%dx%d' % (v['w'], v['h']))
    if v['pix'] and v['pix'] not in ('yuv420p', 'yuvj420p'):
        why.append(v['pix'])
    if p['kbps'] > 8000:
        why.append('%d Mbps' % round(p['kbps'] / 1000))
    return ', '.join(why)


def song(src, dst):
    p = probe(src)
    if not p['audio']:
        return 'skipped: no sound in it'
    if (p['audio'] == 'mp3' and 0 < p['kbps'] < 192) or (p['audio'] == 'aac' and 0 < p['kbps'] <= 200):
        out = dst if p['audio'] == 'aac' else os.path.splitext(dst)[0] + '.mp3'
        shutil.copy2(src, out)
        return 'copied as it is (%s, %d kbps)' % (p['audio'], p['kbps'])
    if FFMPEG:
        subprocess.run([FFMPEG, '-v', 'error', '-y', '-i', src, '-map', '0:a:0', '-c:a', 'aac', '-b:a', '192k', '-ac', '2',
                        '-map_metadata', '-1', '-movflags', '+faststart', dst], check=True, timeout=900)
    else:
        subprocess.run(['afconvert', '-f', 'm4af', '-d', 'aac', '-b', '192000', '-c', '2', src, dst], check=True, timeout=900)
    q = probe(dst)
    if not p['dur'] or abs(q['dur'] - p['dur']) > 0.2:
        os.remove(dst)
        return 'FAILED: came out %.2f s long against %.2f s' % (q['dur'], p['dur'])
    return 'made AAC: %.1f MB -> %.1f MB' % (os.path.getsize(src) / 1e6, os.path.getsize(dst) / 1e6)


def x264():
    return FFMPEG and 'libx264' in subprocess.run([FFMPEG, '-hide_banner', '-encoders'], capture_output=True, text=True).stdout


def video(src, dst):
    if not FFMPEG:
        return 'skipped: videos need ffmpeg'
    p = probe(src)
    if not p['video']:
        return 'skipped: no picture in it'
    why = video_needs(p)
    if not why and src.lower().endswith(('.mp4', '.m4v')):
        shutil.copy2(src, dst)
        return 'copied as it is (H.264, %dx%d, %.1f Mbps)' % (p['video']['w'], p['video']['h'], p['kbps'] / 1000)
    fps = p['video']['fps'] or 30
    if fps > 31:
        fps = fps / 2
    enc = (['-c:v', 'libx264', '-preset', 'medium', '-profile:v', 'high', '-level:v', '4.1', '-crf', '21', '-maxrate', '6M', '-bufsize', '12M']
           if x264() else   # an ffmpeg without x264: the Mac's own H.264 encoder, at a set rate
           ['-c:v', 'h264_videotoolbox', '-profile:v', 'high', '-b:v', '4500k', '-maxrate', '6M', '-bufsize', '12M'])
    cmd = [FFMPEG, '-v', 'error', '-y', '-i', src, '-map', '0:v:0', '-map', '0:a:0?',
           '-vf', "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p",
           *enc, '-r', str(round(fps, 3)), '-c:a', 'aac', '-b:a', '160k', '-ac', '2', '-movflags', '+faststart', dst]
    subprocess.run(cmd, check=True, timeout=7200)
    q = probe(dst)
    bad = ('not H.264' if not q['video'] or q['video']['codec'] != 'h264' else
           'no sound' if p['audio'] and not q['audio'] else
           '%.2f s long against %.2f s' % (q['dur'], p['dur']) if not p['dur'] or abs(q['dur'] - p['dur']) > 0.5 else '')
    if bad:
        os.remove(dst)
        return 'FAILED: ' + bad
    return 'made H.264 (was %s): %.0f MB -> %.0f MB' % (why or 'not MP4', os.path.getsize(src) / 1e6, os.path.getsize(dst) / 1e6)


def main(args):
    if len(args) != 1 or not os.path.isdir(args[0]):
        sys.exit(__doc__)
    folder = os.path.abspath(args[0]); out = os.path.join(folder, 'converted')
    os.makedirs(out, exist_ok=True)
    if not FFMPEG:
        print('No ffmpeg found: songs only, with macOS\'s afconvert (videos are left out).')
    names = sorted(n for n in os.listdir(folder) if not n.startswith('.') and os.path.isfile(os.path.join(folder, n)))
    failed = 0
    for n in names:
        base, ext = os.path.splitext(n); ext = ext.lower()
        if ext not in SONGS + VIDEOS:
            continue
        src = os.path.join(folder, n)
        dst = os.path.join(out, base + ('.m4a' if ext in SONGS else '.mp4'))
        if os.path.exists(dst) or os.path.exists(os.path.join(out, base + '.mp3')) and ext in SONGS:
            print('%s: already in converted/' % n); continue
        try:
            said = song(src, dst) if ext in SONGS else video(src, dst)
        except Exception as e:
            said = 'FAILED: %s' % str(e)[:200]
        failed += said.startswith('FAILED')
        print('%s: %s' % (n, said), flush=True)
    print('Done: upload %s on the Songs tab (Upload a folder of songs…).%s' % (out, ' %d failed: their originals can go up instead.' % failed if failed else ''))


if __name__ == '__main__':
    main(sys.argv[1:])
