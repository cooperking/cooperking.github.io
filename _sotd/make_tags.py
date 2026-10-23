#!/usr/bin/env python3
"""Builds songs/tags.json: up to two moods (of ten) and up to two broad genres for every song.

Each song's mood is weighed up from three voices, then John's corrections win:
  Claude   _sotd/data/claude.json   picks for songs Claude knows, sound and lyrics (a guess counts for little)
  Listeners  Last.fm track tags      what listeners call the song (cache outside the repo; never published)
  Sound    _sotd/data/audio.json    Essentia's mood models on the 30-second preview
  John     _sotd/data/john.json     corrections from the admin page; these always win
Genres: MusicBrainz tags (album, artists, recording), Last.fm tags and Essentia's Discogs styles,
with Claude's corrections and then John's on top.

A song is marked "check" unless Claude knows or has researched it (and listeners and audio don't both disagree), or
checked it thoroughly (claude.json "checked"; those picks stand as given).
"""
import json, math, os, sys, urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from genres import GENRES, bucket, dist, discogs_bucket   # noqa: E402

DATA = os.path.join(HERE, 'data')
OUT = os.environ.get('SOTD_OUT') or os.path.join(HERE, '..', 'songs', 'moods.json')
SONGS_URL = os.environ.get('SOTD_SONGS_URL', 'https://sotd-playlists.cooperking.workers.dev/songs.json')
LASTFM = os.path.expanduser(os.environ.get('LASTFM_CACHE', '~/.cache/lastfm/tags.json'))

MOODS = ["Joyful", "Uplifting", "Energetic", "Playful", "Mellow", "Dreamy", "Romantic", "Bittersweet", "Melancholy", "Dark"]
MOOD_INFO = {
    "Joyful": "happy and bright", "Uplifting": "hopeful and rousing", "Energetic": "driving and high-energy",
    "Playful": "fun, cheeky or quirky", "Mellow": "calm and laid-back", "Dreamy": "hazy, floating, atmospheric",
    "Romantic": "love and longing", "Bittersweet": "happy-sad, nostalgic", "Melancholy": "sad or lonely",
    "Dark": "brooding, tense or angry",
}

# ---------- listener tags -> moods ----------
TAG_MOODS = {}
for mood, words in {
    "Joyful": "happy|feel good|feelgood|feel-good|joyful|joy|cheerful|sunny|sunshine|euphoric|celebration|good mood|"
              "happy music|summer|party|good vibes|positive",
    "Uplifting": "uplifting|inspiring|inspirational|hopeful|anthemic|anthem|epic|soaring|triumphant|empowering|"
                 "motivational|optimistic|spiritual|gospel|life affirming|life-affirming",
    "Energetic": "energetic|energy|driving|fast|intense|powerful|rocking|adrenaline|workout|loud|upbeat|high energy|"
                 "headbanging|frantic|raw",
    "Playful": "fun|playful|quirky|silly|funny|cheeky|whimsical|humor|humour|novelty|bouncy|groovy|catchy|cute|"
               "comedy|tongue in cheek|witty|ironic|kitsch|camp",
    "Mellow": "mellow|chill|chillout|chill out|chilled|relaxing|relax|relaxed|calm|laid back|laid-back|soft|gentle|"
              "peaceful|smooth|lazy|easy listening|sunday|soothing|quiet|slow|easy",
    "Dreamy": "dreamy|ethereal|atmospheric|hypnotic|hazy|trippy|spacey|spacy|floating|lush|ambient|dream|"
              "psychedelic|otherworldly|surreal|cinematic|haunting",
    "Romantic": "romantic|love|love songs|love song|sensual|sexy|tender|sweet|seductive|passionate|in love|lovely|"
                "love at first listen|romance|wedding",
    "Bittersweet": "bittersweet|nostalgic|nostalgia|wistful|poignant|reflective|longing|yearning|memories|autumn|"
                   "sentimental|regret",
    "Melancholy": "melancholy|melancholic|melancholia|sad|sadness|depressing|depressive|depressed|heartbreak|"
                  "heartbroken|heartbreaking|lonely|loneliness|sorrow|mournful|tearjerker|sad songs|breakup|"
                  "break up|crying|grief|despair|bleak|sad but beautiful|introspective",
    "Dark": "dark|brooding|ominous|menacing|sinister|angry|anger|gloomy|creepy|eerie|tense|aggressive|paranoid|"
            "apocalyptic|evil|noir|disturbing|unsettling|protest|political",
}.items():
    for w in words.split('|'):
        TAG_MOODS.setdefault(w, []).append((mood, 1.0))
TAG_MOODS["emotional"] = [("Melancholy", 0.5), ("Bittersweet", 0.5)]
TAG_MOODS["beautiful"] = [("Romantic", 0.4), ("Dreamy", 0.3)]
TAG_MOODS["dance"] = [("Energetic", 0.5)]

# ---------- sound models -> moods (weights on collection-wide z-scores) ----------
# mt: = an MTG-Jamendo mood/theme tag; the rest are the single-mood models and DEAM valence/arousal
SOUND = {
    "Joyful": {"happy": 1, "valence": .7, "mt:happy": .6, "mt:positive": .4, "mt:upbeat": .3, "mt:summer": .3, "sad": -.6},
    "Uplifting": {"mt:uplifting": .8, "mt:hopeful": .6, "mt:inspiring": .6, "mt:epic": .4, "mt:motivational": .4,
                  "mt:powerful": .2, "valence": .3},
    "Energetic": {"arousal": 1, "mt:energetic": .6, "mt:fast": .5, "party": .4, "mt:action": .3, "aggressive": .3,
                  "relaxed": -.6},
    "Playful": {"mt:fun": .8, "mt:funny": .6, "mt:groovy": .4, "party": .4, "mt:children": .3, "mt:retro": .3, "valence": .3},
    "Mellow": {"relaxed": 1, "mt:calm": .6, "mt:relaxing": .5, "mt:soft": .5, "mt:slow": .3, "arousal": -.6, "aggressive": -.3},
    "Dreamy": {"mt:dream": .8, "mt:space": .5, "mt:meditative": .4, "mt:soundscape": .4, "mt:deep": .3, "mt:nature": .2,
               "relaxed": .3},
    "Romantic": {"mt:romantic": .8, "mt:love": .7, "mt:sexy": .4, "mt:ballad": .3, "mt:soft": .2},
    "Bittersweet": {"mt:emotional": .6, "mt:melancholic": .4, "mt:hopeful": .3, "mt:ballad": .3, "mt:retro": .2, "valence": .2},
    "Melancholy": {"sad": 1, "mt:sad": .7, "mt:melancholic": .7, "mt:emotional": .3, "valence": -.5, "happy": -.4},
    "Dark": {"mt:dark": .8, "aggressive": .5, "mt:heavy": .4, "mt:dramatic": .3, "mt:drama": .2, "valence": -.4},
}

# Claude's picks count by how they were made: knowing the song, researching it (by how clear the sources were), or a guess
CLAUDE_WEIGHT = {"knows": 0.55, "researched-high": 0.5, "researched-medium": 0.35, "researched-low": 0.15, "guess": 0.15}
WEIGHTS = {"listeners": 0.30, "sound": 0.20}
ENERGY_CHECK = 15   # flag Energetic in the calmest 15% of clips, Mellow in the liveliest 15%
GENRE_WEIGHTS = {"musicbrainz": 0.5, "listeners": 0.3, "sound": 0.2}
MB_SOURCE_WEIGHT = {"album": 3, "artist": 2, "recording": 1}


def load(path, default):
    return json.load(open(path, encoding='utf-8')) if os.path.exists(path) else default


def norm(d):
    t = sum(v for v in d.values() if v > 0)
    return {k: v / t for k, v in d.items() if v > 0} if t else {}


def top(d, k=3):
    return [m for m, v in sorted(d.items(), key=lambda kv: -kv[1])[:k] if v > 0]


def shares(d, k=3):
    """The k biggest shares of a {name: weight} dict, as [[name, percent]]."""
    t = sum(v for v in d.values() if v > 0) or 1
    return [[n, round(100 * v / t)] for n, v in sorted(d.items(), key=lambda kv: -kv[1])[:k] if v > 0]


def picks_dist(picks):
    return dict(zip(picks, [0.65, 0.35])) if len(picks) > 1 else ({picks[0]: 1.0} if picks else {})


def sound_moods(audio):
    """Each song's sound-model mood shares, scored against the rest of the collection."""
    classes = audio.get('moodthemeClasses', [])
    feats = {}
    for tid, a in audio.get('songs', {}).items():
        f = {k: a[k] for k in ('happy', 'sad', 'relaxed', 'aggressive', 'party', 'valence', 'arousal') if k in a}
        f.update({'mt:' + c: v for c, v in zip(classes, a.get('moodtheme', []))})
        feats[tid] = f
    if len(feats) < 10:
        return {}
    keys = {k for f in feats.values() for k in f}
    stats = {}
    for k in keys:
        v = [f[k] for f in feats.values() if k in f]
        m = sum(v) / len(v)
        sd = math.sqrt(sum((x - m) ** 2 for x in v) / len(v)) or 1
        stats[k] = (m, sd)
    raw = {}
    for tid, f in feats.items():
        raw[tid] = {}
        for mood, w in SOUND.items():
            tot = sum(abs(x) for x in w.values())
            raw[tid][mood] = sum(x * (f[k] - stats[k][0]) / stats[k][1] for k, x in w.items() if k in f) / tot
    # put the moods on the same footing, then turn scores into shares
    out = {}
    mstats = {}
    for mood in MOODS:
        v = [r[mood] for r in raw.values()]
        m = sum(v) / len(v)
        mstats[mood] = (m, math.sqrt(sum((x - m) ** 2 for x in v) / len(v)) or 1)
    for tid, r in raw.items():
        z = {mood: (r[mood] - mstats[mood][0]) / mstats[mood][1] for mood in MOODS}
        e = {mood: math.exp(1.5 * z[mood]) for mood in MOODS}
        t = sum(e.values())
        out[tid] = {mood: e[mood] / t for mood in MOODS}
    return out


def listener_moods(tags):
    w = {}
    for name, count in tags:
        for mood, x in TAG_MOODS.get(name, []):
            w[mood] = w.get(mood, 0) + x * count / 100
    total = sum(w.values())
    return norm(w), min(1.0, total / 0.6)


def main():
    with urllib.request.urlopen(urllib.request.Request(SONGS_URL, headers={'User-Agent': 'SongOfTheDayCalendar/1.0'}),
                                timeout=60) as r:
        songs = json.loads(r.read())['songs']
    audio = load(os.path.join(DATA, 'audio.json'), {})
    mbz = load(os.path.join(DATA, 'musicbrainz.json'), {}).get('songs', {})
    claude = load(os.path.join(DATA, 'claude.json'), {}).get('songs', {})
    john = load(os.path.join(DATA, 'john.json'), {})
    lfm = load(LASTFM, {})
    sound = sound_moods(audio)
    # how lively each clip sounds against the rest (DEAM arousal, the dimension audio models judge best)
    ar = sorted(a['arousal'] for a in audio.get('songs', {}).values() if 'arousal' in a)
    energy_pct = {tid: round(100 * sum(1 for v in ar if v <= a['arousal']) / len(ar))
                  for tid, a in audio.get('songs', {}).items() if 'arousal' in a} if ar else {}
    out, stats = {}, Counter()

    for s in songs:
        tid = s['id']
        rec = {'n': s['n'], 'title': s['title'], 'artist': s['artist']}
        c = claude.get(tid, {})
        j = john.get(tid, {})
        a = audio.get('songs', {}).get(tid)
        lf = lfm.get(tid, {})

        # ---------- moods ----------
        voices = []   # (weight, shares)
        basis = c.get('basis') or ('knows' if c.get('moodSure') else 'guess')
        # Claude knows what the song is about, or checked it thoroughly (the recheck of 23 Oct 2026): its picks stand
        solid = bool(c.get('moods')) and (basis in ('knows', 'researched-high') or bool(c.get('checked')))
        if c.get('moods'):
            voices.append((CLAUDE_WEIGHT[basis], picks_dist(c['moods']), 'Claude'))
        lmoods, rel = listener_moods(lf.get('track', []))
        stats['listener tags'] += bool(lf.get('track'))
        stats['listener mood tags'] += bool(lmoods)
        stats['listener mood tags strong enough'] += bool(lmoods and rel >= 0.15)
        if lmoods and rel >= 0.15:
            voices.append((WEIGHTS['listeners'] * rel, lmoods, 'Last.fm'))
        # Last.fm has track tags for few of these songs. The artist's tags were tried as a stand-in, but backed
        # Claude's pick less often than chance, so they are only measured (see agreement), not used.
        amoods, arel = listener_moods(lf.get('artist', []))
        if tid in sound:
            voices.append((WEIGHTS['sound'], sound[tid], 'Audio'))
        mix = {}
        for w, d, _ in voices:
            for m, x in d.items():
                mix[m] = mix.get(m, 0) + w * x
        ranked = top(mix, 10)
        moods = ranked[:1] + [m for m in ranked[1:2] if mix[m] >= 0.5 * mix[ranked[0]]] if ranked else []
        if solid:
            moods = c['moods'][:2]   # Claude knows or has read what the song is about: its picks stand as given
        rec['moods'] = moods
        if voices and not solid:   # show the working: each voice's weight and leanings, and the result
            vw = sum(w for w, _, _ in voices)
            rec['moodMix'] = shares(mix)
            rec['moodEvidence'] = [[label, round(100 * w / vw), shares(d)] for w, d, label in voices]
        rec['moodFrom'] = 'Claude' if solid and basis == 'knows' else 'Claude (researched)' if solid else 'Weighed up'
        if tid in sound:
            rec['sound'] = top(sound[tid], 3)
        if c.get('moods'):
            rec['claude'] = c['moods']
            rec['claudeBasis'] = basis
            if c.get('why'):
                rec['claudeWhy'] = c['why']
            if c.get('source'):
                rec['claudeSource'] = c['source']
        lis_top = top(lmoods, 3) if lmoods and rel >= 0.15 else []
        rec['listeners'] = 'none' if not lis_top else ('agree' if moods and moods[0] in lis_top else 'differ')

        # do the voices back the first mood?
        first = moods[0] if moods else None
        if c.get('checked'):
            check = False   # checked thoroughly, song by song
        elif solid:
            # the audio models alone are too unreliable to overrule Claude; it takes listeners and audio both disagreeing
            check = bool(lis_top) and first not in lis_top and first not in rec.get('sound', [first])
        elif c.get('moods') and basis == 'researched-medium':
            check = tid in sound and first not in rec['sound'] and first not in lis_top
        else:
            check = True   # nothing firmer than a guess or the audio models
        # ---------- genres ----------
        parts, mb_seen = [], []
        m = mbz.get(tid, {})
        mbmix, wsum = {}, 0
        for src in ('album', 'artist', 'recording'):
            d = dist(m.get(src, []))
            if d:
                mb_seen += [t for t, _ in m.get(src, [])]
                wsum += MB_SOURCE_WEIGHT[src]
                for b, x in d.items():
                    mbmix[b] = mbmix.get(b, 0) + MB_SOURCE_WEIGHT[src] * x
        if wsum:
            parts.append((GENRE_WEIGHTS['musicbrainz'], {b: x / wsum for b, x in mbmix.items()}, 'MusicBrainz'))
        lg = dist(lf.get('track', []) + [[t, cnt / 2] for t, cnt in lf.get('artist', [])])
        if lg:
            parts.append((GENRE_WEIGHTS['listeners'], lg, 'Last.fm'))
        if a and a.get('styles'):
            sg = {}
            for label, x in a['styles']:
                b = discogs_bucket(label)
                if b:
                    sg[b] = sg.get(b, 0) + x
            if sg:
                parts.append((GENRE_WEIGHTS['sound'], norm(sg), 'Audio'))
            rec['styles'] = [l.split('---')[-1] for l, _ in a['styles'][:3]]
        gmix, gw = {}, sum(w for w, _, _ in parts)
        for w, d, _ in parts:
            for b, x in d.items():
                gmix[b] = gmix.get(b, 0) + w * x / gw
        picked = top(gmix, 1) + [b for b in top(gmix, 2)[1:] if gmix[b] >= 0.25]   # the top genre, plus a second at 25% or more
        if c.get('genres'):
            rec['genres'], rec['genreFrom'] = c['genres'], 'Claude'
        elif picked:
            rec['genres'], rec['genreFrom'] = picked, 'Weighed up'
        elif c.get('genresIfNone'):
            rec['genres'], rec['genreFrom'] = c['genresIfNone'], 'Claude'
        else:
            rec['genres'], rec['genreFrom'] = [], 'none'
        if parts:   # show the working: each source's weight and leanings, and the combined shares
            rec['genreMix'] = shares(gmix)
            rec['genreEvidence'] = [[label, round(100 * w / gw), shares(d)] for w, d, label in parts]
        if mb_seen:
            rec['musicbrainz'] = sorted(set(t for t in mb_seen if bucket(t)))[:10]

        # ---------- John's corrections win ----------
        if j.get('moods'):
            rec['moods'], rec['moodFrom'] = [x for x in j['moods'] if x in MOODS][:2], 'John'
        if j.get('genres'):
            rec['genres'], rec['genreFrom'] = [x for x in j['genres'] if x in GENRES][:2], 'John'
        # however a mood was picked, the audio can still catch an Energetic song that sounds calm, or the reverse
        why = []
        pc = energy_pct.get(tid)
        if pc is not None and rec['moodFrom'] != 'John':
            if 'Energetic' in rec['moods'] and pc <= ENERGY_CHECK:
                why.append(f'Energetic, but the audio sounds calmer than {100 - pc}% of the songs')
            if 'Mellow' in rec['moods'] and pc >= 100 - ENERGY_CHECK:
                why.append(f'Mellow, but the audio sounds livelier than {pc}% of the songs')
        if why:
            rec['checkWhy'] = '; '.join(why)
        if (check or why) and rec['moodFrom'] != 'John':
            rec['check'] = True
        out[tid] = rec
        if solid:   # how often each other voice backs Claude's first pick, as a running check on them
            if lis_top:
                stats['listeners heard'] += 1
                stats['listeners back Claude'] += c['moods'][0] in lis_top
            if amoods and arel >= 0.15:
                stats['artist tags heard'] += 1
                stats['artist tags back Claude'] += c['moods'][0] in top(amoods, 3)
            if tid in sound:
                stats['audio heard'] += 1
                stats['audio backs Claude'] += c['moods'][0] in rec['sound']
        stats['voices %d' % len(voices)] += 1
        stats['listeners ' + rec['listeners']] += 1

    tags = {
        'about': ("Moods and broad genres for each song on John's Song of the Day. Each song gets up to two of ten moods, "
                  "weighed up from Claude's picks (songs Claude knows or has read about, sound and lyrics), Last.fm listener tags, and "
                  "Essentia's mood models listening to the 30-second preview (Music Technology Group, Universitat Pompeu "
                  "Fabra, CC BY-NC-SA 4.0). sound = the three moods the audio models hear most; listeners = whether "
                  "Last.fm listeners' tags back the first mood; check = nothing firmer than a guess or the audio "
                  "models, or listeners and audio both disagree with Claude. Genres come from MusicBrainz, Last.fm and Essentia's Discogs styles. John's "
                  "corrections from the admin page always win."),
        'agreement': {
            'about': "For songs Claude knows: how often the three strongest moods from Last.fm listeners' track tags, "
                     "from the artist's Last.fm tags, and from the audio models include Claude's first pick.",
            'listeners': [stats['listeners back Claude'], stats['listeners heard']],
            'audio': [stats['audio backs Claude'], stats['audio heard']],
            'artistTags': [stats['artist tags back Claude'], stats['artist tags heard']],
            'songsWithListenerTags': stats['listener tags'],
            'songsWithListenerMoodTags': stats['listener mood tags'],
            'songsWhereListenersCount': stats['listener mood tags strong enough'],
        },
        'moods': MOODS, 'moodInfo': MOOD_INFO, 'genres': GENRES, 'songs': out,
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(tags, f, ensure_ascii=False, indent=1)
    print('songs', len(out), dict(stats))
    print('first moods', Counter(r['moods'][0] for r in out.values() if r['moods']).most_common())
    print('all moods', Counter(m for r in out.values() for m in r['moods']).most_common())
    print('genres', Counter(g for r in out.values() for g in r['genres']).most_common())
    print('to check', sum(1 for r in out.values() if r.get('check')))


if __name__ == '__main__':
    main()
