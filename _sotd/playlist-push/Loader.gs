/**
 * Playlist push, loaded from the Song of the Day repository (John, 1 Nov 2026: so its code is changed with a commit,
 * not by pasting). This is the whole of Code.gs in the Apps Script project: each run reads
 * _sotd/playlist-push/Code.gs from the repository's main branch and runs its check() (or setup()). The last copy read
 * is kept in the script's properties (codePart0…, codeParts), so a run goes on with it when GitHub can't be reached.
 *
 * Anyone who can change the repository's main branch can change what this runs, with this project's access to your
 * YouTube, Gmail, Sheets and the web. The settings (sheetId, sotdKey, GITHUB_TOKEN) and the 5-minute trigger on check
 * stay as they are.
 */
const CODE_URL = 'https://raw.githubusercontent.com/cooperking/cooperking.github.io/main/_sotd/playlist-push/Code.gs';
const CODE_PIECE = 8000;

function loadCode_() {
  const props = PropertiesService.getScriptProperties();
  let code = '';
  try {
    const r = UrlFetchApp.fetch(CODE_URL + '?t=' + Date.now(), { muteHttpExceptions: true });
    const text = r.getResponseCode() === 200 ? r.getContentText() : '';
    if (/function check\(/.test(text) && /function setup\(/.test(text)) {
      code = text;
      if (code !== keptCode_(props)) keepCode_(props, code);
    } else {
      console.log('The repository answered ' + r.getResponseCode() + ': the last copy runs');
    }
  } catch (e) {
    console.log('The repository could not be read (' + e.message + '): the last copy runs');
  }
  code = code || keptCode_(props);
  if (!code) throw new Error('No code yet: the repository could not be read');
  return new Function(code + '\n;return { check: check, setup: setup };')();
}

function keptCode_(props) {
  const n = Number(props.getProperty('codeParts') || 0);
  let s = '';
  for (let i = 0; i < n; i++) s += props.getProperty('codePart' + i) || '';
  return s;
}

function keepCode_(props, code) {
  const old = Number(props.getProperty('codeParts') || 0), out = {};
  let n = 0;
  for (let i = 0; i < code.length; i += CODE_PIECE) out['codePart' + n++] = code.slice(i, i + CODE_PIECE);
  out.codeParts = String(n);
  props.setProperties(out);
  for (let i = n; i < old; i++) props.deleteProperty('codePart' + i);
}

/** The 5-minute trigger's function. */
function check() {
  loadCode_().check();
}

/** Run once by hand after pasting, if the trigger or the permissions need setting up again. */
function setup() {
  loadCode_().setup();
}

/** Never run: it names the services the loaded code uses, so Apps Script asks for their permissions. */
function permissions_() {
  YouTube.Playlists.list('id', { mine: true, maxResults: 1 });
  SpreadsheetApp.openById('');
  MailApp.sendEmail('', '', '');
  ScriptApp.newTrigger('check');
  UrlFetchApp.fetch('');
}
