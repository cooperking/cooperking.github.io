NAME = "John's Song of the Day"
SHORT = "A song for every day, with its video, its story and a playlist of your own."
FULL = """John's Song of the Day is a calendar of songs: one picked for each day, each with its own page.

• Every day's song: tap a day to hear it, watch its video, or read its story: who wrote it, who played on it and where it came from.
• Play all: every song, newest first or shuffled, playing on while you do other things, with the lock screen and headphone buttons.
• My playlist: add songs with the heart, then play it here or open it in Spotify or YouTube.
• Search by song, artist, year, mood or genre.
• Play on a speaker: Chromecasts and Google Cast speakers.
• Home Screen widgets: today's song, Play (play all, shuffle or My playlist), and a calendar of the month's covers. Also a Quick Settings tile, and shortcuts on the app's icon.
• Members John invites also hear his own recordings and videos, carry on on another device from where they left off, download songs to play with no connection, and get a notification as each new song goes up.

No tracking, and no adverts of its own (YouTube's player may show its own in some videos)."""
for k, v, lim in (('name', NAME, 30), ('short', SHORT, 80), ('full', FULL, 4000)):
    print(k, len(v), '/', lim, 'OK' if len(v) <= lim else 'TOO LONG')
