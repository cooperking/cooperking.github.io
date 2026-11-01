/**
 * Playlist push: rows for playlists that are gone come out of the sheet (and so out of its published CSV), within
 * 5 minutes of the next check (John, 1 Nov 2026: "part of the process of deleting a playlist should be to remove it
 * from the csv").
 *
 * In the "Playlist push" Apps Script project (Code.gs), replace the two functions addChannelPlaylists and
 * addMemberPlaylists with these two. Nothing else changes.
 *
 * - A member playlist's row comes out once the Worker no longer lists it (deleted from the calendar, or an import
 *   switched for another: the new one's row is added in its place). Not when the Worker doesn't answer, or when the
 *   sotdKey property isn't set.
 * - A YouTube playlist's row comes out once that playlist no longer exists on YouTube (the site deletes a deleted
 *   member playlist's YouTube copy). Your own channel's playlists are left as they are.
 * The records the script kept for them are tidied away by prune() as before.
 */

/** Appends a row for each unlisted channel playlist not already in rows, and takes out rows whose YouTube playlist is
 * gone; returns the rows as they are now. */
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

/** Appends a row for each member playlist the Worker lists that isn't in rows yet, and takes out rows for member
 * playlists it no longer lists; returns the rows as they are now. */
function addMemberPlaylists(sh, rows) {
  if (!PropertiesService.getScriptProperties().getProperty('sotdKey')) return rows;
  const listed = memberPlaylists();   // (throws when the Worker doesn't answer: then nothing is taken out)
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
