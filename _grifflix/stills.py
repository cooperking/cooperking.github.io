#!/usr/bin/env python3
"""Grifflix's share stills: a day's (its month's theme with the day's film at the front), a month's (its theme with its films)
and Grifflix's own (the zoetrope), drawn from the site signed out, in a browser (laid out at 1600 x 840, saved at 1200 x 630), and put where the Worker keeps
the share cards (R2: grifflix/cards/<key>.jpg), over any card a member's page drew. Run by .github/workflows/grifflix-stills.yml.

  python3 _grifflix/stills.py              those not drawn yet, and today's, this month's and Grifflix's own again
  python3 _grifflix/stills.py --all        every one again
  python3 _grifflix/stills.py --out DIR    today's, this month's and Grifflix's own drawn into DIR as files, nothing sent
  python3 _grifflix/stills.py --page /draft/   from another page of the site (the live page, /, otherwise)
  python3 _grifflix/stills.py --only 2026-11-10,2026-11,cal   just these

A day is drawn once it's shown on Grifflix (from 8am London on the day, as the Worker has it). A still is sent only when the
page drew it as a still (window.__still set): a page without the still mode is never sent.
Needs R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET and CLOUDFLARE_ACCOUNT_ID (as _sotd/video_tv.py), except with --out.
"""
import argparse, asyncio, datetime, json, os, sys, urllib.request
from zoneinfo import ZoneInfo

BASE = os.environ.get('GRIFFLIX_BASE', 'https://grifflix.co.uk')
LIST = 'grifflix/cards/stills.json'   # the stills drawn: { key: { "at": time, "v": the page's version } }


def london():
    return datetime.datetime.now(ZoneInfo('Europe/London'))


def shown(day, now):
    t = now.strftime('%Y-%m-%d')
    return day < t or (day == t and now.hour >= 8)


def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'grifflix-stills'}), timeout=60) as r:
        return json.loads(r.read().decode('utf-8'))


def r2():
    import boto3
    acct = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '').strip()
    return boto3.client('s3', endpoint_url=os.environ.get('R2_ENDPOINT') or 'https://%s.r2.cloudflarestorage.com' % acct,
                        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'], aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'], region_name='auto')


def url_for(key, page):
    p = BASE + page
    if key == 'cal':
        return p + '?still=site&theme=picture'
    if len(key) == 7:
        return p + '?still=month#month/' + key
    return p + '?still=day#day/' + key


async def draw(browser, key, page_path):
    """The still as JPEG bytes and what the page says of it, or None when the page didn't draw it as a still."""
    ctx = await browser.new_context(viewport={'width': 1600, 'height': 840}, device_scale_factor=0.75)   # (laid out as a computer's screen, saved at 1200 x 630)
    page = await ctx.new_page()
    try:
        await page.goto(url_for(key, page_path), wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_function("() => window.__still && document.querySelector('#cal .thstage, #cal .tring')", timeout=40000)
        try:
            await page.wait_for_load_state('networkidle', timeout=15000)
        except Exception:
            pass
        await page.evaluate("() => document.fonts ? document.fonts.ready.then(() => 1) : 1")
        await page.wait_for_timeout(2500)
        info = await page.evaluate('() => window.__still')
        want = 'cal' if key == 'cal' else key
        if not info or info.get('key') != want:
            print('  %s: the page drew %r, not this still' % (key, info and info.get('key')))
            return None, None
        return await page.screenshot(type='jpeg', quality=86), info
    except Exception as e:
        print('  %s: not drawn (%s)' % (key, str(e).splitlines()[0][:120]))
        return None, None
    finally:
        await ctx.close()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--out')
    ap.add_argument('--page', default='/')
    ap.add_argument('--only', default='')
    a = ap.parse_args()

    now = london(); today = now.strftime('%Y-%m-%d'); month = today[:7]
    days = sorted(d for d, it in get_json(BASE + '/grifflix/data/days.json').get('days', {}).items()
                  if len(d) == 10 and shown(d, now) and not (it or {}).get('secret'))
    months = sorted({d[:7] for d in days})
    every = days + months + ['cal']
    try:
        ver = get_json(BASE + ('/grifflix/draft/' if 'draft' in a.page else '/grifflix/') + 'version.json').get('v', '')
    except Exception:
        ver = ''

    s3 = None if a.out else r2()
    bucket = os.environ.get('R2_BUCKET', '').strip()
    done = {}
    if s3:
        try:
            done = json.loads(s3.get_object(Bucket=bucket, Key=LIST)['Body'].read())
        except Exception:
            done = {}

    if a.only:
        todo = [k.strip() for k in a.only.split(',') if k.strip() in every]
    elif a.all:
        todo = every
    elif a.out:
        todo = [k for k in (today, month, 'cal') if k in every]
    else:
        todo = [k for k in every if k not in done] + [k for k in (today, month, 'cal') if k in every]
    todo = list(dict.fromkeys(todo))
    print('%d shown days, %d months; drawing %d (page %s, version %s)' % (len(days), len(months), len(todo), a.page, ver or '?'))

    from playwright.async_api import async_playwright
    sent = 0; failed = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        sem = asyncio.Semaphore(4)
        probe = await draw(browser, 'cal', a.page)   # (a page without the still mode draws none: nothing to do till it's live)
        if not probe[0]:
            print('The page drew no still: it has no still mode yet (the draft put live), or it isn\'t answering. Nothing sent.')
            await browser.close(); return

        async def one(key):
            nonlocal sent
            if key == 'cal':
                jpg, info = probe
            else:
                async with sem:
                    jpg, info = await draw(browser, key, a.page)
            if not jpg:
                failed.append(key); return
            if a.out:
                os.makedirs(a.out, exist_ok=True)
                open(os.path.join(a.out, key + '.jpg'), 'wb').write(jpg)
            else:
                meta = {'v2': '1', 'still': '1', 'bg': info.get('bg', ''), 'ink': info.get('ink', '')}
                if len(key) == 7 and info.get('name'):
                    meta['name'] = info['name']
                s3.put_object(Bucket=bucket, Key='grifflix/cards/%s.jpg' % key, Body=jpg, ContentType='image/jpeg', Metadata=meta)
                done[key] = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), 'v': ver}
            sent += 1
            print('  %s: %d KB' % (key, len(jpg) // 1024))

        await asyncio.gather(*(one(k) for k in todo))
        await browser.close()

    if s3 and sent:
        s3.put_object(Bucket=bucket, Key=LIST, Body=json.dumps(done, indent=1, sort_keys=True).encode(), ContentType='application/json')
    print('%d drawn%s; %d not: %s' % (sent, ' into ' + a.out if a.out else ' and sent', len(failed), ', '.join(failed[:20])))
    if failed and not sent:
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
