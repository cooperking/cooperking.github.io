/**
 * Emails you when a YouTube playlist listed in the Playlists sheet gets a new video, so an iPhone Email automation
 * can start the download. Every unlisted playlist on your YouTube channel, and every member playlist on the Song of the
 * Day calendar, is added to the sheet automatically; and a row whose playlist is gone comes out of it (a member
 * playlist the Worker no longer lists, or a YouTube playlist that no longer exists), so it leaves the published CSV too
 * (1 Nov 2026).
 *
 * Where it runs: the Apps Script project's Code.gs is Loader.gs (beside this file), which reads this file from the
 * repository's main branch each run and runs its check(); so a change here reaches the project with a commit. (Pasted
 * into the project by itself it works too.)
 *
 * Setup: add the YouTube Data API v3 service, set TO, then run setup() once and allow the permissions it asks for.
 * For members' playlists, the script property sotdKey holds the Song of the Day Worker's admin password.
 * setup() creates the Playlists sheet (its link is in the log) with FIRST_PLAYLIST in it, moves the earlier
 * single-playlist record into the per-playlist record, and runs check() every 5 minutes. Running it again keeps the
 * sheet and the records, rewrites the header row and recreates the trigger.
 *
 * Playlists sheet: A Playlist (a playlist link or ID, or a member playlist's link from the Worker, SOTD/yt/<id>?s=…;
 * one row per playlist), B Title, C Videos, D Status, E Skip.
 * Each check adds a row for every unlisted playlist on your channel, and every member playlist the Worker lists
 * (SOTD/upl/links), that isn't in the sheet yet, and takes out the rows of member playlists the Worker no longer lists
 * and of YouTube playlists that no longer exist. A row with anything in Skip is not watched (and not taken out);
 * clearing a channel or member playlist's row has it added again, so use Skip to stop one.
 * check() fills B when it is empty; the iPhone script names each playlist's folder in SOTD after B.
 * C and D are updated on every full listing.
 *
 * For each playlist, a check asks only for the video count (1 quota unit). The playlist is listed in full (1 unit per
 * 50 videos) when the count has changed, when an hour has passed since its last full listing, and the first time it
 * is watched. Listing your channel's playlists costs 1 unit per 50 playlists. A playlist watched for the first time
 * is announced HOLD_MS later, so the sheet's published CSV includes it by the time the iPhone reads it.
 * A member playlist's songs come from its link with &f=json (no YouTube quota), each check, in full: every song with a
 * video, those still to come on its calendar too, so their files are got ahead of their day.
 * One email per check lists the new videos by playlist, and each newly watched playlist by name and size.
 * Each playlist's video IDs are kept in the script's properties under "<playlist ID>:", split into 8,000-character
 * pieces. Records of playlists no longer watched are deleted.
 *
 * When a song's MP3 or MP4 has been deleted on the admin page since its last look (the Worker's /upl/wanted), it starts
 * that workflow too, so the song gets new files within minutes.
 * Whenever it emails, it also starts the "Download playlists to R2" workflow in GITHUB_REPO with a repository_dispatch
 * event "playlist-new", using the GitHub token in the script property GITHUB_TOKEN. Without that property it only emails.
 */
const TO = 'john.griffiths78@icloud.com';
const SUBJECT = 'Playlist new video';
const FIRST_PLAYLIST = 'PLEQXmr0lDKew';
const SHEET_NAME = 'Playlists';
const HEADERS = ['Playlist', 'Title', 'Videos', 'Status', 'Skip'];
const FULL_EVERY_MS = 60 * 60 * 1000;
const HOLD_MS = 10 * 60 * 1000;
const PIECE = 8000;
const WATCH = 'https://www.youtube.com/watch?v=';
const LIST = 'https://www.youtube.com/playlist?list=';
const SOTD = 'https://sotd-playlists.cooperking.workers.dev';
const GITHUB_REPO = 'cooperking/playlist-r2';

function playlistId(cell) {
  const s = String(cell || '').trim();
  const m = s.match(/[?&]list=([A-Za-z0-9_-]+)/);
  if (m) return m[1];
  return /^[A-Za-z0-9_-]{10,}$/.test(s) ? s : '';
}

/** A member playlist's row: 'm-' and its id on the calendar, or '' for any other cell. */
function memberId(cell) {
  const m = String(cell || '').trim().match(/^https:\/\/sotd-playlists\.cooperking\.workers\.dev\/yt\/([\w-]+)\?s=[\w-]+$/);
  return m ? 'm-' + m[1] : '';
}

/** A member playlist's songs from the Worker: { ids, titles, t } (its video IDs, "Artist – Title" for each, its name). */
function memberList(link) {
  const r = UrlFetchApp.fetch(String(link).trim() + '&f=json', { muteHttpExceptions: true });
  if (r.getResponseCode() !== 200) throw new Error('the Worker answered ' + r.getResponseCode());
  const j = JSON.parse(r.getContentText());
  const ids = [];
  const titles = {};
  (j.songs || []).forEach(function (x) {
    ids.push(x.v);
    titles[x.v] = x.a + ' – ' + x.t;
  });
  return { ids: ids, titles: titles, t: j.t || '' };
}

function key(id, name) {
  return id + ':' + name;
}

function loadSeen(props, id) {
  const parts = props.getProperty(key(id, 'parts'));
  if (parts === null) return null;
  let s = '';
  for (let i = 0; i < Number(parts); i++) s += props.getProperty(key(id, 'seen' + i)) || '';
  return new Set(s.match(/.{11}/g) || []);
}

function saveSeen(props, id, ids) {
  const s = ids.join('');
  const old = Number(props.getProperty(key(id, 'parts')) || 0);
  const out = {};
  let n = 0;
  for (let i = 0; i < s.length || n === 0; i += PIECE) out[key(id, 'seen' + n++)] = s.slice(i, i + PIECE);
  out[key(id, 'parts')] = String(n);
  props.setProperties(out);
  for (let i = n; i < old; i++) props.deleteProperty(key(id, 'seen' + i));
}

function listAll(id) {
  const ids = [];
  const titles = {};
  let pageToken;
  do {
    const r = YouTube.PlaylistItems.list('snippet', { playlistId: id, maxResults: 50, pageToken: pageToken });
    (r.items || []).forEach(function (it) {
      const v = it.snippet.resourceId.videoId;
      ids.push(v);
      titles[v] = it.snippet.title;
    });
    pageToken = r.nextPageToken;
  } while (pageToken);
  return { ids: ids, titles: titles };
}

/** Checks one playlist (a member playlist: with its link). Returns null when nothing needed a full listing, otherwise
 * what the listing found. */
function checkOne(props, id, link) {
  const member = link ? memberList(link) : null;
  const count = member ? member.ids.length : YouTube.PlaylistItems.list('id', { playlistId: id, maxResults: 1 }).pageInfo.totalResults;
  const seen = loadSeen(props, id);
  const due = Date.now() - Number(props.getProperty(key(id, 'fullAt')) || 0) >= FULL_EVERY_MS;
  const same = !member || member.ids.every(function (v) { return seen !== null && seen.has(v); });
  if (seen !== null && String(count) === props.getProperty(key(id, 'count')) && !due && same) return null;

  const all = member || listAll(id);
  saveSeen(props, id, all.ids);
  const stamp = {};
  stamp[key(id, 'count')] = String(count);
  stamp[key(id, 'fullAt')] = String(Date.now());
  stamp[key(id, 'videos')] = String(all.ids.length);
  if (seen === null) stamp[key(id, 'announceAt')] = String(Date.now() + HOLD_MS);
  props.setProperties(stamp);
  return {
    count: all.ids.length,
    fresh: seen === null ? [] : all.ids.filter(function (v) { return !seen.has(v); }),
    titles: all.titles,
  };
}

function playlistTitle(id) {
  const r = YouTube.Playlists.list('snippet', { id: id });
  return r.items && r.items.length ? r.items[0].snippet.title : '';
}

/** The unlisted playlists on the channel of the account the script runs as: [{id, title}]. */
function channelPlaylists() {
  const out = [];
  let pageToken;
  do {
    const r = YouTube.Playlists.list('snippet,status', { mine: true, maxResults: 50, pageToken: pageToken });
    (r.items || []).forEach(function (p) {
      if (p.status && p.status.privacyStatus === 'unlisted') out.push({ id: p.id, title: p.snippet.title });
    });
    pageToken = r.nextPageToken;
  } while (pageToken);
  return out;
}

/** Appends a row for each unlisted channel playlist not already in rows, and takes out rows whose YouTube playlist no
 * longer exists (not FIRST_PLAYLIST, nor a row with Skip filled); returns the rows as they are now. */
function addChannelPlaylists(sh, rows) {
  const ch = channelPlaylists();
  const onChannel = new Set(ch.map(function (p) { return p.id; }));
  for (let i = rows.length - 1; i >= 0; i--) {   // (bottom up, so the row numbers above stay right)
    const id = playlistId(rows[i][0]);
    if (!id || onChannel.has(id) || id === FIRST_PLAYLIST || String(rows[i][4]).trim()) continue;
    let gone = false;
    try { gone = playlistTitle(id) === ''; } catch (e) { gone = /not ?found/i.test(String(e && e.message)); }
    if (gone) { sh.deleteRow(i + 2); rows.splice(i, 1); }
  }
  const inSheet = new Set(rows.map(function (row) { return playlistId(row[0]); }));
  const add = ch.filter(function (p) { return !inSheet.has(p.id); }).map(function (p) {
    return [LIST + p.id, p.title, '', 'Added from channel', ''];
  });
  if (!add.length) return rows;
  sh.getRange(rows.length + 2, 1, add.length, HEADERS.length).setValues(add);
  return rows.concat(add);
}

/** The member playlists the Worker lists: [{ id, t, o, link }]; none without the sotdKey property. */
function memberPlaylists() {
  const k = PropertiesService.getScriptProperties().getProperty('sotdKey');
  if (!k) return [];
  const r = UrlFetchApp.fetch(SOTD + '/upl/links', { headers: { Authorization: 'Bearer ' + k }, muteHttpExceptions: true });
  if (r.getResponseCode() !== 200) throw new Error('the Worker answered ' + r.getResponseCode());
  return JSON.parse(r.getContentText()).links || [];
}

/** Appends a row for each member playlist the Worker lists that isn't in rows yet, and takes out rows of member
 * playlists it no longer lists (deleted from the calendar, or an import switched for another); returns the rows as
 * they are now. Nothing is taken out when the Worker doesn't answer, or without the sotdKey property. */
function addMemberPlaylists(sh, rows) {
  if (!PropertiesService.getScriptProperties().getProperty('sotdKey')) return rows;
  const listed = memberPlaylists();   // (throws when the Worker doesn't answer)
  const live = new Set(listed.map(function (p) { return 'm-' + p.id; }));
  for (let i = rows.length - 1; i >= 0; i--) {
    const m = memberId(rows[i][0]);
    if (m && !live.has(m)) { sh.deleteRow(i + 2); rows.splice(i, 1); }
  }
  const inSheet = new Set(rows.map(function (row) { return memberId(row[0]); }));
  const add = listed.filter(function (p) { return !inSheet.has('m-' + p.id); }).map(function (p) {
    return [p.link, p.t, '', 'Added from the calendar', ''];
  });
  if (!add.length) return rows;
  sh.getRange(rows.length + 2, 1, add.length, HEADERS.length).setValues(add);
  return rows.concat(add);
}

/** Starts the R2 download workflow; logs GitHub's reply when it isn't 204 No Content. */
function startDownloads(props) {
  const token = props.getProperty('GITHUB_TOKEN');
  if (!token) return;
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + GITHUB_REPO + '/dispatches', {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ event_type: 'playlist-new' }),
    muteHttpExceptions: true,
  });
  if (r.getResponseCode() !== 204) console.log('GitHub dispatch: ' + r.getResponseCode() + ' ' + r.getContentText());
}

function prune(props, active) {
  Object.keys(props.getProperties()).forEach(function (k) {
    const i = k.indexOf(':');
    if (i > 0 && !active.has(k.slice(0, i))) props.deleteProperty(k);
  });
}

function check() {
  const props = PropertiesService.getScriptProperties();
  const sheetId = props.getProperty('sheetId');
  if (!sheetId) return;
  const sh = SpreadsheetApp.openById(sheetId).getSheetByName(SHEET_NAME);
  const last = sh.getLastRow();
  let rows = last < 2 ? [] : sh.getRange(2, 1, last - 1, HEADERS.length).getValues();
  try {
    rows = addChannelPlaylists(sh, rows);
  } catch (e) {
    console.log('Channel playlists not read: ' + e.message);
  }
  try {
    rows = addMemberPlaylists(sh, rows);
  } catch (e) {
    console.log('Member playlists not read: ' + e.message);
  }
  const active = new Set();
  const news = [];
  rows.forEach(function (row, i) {
    const r = i + 2;
    const id = playlistId(row[0]) || memberId(row[0]);
    const link = memberId(row[0]) ? String(row[0]).trim() : '';
    if (!id) {
      if (String(row[0]).trim() && row[3] !== 'Not a playlist link') sh.getRange(r, 4).setValue('Not a playlist link');
      return;
    }
    if (String(row[4]).trim() || active.has(id)) return;
    active.add(id);
    try {
      let title = String(row[1] || '').trim();
      if (!title) {
        title = link ? memberList(link).t : playlistTitle(id);
        if (title) sh.getRange(r, 2).setValue(title);
        else title = id;
      }
      const found = checkOne(props, id, link);
      if (found) sh.getRange(r, 3, 1, 2).setValues([[found.count, 'OK']]);
      const announceAt = props.getProperty(key(id, 'announceAt'));
      if (announceAt !== null) {
        if (Date.now() < Number(announceAt)) return;
        props.deleteProperty(key(id, 'announceAt'));
        news.push('New playlist: ' + title + ' (' + props.getProperty(key(id, 'videos')) + ' videos)\n' + (link || LIST + id));
      } else if (found && found.fresh.length) {
        news.push(title + ':\n' + found.fresh.map(function (v) {
          return found.titles[v] + '\n' + WATCH + v;
        }).join('\n\n'));
      }
    } catch (e) {
      sh.getRange(r, 4).setValue('Error: ' + e.message);
    }
  });
  prune(props, active);
  if (news.length) {
    MailApp.sendEmail(TO, SUBJECT, news.join('\n\n\n'));
    startDownloads(props);
  }
  // files deleted on the admin page since the last look (a song's were wrong): the downloads start now, so it gets new
  // ones within minutes rather than at the daily run
  try {
    const w = filesWanted();
    if (w > Number(props.getProperty('wantedAt') || 0)) {
      props.setProperty('wantedAt', String(w));
      if (!news.length) startDownloads(props);
    }
  } catch (e) {
    console.log('Deleted files not read: ' + e.message);
  }
}

/** When a file was last deleted on the admin page (the Worker's /upl/wanted), or 0; 0 without the sotdKey property. */
function filesWanted() {
  const k = PropertiesService.getScriptProperties().getProperty('sotdKey');
  if (!k) return 0;
  const r = UrlFetchApp.fetch(SOTD + '/upl/wanted', { headers: { Authorization: 'Bearer ' + k }, muteHttpExceptions: true });
  if (r.getResponseCode() !== 200) throw new Error('the Worker answered ' + r.getResponseCode());
  return Number(JSON.parse(r.getContentText()).at) || 0;
}

/** Moves the single-playlist record (keys seenParts, seen0…, count, fullAt) to FIRST_PLAYLIST's keys. */
function migrate(props) {
  const parts = props.getProperty('seenParts');
  if (parts === null) return;
  const out = {};
  out[key(FIRST_PLAYLIST, 'parts')] = parts;
  for (let i = 0; i < Number(parts); i++) out[key(FIRST_PLAYLIST, 'seen' + i)] = props.getProperty('seen' + i) || '';
  ['count', 'fullAt'].forEach(function (k) {
    const v = props.getProperty(k);
    if (v !== null) out[key(FIRST_PLAYLIST, k)] = v;
  });
  props.setProperties(out);
  props.deleteProperty('seenParts');
  props.deleteProperty('count');
  props.deleteProperty('fullAt');
  for (let i = 0; i < Number(parts); i++) props.deleteProperty('seen' + i);
}

function setup() {
  const props = PropertiesService.getScriptProperties();
  if (!props.getProperty('sheetId')) {
    const ss = SpreadsheetApp.create('Playlist push – playlists');
    const sh = ss.getSheets()[0];
    sh.setName(SHEET_NAME);
    sh.setFrozenRows(1);
    sh.getRange(2, 1).setValue(LIST + FIRST_PLAYLIST);
    props.setProperty('sheetId', ss.getId());
  }
  const ss = SpreadsheetApp.openById(props.getProperty('sheetId'));
  ss.getSheetByName(SHEET_NAME).getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]).setFontWeight('bold');
  migrate(props);
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  check();
  ScriptApp.newTrigger('check').timeBased().everyMinutes(5).create();
  console.log('Playlists sheet: ' + ss.getUrl());
}
