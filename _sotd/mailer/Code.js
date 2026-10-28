/**
 * Song of the Day mailer: the Worker's emails (a member's code to sign in, and the Members tab's test), sent from
 * John's own Google account with MailApp (Apps Script's quota for a Gmail account: 100 recipients a day).
 *
 * A web app run as John, for anyone with its address and key. The Worker (sotd-playlists) sends
 * { key, to, subject, text } as JSON; the answer is { ok: true, left } or { error }.
 *
 * Set up once with clasp, from this folder: sh setup.sh (it makes the project, pushes this code, deploys it as a web
 * app, writes its address to mailer-url.txt and opens the editor). Then run setUp() there: Google asks to let it send
 * email as you, and the log shows the key. The address and the key go in the site repository's secrets, MAILER_URL and
 * MAILER_KEY; the Worker's job (worker.yml) puts them on the Worker. After a change here: clasp push -f, then
 * clasp redeploy <the deployment's id> (the same address).
 */
function doPost(e) {
  var b = {};
  try { b = JSON.parse(e.postData.contents); } catch (x) { return answer({ error: 'That wasn’t JSON.' }); }
  var key = PropertiesService.getScriptProperties().getProperty('MAILER_KEY');
  if (!key || b.key !== key) return answer({ error: 'Wrong key.' });
  var to = String(b.to || '');
  if (!/^[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+$/.test(to)) return answer({ error: 'No address to send it to.' });
  if (MailApp.getRemainingDailyQuota() < 1) return answer({ error: 'Today’s emails are used up (100 a day).' });
  MailApp.sendEmail({ to: to, subject: String(b.subject || '').slice(0, 200), body: String(b.text || '').slice(0, 5000), name: 'John’s Song of the Day' });
  return answer({ ok: true, left: MailApp.getRemainingDailyQuota() });
}

function answer(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

// Run once in the editor: Google asks to let the script send email as you. Makes the key (Script properties,
// MAILER_KEY) if there isn't one, and shows it in the log, with the emails left today.
function setUp() {
  var p = PropertiesService.getScriptProperties(), key = p.getProperty('MAILER_KEY');
  if (!key) {
    key = Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, '');
    p.setProperty('MAILER_KEY', key);
  }
  Logger.log('MAILER_KEY: ' + key);
  Logger.log('Emails left today: ' + MailApp.getRemainingDailyQuota());
}
