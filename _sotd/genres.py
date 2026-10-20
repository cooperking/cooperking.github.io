"""Broad genres: MusicBrainz / Last.fm tag names and Discogs styles -> the nine genres on the calendar."""
import re

GENRES = ['Soul & R&B', 'Rock', 'Indie', 'Folk', 'Pop', 'Electronic', 'Hip-hop', 'Country', 'World']

# genre name -> broad genre. Exact names first, then words inside longer names.
EXACT = {
    'acid rock': 'Rock',
    'acoustic': 'Folk',
    'adult contemporary': 'Pop',
    'afrobeat': 'World',
    'afropop': 'World',
    'alt country': 'Country',
    'alt-country': 'Country',
    'alternative': 'Indie',
    'alternative country': 'Country',
    'alternative dance': 'Electronic',
    'alternative hip hop': 'Hip-hop',
    'alternative pop': 'Indie',
    'alternative rock': 'Indie',
    'ambient': 'Electronic',
    'american folk': 'Folk',
    'americana': 'Folk',
    'anti-folk': 'Folk',
    'aor': 'Pop',
    'arabic': 'World',
    'art pop': 'Pop',
    'art punk': 'Indie',
    'art rock': 'Rock',
    'avant-garde pop': 'Pop',
    'baroque pop': 'Pop',
    'beat music': 'Rock',
    'bedroom pop': 'Indie',
    'blue-eyed soul': 'Soul & R&B',
    'bluegrass': 'Country',
    'blues': 'Soul & R&B',
    'blues rock': 'Rock',
    'boogie': 'Soul & R&B',
    'bossa nova': 'World',
    'breakbeat': 'Electronic',
    'brill building': 'Pop',
    'british blues': 'Rock',
    'british folk': 'Folk',
    'british rhythm & blues': 'Rock',
    'britpop': 'Indie',
    'bubblegum': 'Pop',
    'chamber folk': 'Folk',
    'chamber pop': 'Pop',
    'chanson': 'World',
    'chillwave': 'Indie',
    'city pop': 'Pop',
    'classic rock': 'Rock',
    'conscious hip hop': 'Hip-hop',
    'contemporary folk': 'Folk',
    'contemporary r&b': 'Soul & R&B',
    'cosmic american music': 'Country',
    'country': 'Country',
    'country pop': 'Country',
    'country rock': 'Country',
    'cumbia': 'World',
    'dance': 'Electronic',
    'dance-pop': 'Pop',
    'dance-punk': 'Indie',
    'dark wave': 'Electronic',
    'darkwave': 'Electronic',
    'disco': 'Soul & R&B',
    'doo-wop': 'Soul & R&B',
    'downtempo': 'Electronic',
    'dream pop': 'Indie',
    'drum and bass': 'Electronic',
    'dub': 'World',
    'dubstep': 'Electronic',
    'easy listening': 'Pop',
    'ebm': 'Electronic',
    'edm': 'Electronic',
    'electro': 'Electronic',
    'electro house': 'Electronic',
    'electronic': 'Electronic',
    'electronica': 'Electronic',
    'electropop': 'Pop',
    'emo': 'Indie',
    'ethio-jazz': 'World',
    'exotica': 'World',
    'folk': 'Folk',
    'folk pop': 'Folk',
    'folk rock': 'Folk',
    'folktronica': 'Folk',
    'freak folk': 'Folk',
    'french electro': 'Electronic',
    'french house': 'Electronic',
    'french pop': 'World',
    'funk': 'Soul & R&B',
    'funk pop': 'Pop',
    'future bass': 'Electronic',
    'garage rock': 'Rock',
    'garage rock revival': 'Indie',
    'glam rock': 'Rock',
    'gospel': 'Soul & R&B',
    'grunge': 'Rock',
    'hard rock': 'Rock',
    'hardcore': 'Rock',
    'hardcore punk': 'Rock',
    'heartland rock': 'Rock',
    'heavy metal': 'Rock',
    'highlife': 'World',
    'hip hop': 'Hip-hop',
    'hip-hop': 'Hip-hop',
    'honky tonk': 'Country',
    'house': 'Electronic',
    'hypnagogic pop': 'Pop',
    'idm': 'Electronic',
    'indie': 'Indie',
    'indie folk': 'Folk',
    'indie pop': 'Indie',
    'indie rock': 'Indie',
    'indietronica': 'Indie',
    'instrumental pop': 'Pop',
    'italo-disco': 'Electronic',
    'jam band': 'Rock',
    'jangle pop': 'Indie',
    'jazz pop': 'Pop',
    'jazz-funk': 'Soul & R&B',
    'krautrock': 'Rock',
    'kwaito': 'World',
    'latin': 'World',
    'lo-fi': 'Indie',
    'lo-fi indie': 'Indie',
    'lounge': 'Pop',
    'mainstream rock': 'Rock',
    'mambo': 'World',
    'marabi': 'World',
    'math rock': 'Indie',
    'merseybeat': 'Rock',
    'metal': 'Rock',
    'mod': 'Rock',
    'motown': 'Soul & R&B',
    'mpb': 'World',
    'neo soul': 'Soul & R&B',
    'neo-psychedelia': 'Indie',
    'new rave': 'Electronic',
    'new romantic': 'Pop',
    'new wave': 'Rock',
    'noise pop': 'Indie',
    'noise rock': 'Indie',
    'northern soul': 'Soul & R&B',
    'nu-disco': 'Electronic',
    'outlaw country': 'Country',
    'plunderphonics': 'Electronic',
    'pop': 'Pop',
    'pop rock': 'Pop',
    'pop soul': 'Pop',
    'post-hardcore': 'Rock',
    'post-punk': 'Rock',
    'post-punk 2k': 'Indie',
    'post-punk revival': 'Indie',
    'post-rock': 'Indie',
    'power pop': 'Pop',
    'progressive pop': 'Pop',
    'progressive rock': 'Rock',
    'proto-punk': 'Rock',
    'psychedelic folk': 'Folk',
    'psychedelic pop': 'Pop',
    'psychedelic rock': 'Rock',
    'psychedelic soul': 'Soul & R&B',
    'pub rock': 'Rock',
    'punk': 'Rock',
    'punk rock': 'Rock',
    'quiet storm': 'Soul & R&B',
    'r&b': 'Soul & R&B',
    'rap': 'Hip-hop',
    'rave': 'Electronic',
    'raï': 'World',
    'reggae': 'World',
    'rhythm & blues': 'Soul & R&B',
    'rock': 'Rock',
    'rock and roll': 'Rock',
    'rockabilly': 'Rock',
    'roots rock': 'Rock',
    'samba': 'World',
    'shoegaze': 'Indie',
    'singer-songwriter': 'Folk',
    'singer/songwriter': 'Folk',
    'ska': 'World',
    'slacker rock': 'Indie',
    'slowcore': 'Indie',
    'soft rock': 'Pop',
    'sophisti-pop': 'Pop',
    'soul': 'Soul & R&B',
    'soul jazz': 'Soul & R&B',
    'southern rock': 'Rock',
    'space rock': 'Rock',
    'sunshine pop': 'Pop',
    'surf rock': 'Rock',
    'synth-pop': 'Pop',
    'synthpop': 'Pop',
    'synthwave': 'Electronic',
    'techno': 'Electronic',
    'township bubblegum': 'World',
    'traditional pop': 'Pop',
    'trip hop': 'Electronic',
    'tropicália': 'World',
    'twee pop': 'Indie',
    'vocal jazz': 'Pop',
    'yacht rock': 'Pop',
    'zouk': 'World',
}
# tags that name no genre, or are too vague to count
IGNORE = ['avant-garde', 'blues', 'classic pop and rock', 'comedy', 'experimental', 'film soundtrack', 'leftfield', 'non-music', 'pop/rock', 'rock and indie', 'seen live', 'session', 'singer-songwriter', 'singer/songwriter', 'soundtrack']
IGNORE = set(IGNORE)
WORDS = [('hip hop', 'Hip-hop'), ('rap', 'Hip-hop'), ('folk', 'Folk'), ('country', 'Country'), ('soul', 'Soul & R&B'), ('funk', 'Soul & R&B'), ('r&b', 'Soul & R&B'), ('blues', 'Soul & R&B'), ('disco', 'Soul & R&B'), ('house', 'Electronic'), ('techno', 'Electronic'), ('electro', 'Electronic'), ('ambient', 'Electronic'), ('indie', 'Indie'), ('punk', 'Rock'), ('rock', 'Rock'), ('pop', 'Pop')]


def bucket(name):
    n = name.lower().strip()
    if n in IGNORE:
        return None
    if n in EXACT:
        return EXACT[n]
    for w, b in WORDS:
        if re.search(r"\b" + re.escape(w), n):
            return b
    return None


def dist(pairs):
    """Share of each broad genre in a list of (tag, votes); voted-down tags don't count."""
    w = {}
    for g, votes in pairs:
        if votes is not None and votes <= 0:
            continue
        b = bucket(g)
        if b:
            w[b] = w.get(b, 0) + max(1, votes)
    tot = sum(w.values())
    return {b: x / tot for b, x in w.items()} if tot else {}


# Discogs top-level genres (the part before "---" in a style name), when the style itself names no genre
DISCOGS_PARENT = {"Rock": "Rock", "Pop": "Pop", "Electronic": "Electronic", "Hip Hop": "Hip-hop", "Funk / Soul": "Soul & R&B",
                  "Latin": "World", "Reggae": "World", "Folk, World, & Country": "World"}
DISCOGS_STYLE = {"country": "Country", "bluegrass": "Country", "honky tonk": "Country", "folk": "Folk", "folk rock": "Folk",
                 "indie rock": "Indie", "indie pop": "Indie", "lo-fi": "Indie", "shoegaze": "Indie", "dream pop": "Indie",
                 "post rock": "Indie", "emo": "Indie", "synth-pop": "Pop", "soft rock": "Pop", "pop rock": "Pop",
                 "vocal": "Pop", "ballad": "Pop", "disco": "Soul & R&B", "gospel": "Soul & R&B", "rhythm & blues": "Soul & R&B",
                 "country rock": "Country", "acoustic": "Folk", "chanson": "World"}


def discogs_bucket(label):
    parent, _, style = label.partition("---")
    s = style.lower().strip()
    if s in DISCOGS_STYLE:
        return DISCOGS_STYLE[s]
    if parent == "Folk, World, & Country":
        return bucket(s) if bucket(s) in ("Country", "Folk") else "World"
    if parent in ("Jazz", "Blues", "Classical", "Stage & Screen", "Non-Music", "Children's", "Brass & Military"):
        return None
    return bucket(s) or DISCOGS_PARENT.get(parent)
