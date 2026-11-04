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


def names(s):
    """Every artist credited, folded, spaces not counting ('Marc Bolan & T. Rex' -> {'marcbolan', 'trex'})."""
    out = set()
    for a in (s.get('artists') or [s.get('artist') or '']):
        for part in re.split(r',\s*|\s+(?:&|and|x|feat\.?|featuring|with)\s+', a or '', flags=re.I):
            f = fold(part).replace(' ', '')
            if f:
                out.add(f)
    return out


def tkey(s):
    """The title alone, as key() folds it."""
    t = re.sub(r'\s+-\s+.*$', '', s.get('title') or '')
    t = re.sub(r'\s*[(\[][^)\]]*[)\]]', '', t).strip()
    return fold(t).replace(' ', '') or None


def same_artist(a, b):   # any artist credited on both (John, 4 Nov 2026: Ride a White Swan by Marc Bolan & T. Rex is T. Rex's)
    return bool(a & b) or any(len(x) >= 4 and len(y) >= 4 and (x in y or y in x) for x in a for y in b)


def finder(songs, has):
    """twin(track id) -> another track id of the same song (its title, with brackets and " - ..." off, and an artist in
    common) for which has(id) is true (John's songs first), or None."""
    by, info = {}, {}
    john = {s['id'] for s in songs if not s.get('upl')}
    for s in songs:
        k = tkey(s)
        if k:
            by.setdefault(k, []).append(s['id'])
            info[s['id']] = (k, names(s))

    def twin(tid):
        k, nm = info.get(tid, (None, set()))
        for t in sorted(by.get(k) or [], key=lambda t: t not in john):
            if t != tid and same_artist(nm, info[t][1]) and has(t):
                return t
        return None
    return twin
