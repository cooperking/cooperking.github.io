"""Which songs are out on the live calendar.

A new song is held back from the live calendar, the share pages and the YouTube playlist until its facts are written
and checked (John, 24 Oct 2026: it can go live before he signs them off; he checks and edits them afterwards on
songs/facts-admin.html). By then song-tags.yml has looked for its video too: the same run that saves the sources the
facts are written from. A song is out once it's in songs/facts.json, where facts_apply.py puts checked facts straight
away. The draft calendar (songs/draft/) shows every song, out or not.
"""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def released():
    """The track ids that are out, or None if there's no songs/facts.json (then everything is)."""
    try:
        with open(os.path.join(ROOT, 'songs', 'facts.json'), encoding='utf-8') as f:
            j = json.load(f)
    except FileNotFoundError:
        return None
    return set(j.get('songs', {})) | set(j.get('signed', {}))
