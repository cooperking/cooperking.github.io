#!/usr/bin/env python3
"""Runs the same Essentia models as the Song of the Day calendar on whole audio files on your own computer,
and keeps everything the models return, unchanged.

    python essentia_full.py <folder of audio files> <output folder>

Reads every .mp3 .m4a .aac .flac .wav .aif .aiff .ogg .opus file in the folder (and its subfolders).
Writes to the output folder:
  README.txt
  averages.csv               one row per file: the mean over the whole file of every output of every model
  per-patch/<model>.csv.gz   one row per file per patch (about one a second): that patch's outputs, as returned
Run it again to pick up new files: files already done are skipped.
Models: Music Technology Group, Universitat Pompeu Fabra, https://essentia.upf.edu/models.html (CC BY-NC-SA 4.0).
"""
import csv, gzip, json, os, subprocess, sys, time

MODELS_URL = 'https://essentia.upf.edu/models/'
MODEL_DIR = os.path.expanduser('~/.cache/essentia-models')
EFFNET = 'feature-extractors/discogs-effnet/discogs-effnet-bs64-1'
MUSICNN = 'feature-extractors/musicnn/msd-musicnn-1'
HEADS = [   # (model, default input node, default output node); each model's own .json overrides these
    ('classification-heads/mtg_jamendo_moodtheme/mtg_jamendo_moodtheme-discogs-effnet-1', 'model/Placeholder', 'model/Sigmoid'),
    ('classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1', 'serving_default_model_Placeholder', 'PartitionedCall:0'),
    ('classification-heads/mood_happy/mood_happy-discogs-effnet-1', 'model/Placeholder', 'model/Softmax'),
    ('classification-heads/mood_sad/mood_sad-discogs-effnet-1', 'model/Placeholder', 'model/Softmax'),
    ('classification-heads/mood_relaxed/mood_relaxed-discogs-effnet-1', 'model/Placeholder', 'model/Softmax'),
    ('classification-heads/mood_aggressive/mood_aggressive-discogs-effnet-1', 'model/Placeholder', 'model/Softmax'),
    ('classification-heads/mood_party/mood_party-discogs-effnet-1', 'model/Placeholder', 'model/Softmax'),
]
DEAM = ('classification-heads/deam/deam-msd-musicnn-2', 'model/Placeholder', 'model/Identity')
AUDIO = ('.mp3', '.m4a', '.aac', '.flac', '.wav', '.aif', '.aiff', '.ogg', '.opus')


def fetch(path):
    """Downloads a model's .pb and .json once (with curl, which uses the Mac's own certificates)."""
    os.makedirs(MODEL_DIR, exist_ok=True)
    base = os.path.join(MODEL_DIR, os.path.basename(path))
    for ext in ('.pb', '.json'):
        if not os.path.exists(base + ext):
            print('downloading', os.path.basename(path) + ext)
            subprocess.run(['curl', '-fsSL', '--retry', '3', '-o', base + ext + '.part', MODELS_URL + path + ext], check=True)
            os.replace(base + ext + '.part', base + ext)
    with open(base + '.json') as f:
        return base + '.pb', json.load(f)


def nodes(meta, din, dout):
    schema = meta.get('schema') or {}
    ins = schema.get('inputs') or []
    outs = [o for o in schema.get('outputs') or [] if o.get('output_purpose') == 'predictions']
    return (ins[0]['name'] if ins else din), (outs[0]['name'] if outs else dout)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, out = (os.path.abspath(os.path.expanduser(p)) for p in sys.argv[1:])
    if not os.path.isdir(src):
        sys.exit(f'No folder at {src}')
    files = sorted(os.path.join(d, f) for d, _, fs in os.walk(src) for f in fs
                   if f.lower().endswith(AUDIO) and not f.startswith('._'))
    drm = sum(1 for d, _, fs in os.walk(src) for f in fs if f.lower().endswith('.m4p'))
    print(f'{len(files)} audio files in {src}' + (f' ({drm} protected .m4p files skipped: they can\'t be read)' if drm else ''))
    os.makedirs(os.path.join(out, 'per-patch'), exist_ok=True)
    progress = os.path.join(out, 'progress.jsonl')
    done = {}
    if os.path.exists(progress):
        for line in open(progress, encoding='utf-8'):
            r = json.loads(line)
            done[r['file']] = r
    todo = [f for f in files if os.path.relpath(f, src) not in done]
    print(f'{len(done)} done before, {len(todo)} to analyse')

    import numpy as np
    import essentia
    essentia.log.infoActive = False
    essentia.log.warningActive = False
    from essentia.standard import (MonoLoader, MetadataReader, TensorflowPredict2D, TensorflowPredictEffnetDiscogs,
                                   TensorflowPredictMusiCNN)
    pb, _ = fetch(EFFNET)
    effnet = TensorflowPredictEffnetDiscogs(graphFilename=pb, output='PartitionedCall:1')
    pb, _ = fetch(MUSICNN)
    musicnn = TensorflowPredictMusiCNN(graphFilename=pb, output='model/dense/BiasAdd')
    models = []
    for path, din, dout in HEADS + [DEAM]:
        pb, meta = fetch(path)
        i, o = nodes(meta, din, dout)
        src_emb = 'musicnn' if path == DEAM[0] else 'effnet'
        models.append((os.path.basename(path), TensorflowPredict2D(graphFilename=pb, input=i, output=o), meta['classes'], src_emb, path, i, o))

    t0 = time.time()
    for k, path in enumerate(todo, 1):
        rel = os.path.relpath(path, src)
        try:
            tags = MetadataReader(filename=path, failOnError=False)()
            title, artist, album = tags[0], tags[1], tags[2]
            audio = MonoLoader(filename=path, sampleRate=16000, resampleQuality=4)()
            emb = {'effnet': effnet(audio), 'musicnn': musicnn(audio)}
            rec = {'file': rel, 'title': title, 'artist': artist, 'album': album, 'seconds': round(len(audio) / 16000, 3), 'avg': {}}
            for name, algo, classes, e, *_ in models:
                res = np.asarray(algo(emb[e]))
                gz = os.path.join(out, 'per-patch', name + '.csv.gz')
                new = not os.path.exists(gz)
                with gzip.open(gz, 'at', newline='', encoding='utf-8') as f:   # each run adds a gzip member; readers see one file
                    w = csv.writer(f)
                    if new:
                        w.writerow(['file', 'patch'] + classes)
                    for p, vals in enumerate(res):
                        w.writerow([rel, p] + [f'{v:.6g}' for v in vals])
                rec['avg'][name] = [float(f'{v:.6g}') for v in res.mean(axis=0)]
            with open(progress, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            done[rel] = rec
            left = (time.time() - t0) / k * (len(todo) - k)
            print(f'{k}/{len(todo)} {rel}  (about {left / 60:.0f} min left)')
        except Exception as ex:
            print(f'{k}/{len(todo)} FAILED {rel}: {ex}')

    cols = [f'{m[0]}: {c}' for m in models for c in m[2]]
    with open(os.path.join(out, 'averages.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['file', 'title', 'artist', 'album', 'seconds'] + cols)
        for rel in sorted(done):
            r = done[rel]
            w.writerow([rel, r['title'], r['artist'], r['album'], r['seconds']] +
                       [v for m in models for v in r['avg'].get(m[0], [''] * len(m[2]))])
    lines = ['Essentia model outputs for whole audio files', '',
             f'Source folder: {src}', 'Everything the models returned, unchanged. Audio loaded with MonoLoader(sampleRate=16000, resampleQuality=4).',
             f'Essentia {essentia.__version__}.', '',
             f'Embeddings: {MODELS_URL}{EFFNET}.pb (patches of about 2 s, every 1 s);',
             f'            {MODELS_URL}{MUSICNN}.pb (patches of about 3 s, every 1.5 s).', '', 'Output models:']
    for name, algo, classes, e, path, i, o in models:
        lines.append(f'  {name}: reads {e}; TensorflowPredict2D(input="{i}", output="{o}"); {len(classes)} outputs; {MODELS_URL}{path}.pb')
    lines += ['', 'averages.csv: one row per file, the mean over all patches of each output (column = "model: class").',
              'per-patch/*.csv.gz: one row per file per patch (patch 0 = start of the file), values as returned.',
              'progress.jsonl: what has been done, so a rerun skips those files.', '',
              'Models by the Music Technology Group, Universitat Pompeu Fabra (essentia.upf.edu/models), CC BY-NC-SA 4.0.']
    with open(os.path.join(out, 'README.txt'), 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'Finished: {len(done)} files in {out}')


if __name__ == '__main__':
    main()
