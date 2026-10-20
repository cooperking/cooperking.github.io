#!/usr/bin/env python3
"""Runs every Essentia model the calendar uses on each song's 30-second Spotify preview and keeps
everything the models return, unchanged: every class of every model, for every patch of audio,
plus the average over the clip. Nothing is mapped, ranked or renamed.

Writes to OUTDIR (default ./essentia-raw):
  README.txt
  averages.csv                one row per song: the mean over the clip of every output of every model
  per-patch/<model>.csv.gz    one row per song per patch: that patch's outputs, exactly as returned
"""
import csv, gzip, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analyze_audio import EFFNET, MUSICNN, HEADS, DEAM, MODELS_URL, model, nodes, load_songs, get  # noqa: E402

OUTDIR = os.path.abspath(os.environ.get('OUTDIR', 'essentia-raw'))


def main():
    import numpy as np
    import essentia
    essentia.log.infoActive = False
    essentia.log.warningActive = False
    from essentia.standard import (MonoLoader, TensorflowPredict2D, TensorflowPredictEffnetDiscogs,
                                   TensorflowPredictMusiCNN)

    os.makedirs(os.path.join(OUTDIR, 'per-patch'), exist_ok=True)
    pb, _ = model(EFFNET)
    effnet = TensorflowPredictEffnetDiscogs(graphFilename=pb, output='PartitionedCall:1')
    pb, _ = model(MUSICNN)
    musicnn = TensorflowPredictMusiCNN(graphFilename=pb, output='model/dense/BiasAdd')
    models = []   # (file name, algorithm, class labels, which embedding it reads)
    for name, path in HEADS.items():
        pb, meta = model(path)
        default_in = 'serving_default_model_Placeholder' if name == 'genre' else 'model/Placeholder'
        default_out = 'PartitionedCall:0' if name == 'genre' else ('model/Sigmoid' if name == 'moodtheme' else 'model/Softmax')
        i, o = nodes(meta, default_in, default_out)
        models.append((os.path.basename(path), TensorflowPredict2D(graphFilename=pb, input=i, output=o), meta['classes'], 'effnet', path, i, o))
    pb, meta = model(DEAM)
    i, o = nodes(meta, 'model/Placeholder', 'model/Identity')
    models.append((os.path.basename(DEAM), TensorflowPredict2D(graphFilename=pb, input=i, output=o), meta['classes'], 'musicnn', DEAM, i, o))

    songs = load_songs()
    print(len(songs), 'songs')
    head = ['n', 'title', 'artist', 'spotify_id', 'preview_id', 'clip_seconds']
    avg_cols = [f'{m[0]}: {c}' for m in models for c in m[2]]
    avg_rows = []
    writers, files = {}, {}
    for m in models:
        f = gzip.open(os.path.join(OUTDIR, 'per-patch', m[0] + '.csv.gz'), 'wt', newline='', encoding='utf-8')
        w = csv.writer(f)
        w.writerow(['n', 'spotify_id', 'patch'] + m[2])
        files[m[0]], writers[m[0]] = f, w
    tmp = tempfile.mkdtemp()
    failed = []
    for k, s in enumerate(songs, 1):
        try:
            mp3 = os.path.join(tmp, 'p.mp3')
            with open(mp3, 'wb') as f:
                f.write(get(s['url']))
            audio = MonoLoader(filename=mp3, sampleRate=16000, resampleQuality=4)()
            emb = {'effnet': effnet(audio), 'musicnn': musicnn(audio)}
            row = [s['n'], s['title'], s['artist'], s['id'], s['preview'], round(len(audio) / 16000, 3)]
            for name, algo, classes, src, *_ in models:
                out = np.asarray(algo(emb[src]))
                for p, vals in enumerate(out):
                    writers[name].writerow([s['n'], s['id'], p] + [f'{v:.6g}' for v in vals])
                row += [f'{v:.6g}' for v in out.mean(axis=0)]
            avg_rows.append(row)
            print(f"{k}/{len(songs)} {s['title']}")
        except Exception as e:
            failed.append(f"{s['n']} {s['title']}: {e}")
            print('FAILED', failed[-1])
    for f in files.values():
        f.close()
    with open(os.path.join(OUTDIR, 'averages.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(head + avg_cols)
        w.writerows(sorted(avg_rows, key=lambda r: r[0]))
    lines = [
        'Essentia model outputs for the John\'s Song of the Day previews',
        '',
        'Everything the models returned, unchanged. Audio: each song\'s 30-second Spotify preview (96 kbps MP3),',
        'loaded with MonoLoader(sampleRate=16000, resampleQuality=4). Essentia: essentia-tensorflow 2.1b6.dev1389.',
        '',
        'Embedding models:',
        f'  {MODELS_URL}{EFFNET}.pb  TensorflowPredictEffnetDiscogs(output="PartitionedCall:1"), default patches:',
        '    128 frames (about 2 s) every 62 frames (about 1 s)',
        f'  {MODELS_URL}{MUSICNN}.pb  TensorflowPredictMusiCNN(output="model/dense/BiasAdd"), default patches:',
        '    187 frames (about 3 s) every 93 frames (about 1.5 s)',
        '',
        'Output models (file in per-patch/, classes in the order the model returns them):',
    ]
    for name, algo, classes, src, path, i, o in models:
        lines.append(f'  {name}  reads {src}; TensorflowPredict2D(input="{i}", output="{o}"); {len(classes)} outputs')
        lines.append(f'    {MODELS_URL}{path}.pb')
    lines += [
        '',
        'averages.csv: one row per song, the mean over all patches of each output (column = "model: class").',
        'per-patch/*.csv.gz: one row per song per patch (patch 0 = start of the clip), values as returned.',
        '',
        'Models by the Music Technology Group, Universitat Pompeu Fabra (essentia.upf.edu/models), CC BY-NC-SA 4.0.',
    ]
    if failed:
        lines += ['', 'Songs that failed:'] + ['  ' + x for x in failed]
    with open(os.path.join(OUTDIR, 'README.txt'), 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print('done', len(avg_rows), 'songs,', len(failed), 'failed')


if __name__ == '__main__':
    main()
