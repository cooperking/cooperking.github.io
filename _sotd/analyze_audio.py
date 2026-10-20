#!/usr/bin/env python3
"""Listens to each song's 30-second Spotify preview with Essentia's pre-trained music models
and writes the averaged model outputs to _sotd/data/audio.json.

Only songs not analysed yet are processed; --all redoes every song; --check only prints how many
songs are waiting (and doesn't need Essentia installed).

Models: Music Technology Group, Universitat Pompeu Fabra, https://essentia.upf.edu/models.html
(CC BY-NC-SA 4.0). This site is non-commercial and credits them on the calendar page.
"""
import json, os, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'data', 'audio.json')
SONGS_URL = os.environ.get('SOTD_SONGS_URL', 'https://sotd-playlists.cooperking.workers.dev/songs.json')
MODELS_URL = 'https://essentia.upf.edu/models/'
MODEL_DIR = os.path.expanduser(os.environ.get('ESSENTIA_MODELS', '~/.cache/essentia-models'))
UA = {'User-Agent': 'SongOfTheDay/1.0 (+https://cooperking.github.io/songs/)'}

EFFNET = 'feature-extractors/discogs-effnet/discogs-effnet-bs64-1'
MUSICNN = 'feature-extractors/musicnn/msd-musicnn-1'
# heads that read the Discogs-EffNet embeddings
HEADS = {
    'moodtheme': 'classification-heads/mtg_jamendo_moodtheme/mtg_jamendo_moodtheme-discogs-effnet-1',
    'genre': 'classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1',
    'happy': 'classification-heads/mood_happy/mood_happy-discogs-effnet-1',
    'sad': 'classification-heads/mood_sad/mood_sad-discogs-effnet-1',
    'relaxed': 'classification-heads/mood_relaxed/mood_relaxed-discogs-effnet-1',
    'aggressive': 'classification-heads/mood_aggressive/mood_aggressive-discogs-effnet-1',
    'party': 'classification-heads/mood_party/mood_party-discogs-effnet-1',
}
DEAM = 'classification-heads/deam/deam-msd-musicnn-2'   # valence and arousal, 1 to 9, reads MusiCNN embeddings


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception as e:
            if i == tries - 1:
                raise
            print('  retry', url, e)
            time.sleep(3 * (i + 1))


def model(path):
    """Downloads a model's .pb and .json once; returns (pb path, metadata)."""
    os.makedirs(MODEL_DIR, exist_ok=True)
    base = os.path.join(MODEL_DIR, os.path.basename(path))
    for ext in ('.pb', '.json'):
        if not os.path.exists(base + ext):
            data = get(MODELS_URL + path + ext)
            with open(base + ext + '.part', 'wb') as f:
                f.write(data)
            os.replace(base + ext + '.part', base + ext)
    with open(base + '.json') as f:
        return base + '.pb', json.load(f)


def nodes(meta, default_in, default_out):
    schema = meta.get('schema') or {}
    ins = schema.get('inputs') or []
    outs = [o for o in schema.get('outputs') or [] if o.get('output_purpose') == 'predictions']
    return (ins[0]['name'] if ins else default_in), (outs[0]['name'] if outs else default_out)


def load_songs():
    songs = json.loads(get(SONGS_URL))['songs']
    saved = {}
    path = os.path.join(HERE, 'data', 'previews.json')
    if os.path.exists(path):
        with open(path) as f:
            saved = json.load(f)['previews']
    out = []
    for s in songs:
        p = s.get('p') or ('https://p.scdn.co/mp3-preview/' + saved[s['id']] if s['id'] in saved else '')
        if p:
            out.append({'id': s['id'], 'n': s['n'], 'title': s['title'], 'artist': s['artist'],
                        'preview': p.rsplit('/', 1)[-1], 'url': p})
    return out


def main():
    redo = '--all' in sys.argv
    old = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            old = json.load(f)
    done = old.get('songs', {})
    songs = load_songs()
    todo = [s for s in songs if redo or s['id'] not in done or done[s['id']].get('preview') != s['preview']]
    print(f'{len(songs)} songs with previews, {len(todo)} to analyse')
    if '--check' in sys.argv:
        gh = os.environ.get('GITHUB_OUTPUT')
        if gh:
            with open(gh, 'a') as f:
                f.write(f'todo={len(todo)}\n')
        return
    if not todo:
        return

    import numpy as np
    import essentia
    essentia.log.infoActive = False
    essentia.log.warningActive = False
    from essentia.standard import (MonoLoader, TensorflowPredict2D, TensorflowPredictEffnetDiscogs,
                                   TensorflowPredictMusiCNN)

    pb, _ = model(EFFNET)
    effnet = TensorflowPredictEffnetDiscogs(graphFilename=pb, output='PartitionedCall:1')
    pb, _ = model(MUSICNN)
    musicnn = TensorflowPredictMusiCNN(graphFilename=pb, output='model/dense/BiasAdd')
    heads, classes = {}, {}
    for name, path in HEADS.items():
        pb, meta = model(path)
        default_in = 'serving_default_model_Placeholder' if name == 'genre' else 'model/Placeholder'
        default_out = 'PartitionedCall:0' if name == 'genre' else ('model/Sigmoid' if name == 'moodtheme' else 'model/Softmax')
        i, o = nodes(meta, default_in, default_out)
        heads[name] = TensorflowPredict2D(graphFilename=pb, input=i, output=o)
        classes[name] = meta['classes']
    pb, meta = model(DEAM)
    i, o = nodes(meta, 'model/Placeholder', 'model/Identity')
    deam = TensorflowPredict2D(graphFilename=pb, input=i, output=o)
    deam_classes = [c.lower() for c in meta.get('classes', ['valence', 'arousal'])]

    tmp = tempfile.mkdtemp()
    failed = {}
    for k, s in enumerate(todo, 1):
        try:
            mp3 = os.path.join(tmp, 'p.mp3')
            with open(mp3, 'wb') as f:
                f.write(get(s['url']))
            audio = MonoLoader(filename=mp3, sampleRate=16000, resampleQuality=4)()
            emb = effnet(audio)
            rec = {'n': s['n'], 'title': s['title'], 'artist': s['artist'], 'preview': s['preview'],
                   'seconds': round(len(audio) / 16000, 1)}
            for name, head in heads.items():
                p = np.asarray(head(emb)).mean(axis=0)
                if name == 'moodtheme':
                    rec['moodtheme'] = [round(float(x), 4) for x in p]
                elif name == 'genre':
                    top = np.argsort(-p)[:8]
                    rec['styles'] = [[classes['genre'][j], round(float(p[j]), 4)] for j in top]
                else:
                    rec[name] = round(float(p[classes[name].index(name)]), 4)
            va = np.asarray(deam(musicnn(audio))).mean(axis=0)
            for j, c in enumerate(deam_classes[:2]):
                rec[c] = round(float(va[j]), 3)
            done[s['id']] = rec
            print(f"{k}/{len(todo)} {s['title']} — {s['artist']}: happy {rec['happy']} sad {rec['sad']} "
                  f"valence {rec.get('valence')} arousal {rec.get('arousal')}")
        except Exception as e:
            failed[s['id']] = f"{s['n']} {s['title']}: {type(e).__name__}: {e}"[:300]
            print(f"{k}/{len(todo)} FAILED {failed[s['id']]}")
        if k % 25 == 0 or k == len(todo):
            save(done, classes, failed)
    print(f'done, {len(failed)} failed')


def save(done, classes, failed=None):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out = {
        'about': ("Essentia model outputs for each song's 30-second Spotify preview, averaged over the clip. "
                  "moodtheme: MTG-Jamendo mood/theme scores (0 to 1, in the order of moodthemeClasses); "
                  "happy, sad, relaxed, aggressive, party: chance the clip has that mood; valence and arousal: "
                  "DEAM scale 1 to 9; styles: top Discogs styles. Models by the Music Technology Group, "
                  "Universitat Pompeu Fabra (essentia.upf.edu/models), CC BY-NC-SA 4.0."),
        'moodthemeClasses': classes['moodtheme'],
        'songs': dict(sorted(done.items(), key=lambda kv: kv[1]['n'])),
    }
    if failed:
        out['failed'] = failed
    with open(OUT + '.part', 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=0, separators=(',', ':'))
    os.replace(OUT + '.part', OUT)


if __name__ == '__main__':
    main()
