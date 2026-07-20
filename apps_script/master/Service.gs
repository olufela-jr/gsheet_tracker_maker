/**
 * Master control sheet logic, driven by the Console tab (see Console.gs).
 *
 * sendFromConsole:     act on the sheet whose URL sits in the Console, running
 *                      the chosen action. scaffold also carries the details.
 * newTrackerFromConsole: create a clean sheet the operator owns, share the
 *                      service account, scaffold it, then point the Console at
 *                      the new URL so the next Send targets it.
 *
 * The master calls the private service directly. The operator's identity token
 * authenticates to Cloud Run (its audience is registered; the operator has
 * run.invoker) and also rides in the body so the service can verify the caller.
 *
 * Config.gs (gitignored) provides SERVICE_URL and SERVICE_ACCOUNT_EMAIL.
 */

/** Tracker Admin > Send: run the Console's action on the Console's URL. */
function sendFromConsole() {
  const input = readConsole_();
  if (!input) {
    SpreadsheetApp.getUi().alert(
      'No Console tab. Run Tracker Admin > Apply formatting first.');
    return;
  }
  const id = parseSheetId_(input.url);
  if (!id) {
    writeStatus_('Paste a valid tracker URL into ' + CELL_URL + ' and try again.');
    return;
  }

  // The service account must be able to edit the target; the operator must own
  // or edit it for this share to succeed.
  let ss;
  try {
    ss = SpreadsheetApp.openById(id);
    shareWithServiceAccount_(ss);
  } catch (err) {
    writeStatus_('Could not open or share that sheet: ' + err);
    return;
  }

  writeStatus_('Running ' + input.action + '...');
  const payload = { action: input.action, spreadsheet_id: id };
  if (input.action === 'scaffold') {
    payload.url = ss.getUrl();
    payload.title = input.title;
    payload.client = input.client;
    payload.sub_brand = input.subBrand;
  }

  const parsed = postToService_(payload);
  if (parsed && parsed.status === 'ok') {
    writeStatus_(input.action + ': ' + (parsed.message || 'done'));
    if (input.action === 'scaffold') {
      logTracker_(input.title, input.client, input.subBrand, ss.getUrl());
    }
  } else {
    writeStatus_(input.action + ' failed: ' + serviceErrorText_(parsed));
  }
}

/** Tracker Admin > New tracker: create a clean sheet from the Console details. */
function newTrackerFromConsole() {
  const ui = SpreadsheetApp.getUi();
  const input = readConsole_();
  if (!input) {
    ui.alert('No Console tab. Run Tracker Admin > Apply formatting first.');
    return;
  }
  // The service requires these to register the tracker; block loudly, not just
  // with a status line, so it is obvious why nothing was created.
  if (!input.client || !input.subBrand) {
    const msg = 'Fill in Client and Sub-brand on the Console (cells ' +
      CELL_CLIENT + ' and ' + CELL_SUBBRAND + '), then New tracker again.';
    writeStatus_(msg);
    ui.alert('New tracker', msg, ui.ButtonSet.OK);
    return;
  }

  // Create AS the operator (they own it), then let the service account edit it.
  const ss = SpreadsheetApp.create(input.title);
  shareWithServiceAccount_(ss);
  const url = ss.getUrl();

  writeStatus_('Creating "' + input.title + '"...');
  const parsed = postToService_({
    action: 'scaffold',
    spreadsheet_id: ss.getId(),
    url: url,
    title: input.title,
    client: input.client,
    sub_brand: input.subBrand
  });

  if (parsed && parsed.status === 'ok') {
    // Point the Console at the new sheet so the next Send targets it.
    consoleSheet_().getRange(CELL_URL).setValue(url);
    writeStatus_('Created "' + input.title + '". URL is now in ' + CELL_URL +
      '. Fill its setup + data_source tabs, then Send with run_all.');
    logTracker_(input.title, input.client, input.subBrand, url);
    ui.alert(
      'Tracker created',
      '"' + input.title + '" was created and scaffolded.\n\n' + url +
        '\n\nIts URL is now in the Console. Fill in its setup and data_source ' +
        'tabs, then use Send with the run_all action.',
      ui.ButtonSet.OK);
  } else {
    const err = 'Created the sheet but scaffolding failed: ' + serviceErrorText_(parsed);
    writeStatus_(err);
    ui.alert('New tracker', err, ui.ButtonSet.OK);
  }
}

/** Message plus any specific service errors (e.g. why a sheet is not ready). */
function serviceErrorText_(parsed) {
  if (!parsed) {
    return 'no response';
  }
  let message = parsed.message || 'error';
  if (parsed.detail && parsed.detail.errors && parsed.detail.errors.length) {
    message += ' - ' + parsed.detail.errors.join('; ');
  }
  return message;
}

function shareWithServiceAccount_(spreadsheet) {
  const sa = (typeof SERVICE_ACCOUNT_EMAIL !== 'undefined') ? SERVICE_ACCOUNT_EMAIL : '';
  if (sa) {
    spreadsheet.addEditor(sa);
  }
}

function parseSheetId_(text) {
  if (!text) {
    return '';
  }
  text = text.trim();
  const match = text.match(/\/d\/([a-zA-Z0-9-_]+)/);
  if (match) {
    return match[1];
  }
  return /^[a-zA-Z0-9-_]+$/.test(text) ? text : '';
}

function postToService_(payload) {
  const url = (typeof SERVICE_URL !== 'undefined') ? SERVICE_URL : '';
  if (!url) {
    SpreadsheetApp.getUi().alert('SERVICE_URL is not set in Config.gs.');
    return null;
  }
  // The operator's identity token authenticates to Cloud Run (header) and is
  // verified by the service (body).
  const token = ScriptApp.getIdentityToken();
  payload.token = token;
  const options = {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + token },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };
  try {
    return JSON.parse(UrlFetchApp.fetch(url, options).getContentText());
  } catch (err) {
    SpreadsheetApp.getUi().alert('Request failed: ' + err);
    return null;
  }
}
