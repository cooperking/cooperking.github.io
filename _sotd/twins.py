"""The same song on Spotify under another track id: a remaster, a compilation's copy, a single and its album's
(John, 1 Nov 2026: "it's odd what it can't find sometimes like Werewolves of London, which is an extremely famous
song" — John's copy had its year and moods; a member's other release of it had neither). Two tracks are the same song
when their titles match with every bracketed part and anything after " - " taken off ("Werewolves of London - 2007
Remaster"), and their first artists match, both folded (case, accents, punctuation). Used by years.py and
make_tags.py, so a song takes what's known for its twin instead of waiting for (or missing) its own lookups."""
import re, unicodedata


def fold(x):
    x = re.sub(r"['’`]", '', x or '')
    x = unicodedata.normalize('NFKD', x).encode('ascii', 'ignore').decode().lower().replace('&', ' and ')
    return re.sub(r'[^a-z0-9]+', ' ', x).strip()


def key(s):
    """The song's key: (folded title without brackets or " - " parts, folded first artist), or None."""
    t = re.sub(r'\s+-\s+.*$', '', s.get('title') or '')
    t = re.sub(r'\s*[(\[][^)\]]*[)\]]', '', t).strip()
    a = (s.get('artists') or [s.get('artist') or ''])[0]
    a = re.split(r',\s*', a)[0]
    t, a = fold(t).replace(' ', ''), fold(a).replace(' ', '')   # (spaces don't count: Mother Fucking and Motherfucking are the same song, John, 4 Nov 2026)
    return (t, a) if t and a else None


def finder(songs, has):
    """twin(track id) -> another track id of the same song for which has(id) is true (John's songs first), or None."""
    by = {}
    john = {s['id'] for s in songs if not s.get('upl')}
    for s in songs:
        k = key(s)
        if k:
            by.setdefault(k, []).append(s['id'])
    keys = {s['id']: key(s) for s in songs}

    def twin(tid):
        ids = by.get(keys.get(tid)) or []
        for t in sorted(ids, key=lambda t: t not in john):
            if t != tid and has(t):
                return t
        return None
    return twin
