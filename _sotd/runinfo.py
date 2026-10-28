"""The GitHub job run a script is part of, kept with what it did so the admin page's Status tab can say which run did
each step of a new song (John, 28 Oct 2026: "I need to know what job ran for it to get those ticks").

    {'id': GITHUB_RUN_ID, 'n': GITHUB_RUN_NUMBER, 'job': GITHUB_WORKFLOW, 'at': now (UTC)}, or None outside GitHub
"""
import datetime, os


def this_run():
    i = os.environ.get('GITHUB_RUN_ID')
    if not i or not i.isdigit():
        return None
    n = os.environ.get('GITHUB_RUN_NUMBER') or '0'
    return {'id': int(i), 'n': int(n) if n.isdigit() else 0, 'job': os.environ.get('GITHUB_WORKFLOW', ''),
            'at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}
