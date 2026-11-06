#!/usr/bin/env python3
"""Lists the songs that still need facts written, for the scheduled facts task (see _sotd/FACTS.md).

    python3 _sotd/facts_todo.py OUTDIR

A song needs facts when it isn't in the live facts at all (one there with none found isn't tried again) and has none
waiting for John; a song from a member's public playlist (upl in sources.json) when it's not in
_sotd/data/upl-facts.json yet (written once; its owner checks them). For each, writes into OUTDIR:
todo.md (each song with its album, year, credited artists, MusicBrainz details and the saved Wikipedia pages to read)
and pages/*.txt (those pages' text). Prints how many songs need facts; 0 means nothing to do.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load(p, empty):
    try:
        return json.load(open(p, encoding='utf-8'))
    except FileNotFoundError:
        return empty


def slug(t):
    return re.sub(r'[^A-Za-z0-9]+', '_', t).strip('_')[:80]


def mbline(m):
    if not m:
        return ''
    parts = ['%s: %s' % (lab, m[k]) for k, lab in [('type', 'type'), ('gender', 'gender'), ('country', 'country'), ('beginArea', 'from'),
                                                   ('begin', 'started'), ('end', 'ended'), ('disambiguation', 'note')] if m.get(k)]
    if m.get('members'):
        parts.append('members: ' + '; '.join(x['name'] + (' (' + ', '.join(x['roles']) + ')' if x.get('roles') else '') for x in m['members']))
    return ' · '.join(parts) + (' (%s)' % m['url'] if m.get('url') else '')


def main(out):
    S = load(os.path.join(HERE, 'data', 'sources.json'), {'songs': {}, 'artists': {}, 'pages': {}})
    live = load(os.path.join(ROOT, 'songs', 'facts.json'), {'songs': {}, 'artists': {}})
    pending = load(os.path.join(HERE, 'data', 'facts-pending.json'), {'songs': {}})
    years = load(os.path.join(HERE, 'data', 'years.json'), {'songs': {}})['songs']
    theirs = load(os.path.join(HERE, 'data', 'upl-facts.json'), {'songs': {}})['songs']

    redo = load(os.path.join(HERE, 'data', 'facts-redo.json'), {})   # (out already, asked for again by John: facts_redo.py)

    def has_live(tid):   # on the calendar already, even with no facts (nothing could be found): not tried again, unless John asks
        return (tid in live['songs'] or tid in live.get('signed', {})) and tid not in redo
    todo = sorted((s for tid, s in S['songs'].items() if (tid not in theirs if s.get('upl') else not has_live(tid) and tid not in pending['songs'])), key=lambda s: s['n'])
    os.makedirs(os.path.join(out, 'pages'), exist_ok=True)
    lines = []
    for s in todo:
        tid = next(k for k, v in S['songs'].items() if v is s)
        y = years.get(tid, {})
        yr = min([v for v in (y.get('mb'), y.get('sp')) if v] or ['?'])
        lines.append('## Song %d: “%s” by %s   [track: %s]' % (s['n'], s['title'], s['artist'], tid) + ('   (a member’s playlist’s: its owner checks them)' if s.get('upl') else ''))
        lines.append('Album (Spotify): %s · first released: %s' % (s.get('album'), yr))
        rd = redo.get(tid)
        if isinstance(rd, dict) and (rd.get('avoid') or rd.get('avoidSrc')):   # (asked for again: none of the facts it had, not from the same pages)
            if rd.get('mode') == 'song':
                lines.append('**Asked for again by John: more facts about the song itself, 3 to 5 new ones. Its artist facts are kept, so write none for the artist. None of the facts below, nothing close to them; look beyond the pages listed: interviews, books, liner notes and articles about the song (see FACTS.md).**')
            else:
                lines.append('**Asked for again by John: the facts below weren\'t good enough. Write new ones: none of these, nothing close to them, and not from the pages listed; find interviews, books, liner notes and articles (see FACTS.md).**')
            lines.extend('- not: ' + t for t in rd.get('avoid') or [])
            lines.extend('- not from: ' + u for u in rd.get('avoidSrc') or [])
        saved = []
        for label, t in (('song article', s.get('song')), ('album article', s.get('albumPage'))):
            if t in S['pages']:
                fn = 'pages/' + slug(t) + '.txt'
                p = S['pages'][t]
                open(os.path.join(out, fn), 'w', encoding='utf-8').write('%s\n%s (revision %s)\n\n%s\n' % (p['title'], p['url'], p.get('rev'), p['text']))
                saved.append('%s: %s (%s)' % (label, fn, p['url']))
        lines.append('Saved Wikipedia: ' + ('; '.join(saved) if saved else 'none on the song or album'))
        for k in s['artists']:
            a = S['artists'].get(k, {})
            lines.append('### Artist: %s   [artist: %s]' % (a.get('name'), k))
            if a.get('mb'):
                lines.append('MusicBrainz: ' + mbline(a['mb']))
            if a.get('wiki') in S['pages']:
                p = S['pages'][a['wiki']]
                fn = 'pages/' + slug(a['wiki']) + '.txt'
                open(os.path.join(out, fn), 'w', encoding='utf-8').write('%s\n%s (revision %s)\n\n%s\n' % (p['title'], p['url'], p.get('rev'), p['text']))
                lines.append('Saved Wikipedia: artist article: %s (%s)' % (fn, p['url']))
            else:
                lines.append('Saved Wikipedia: none')
        lines.append('')
    open(os.path.join(out, 'todo.md'), 'w', encoding='utf-8').write('\n'.join(lines))
    print(len(todo), 'songs need facts' + (': ' + ', '.join('%d %s' % (s['n'], s['title']) for s in todo) if todo else ''))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
