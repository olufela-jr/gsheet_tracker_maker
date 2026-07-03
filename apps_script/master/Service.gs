/**
 * Master control sheet logic.
 *
 * createTracker:  create a clean sheet (no script), the operator owns it, share
 *                 the service account, then have the service scaffold + format
 *                 it and log it to BigQuery.
 * operateOnTracker: point the service at any sheet by URL and run an action.
 *
 * The master calls the private service directly. The operator's identity token
 * authenticates to Cloud Run (its audience is registered; the operator has
 * run.invoker) and also rides in the body so the service can verify the caller.
 *
 * Config.gs (gitignored) provides SERVICE_URL and SERVICE_ACCOUNT_EMAIL.
 */

function createTracker() {
  var ui = SpreadsheetApp.getUi();

  var client = promptRequired_(ui, 'New tracker (1/3)', 'Client:');
  if (client === null) {
    return;
  }
  var subBrand = promptRequired_(ui, 'New tracker (2/3)', 'Sub-brand:');
  if (subBrand === null) {
    return;
  }
  var titleResponse = ui.prompt(
    'New tracker (3/3)', 'Sheet title:', ui.ButtonSet.OK_CANCEL);
  if (titleResponse.getSelectedButton() !== ui.Button.OK) {
    return;
  }
  var title = titleResponse.getResponseText() || 'Performance Tracker';

  // Create AS the operator (they own it), then let the service account edit it.
  var ss = SpreadsheetApp.create(title);
  shareWithServiceAccount_(ss);
  var url = ss.getUrl();

  var parsed = postToService_({
    action: 'scaffold',
    spreadsheet_id: ss.getId(),
    url: url,
    title: title,
    client: client,
    sub_brand: subBrand
  });
  if (parsed && parsed.status === 'ok') {
    SpreadsheetApp.getActiveSpreadsheet().toast(title + ' created', 'New tracker', 5);
    logCreatedTracker_(title, client, subBrand, url);
  } else {
    ui.alert(
      'Created the sheet but registration failed: ' +
      ((parsed && parsed.message) || 'unknown error')
    );
  }
}

/**
 * Prepare an EXISTING sheet as a tracker: ensure + format the input tabs and
 * register it. For a sheet that is not yet set up (e.g. missing data_source).
 */
function setUpExistingSheet() {
  var ui = SpreadsheetApp.getUi();
  var urlResponse = ui.prompt(
    'Set up an existing sheet', 'Paste the sheet URL (or ID):',
    ui.ButtonSet.OK_CANCEL);
  if (urlResponse.getSelectedButton() !== ui.Button.OK) {
    return;
  }
  var id = parseSheetId_(urlResponse.getResponseText());
  if (!id) {
    ui.alert('Could not read a sheet ID from that.');
    return;
  }

  var client = promptRequired_(ui, 'Set up (1/3)', 'Client:');
  if (client === null) {
    return;
  }
  var subBrand = promptRequired_(ui, 'Set up (2/3)', 'Sub-brand:');
  if (subBrand === null) {
    return;
  }
  var titleResponse = ui.prompt('Set up (3/3)', 'Title:', ui.ButtonSet.OK_CANCEL);
  if (titleResponse.getSelectedButton() !== ui.Button.OK) {
    return;
  }
  var title = titleResponse.getResponseText() || 'Performance Tracker';

  var ss;
  try {
    ss = SpreadsheetApp.openById(id);
    shareWithServiceAccount_(ss);
  } catch (err) {
    ui.alert('Could not open or share that sheet: ' + err);
    return;
  }

  var parsed = postToService_({
    action: 'scaffold',
    spreadsheet_id: id,
    url: ss.getUrl(),
    title: title,
    client: client,
    sub_brand: subBrand
  });
  if (parsed && parsed.status === 'ok') {
    SpreadsheetApp.getActiveSpreadsheet().toast(title + ' set up', 'Set up', 5);
    logCreatedTracker_(title, client, subBrand, ss.getUrl());
  } else {
    ui.alert('Set up failed: ' + ((parsed && parsed.message) || 'unknown error'));
  }
}

function operateOnTracker() {
  var ui = SpreadsheetApp.getUi();
  var response = ui.prompt(
    'Operate on tracker', 'Paste the tracker sheet URL (or ID):',
    ui.ButtonSet.OK_CANCEL);
  if (response.getSelectedButton() !== ui.Button.OK) {
    return;
  }
  var id = parseSheetId_(response.getResponseText());
  if (!id) {
    ui.alert('Could not read a sheet ID from that.');
    return;
  }

  // The service account must be able to edit the sheet. The operator must be
  // able to share it (own/edit it) for this to succeed.
  try {
    shareWithServiceAccount_(SpreadsheetApp.openById(id));
  } catch (err) {
    ui.alert('Could not share that sheet with the service account: ' + err);
    return;
  }

  var parsed = postToService_({ action: 'list_actions' });
  if (!parsed || parsed.status !== 'ok') {
    ui.alert('Could not load actions: ' + (parsed && parsed.message));
    return;
  }
  var actions = (parsed.detail && parsed.detail.actions) || [];
  var buttons = actions
    .map(function (a) {
      return '<button style="margin:4px 0;width:100%" onclick="run(\'' +
        a.action + '\')">' + a.label + '</button>';
    })
    .join('');
  var html = HtmlService.createHtmlOutput(
    '<div style="font-family:Arial,sans-serif;padding:8px">' + buttons +
    '<p id="msg" style="color:#5f6368"></p></div>' +
    '<script>function run(a){' +
    'document.getElementById("msg").innerText="Running "+a+"...";' +
    'google.script.run.withSuccessHandler(function(m){' +
    'document.getElementById("msg").innerText=m;}).runActionOnTarget(a,"' + id + '");}' +
    '</script>'
  ).setWidth(280).setHeight(320);
  ui.showModalDialog(html, 'Operate on tracker');
}

/** Called from the operate dialog; runs one action on the target sheet. */
function runActionOnTarget(action, spreadsheetId) {
  var parsed = postToService_({ action: action, spreadsheet_id: spreadsheetId });
  if (parsed && parsed.status === 'ok') {
    return action + ': ' + (parsed.message || 'done');
  }
  return action + ' failed: ' + serviceErrorText_(parsed);
}

/** Message plus any specific service errors (e.g. why a sheet is not ready). */
function serviceErrorText_(parsed) {
  if (!parsed) {
    return 'no response';
  }
  var message = parsed.message || 'error';
  if (parsed.detail && parsed.detail.errors && parsed.detail.errors.length) {
    message += ' - ' + parsed.detail.errors.join('; ');
  }
  return message;
}

function shareWithServiceAccount_(spreadsheet) {
  var sa = (typeof SERVICE_ACCOUNT_EMAIL !== 'undefined') ? SERVICE_ACCOUNT_EMAIL : '';
  if (sa) {
    spreadsheet.addEditor(sa);
  }
}

function parseSheetId_(text) {
  if (!text) {
    return '';
  }
  text = text.trim();
  var match = text.match(/\/d\/([a-zA-Z0-9-_]+)/);
  if (match) {
    return match[1];
  }
  return /^[a-zA-Z0-9-_]+$/.test(text) ? text : '';
}

function promptRequired_(ui, title, label) {
  while (true) {
    var response = ui.prompt(title, label, ui.ButtonSet.OK_CANCEL);
    if (response.getSelectedButton() !== ui.Button.OK) {
      return null;
    }
    var value = response.getResponseText().trim();
    if (value) {
      return value;
    }
    ui.alert('This field is required.');
  }
}

/**
 * Append a clickable record of a created tracker to the Admin tab, so the URL
 * is captured without a blocking dialog.
 */
function logCreatedTracker_(title, client, subBrand, url) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Admin');
  if (!sheet) {
    return;
  }
  var label = String(title).replace(/"/g, '""');
  var row = sheet.getLastRow() + 1;
  sheet.getRange(row, 1).setValue(new Date());
  sheet.getRange(row, 2).setValue(client + ' / ' + subBrand);
  sheet.getRange(row, 3).setFormula('=HYPERLINK("' + url + '","' + label + '")');
}

function postToService_(payload) {
  var url = (typeof SERVICE_URL !== 'undefined') ? SERVICE_URL : '';
  if (!url) {
    SpreadsheetApp.getUi().alert('SERVICE_URL is not set in Config.gs.');
    return null;
  }
  // The operator's identity token authenticates to Cloud Run (header) and is
  // verified by the service (body).
  var token = ScriptApp.getIdentityToken();
  payload.token = token;
  var options = {
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
