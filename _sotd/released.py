"""Which songs are out on the live calendar.

A new song is held back from the live calendar, the share pages and the YouTube playlist until John signs it off on the
Songs tab of songs/admin.html (John, 28 Oct 2026: he checks its facts and adds his own song and video for the members
first; from 24 to 28 Oct its checked facts put it out straight away). A song is out once it's in songs/facts.json, where
facts_apply.py puts what he signs off. The draft calendar (songs/draft/) shows the ones waiting for him as well.
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
