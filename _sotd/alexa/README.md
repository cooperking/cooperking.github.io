# John's Song of the Day: the Alexa skill

Plays John's own recordings (the MP3s on the Songs admin tab) on an Echo, from the Worker's `/alexa` (see the Alexa
section of `_sotd/worker/worker.js`). Nothing to host: the skill's only setting outside this repository is in Amazon's
developer console, made once:

1. developer.amazon.com/alexa/console/ask, signed in with the Amazon account the Echo uses. **Create Skill**: name
   *John's Song of the Day*, locale **English (UK)**, type **Other** / **Custom**, hosting **Provision your own**,
   template **Start from scratch**.
2. **Build → Interaction Model → JSON Editor**: paste `interaction-model.json`, **Save**, **Build skill**.
3. **Build → Interfaces**: turn on **Audio Player** and **Video App**, **Save**, **Build skill** again.
4. **Build → Endpoint**: **HTTPS**, Default Region `https://sotd-playlists.cooperking.workers.dev/alexa`, certificate
   *My development endpoint is a sub-domain of a domain that has a wildcard certificate from a certificate authority*.
   **Save**.
5. **Test**: set *Skill testing is enabled in* to **Development**. Then: "Alexa, open John's Song of the Day".
6. Family on their own Amazon accounts: **Distribution** (the required boxes), then **Availability → Beta Test**, their
   email addresses. Amazon ends a beta after 90 days; start a new one to carry on.

The first skill that calls `/alexa` is the only one it ever answers (KV `alexaSkill`). A new skill (made again from
scratch) needs that key deleted first.

What it understands: open (today's song, then back a day at a time); play today's song; play the song from {a day};
play the song called {name}; play songs by {artist}; shuffle; next, previous, pause, resume, start over, shuffle on/off,
loop on/off; what's this song (title, artist, year, its day); tell me about this song (one of its facts); play the
video (an Echo Show: John's own video). Only songs with John's MP3 play; a day without one gives the one before.
