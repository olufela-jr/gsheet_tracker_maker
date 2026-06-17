/**
 * Child tracker shim (generic dispatcher).
 *
 * This is the ONLY code that lives in a child tracker sheet, and it is
 * deliberately generic: it holds no business logic, only the ability to call
 * the central Cloud Run service. New features ship by redeploying the service,
 * so this file does not change and existing children never need re-editing.
 * "Refresh" and "use the latest logic" are the same thing, because the child
 * has no logic of its own.
 *
 * The service URL is read from a Script Property (set on the template and
 * copied along with it), never hardcoded.
 */

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Tracker')
    .addItem('Refresh', 'refresh')
    .addItem('More actions...', 'moreActions')
    .addToUi();
}

function refresh() {
  showResult_('Refresh', callService_('run_all'));
}

/**
 * Server-driven action picker. The list of actions comes from the service, so
 * adding a new action needs no change here.
 */
function moreActions() {
  var parsed = callService_('list_actions');
  if (!parsed || parsed.status !== 'ok') {
    SpreadsheetApp.getUi().alert(
      'Could not load actions: ' + (parsed && parsed.message)
    );
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
    'document.getElementById("msg").innerText=m;}).dispatchAction(a);}' +
    '</script>'
  ).setWidth(260).setHeight(320);
  SpreadsheetApp.getUi().showModalDialog(html, 'Tracker actions');
}

/** Called from the More actions dialog. Returns a status string to show. */
function dispatchAction(action) {
  var parsed = callService_(action);
  if (parsed && parsed.status === 'ok') {
    return action + ': ' + (parsed.message || 'done');
  }
  return action + ' failed: ' + ((parsed && parsed.message) || 'unknown error');
}

function callService_(action) {
  var url = (typeof SERVICE_URL !== 'undefined') ? SERVICE_URL : '';
  if (!url) {
    SpreadsheetApp.getUi().alert('SERVICE_URL is not set in Config.gs.');
    return null;
  }
  var options = {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + ScriptApp.getIdentityToken() },
    payload: JSON.stringify({
      spreadsheet_id: SpreadsheetApp.getActiveSpreadsheet().getId(),
      action: action
    }),
    muteHttpExceptions: true
  };
  try {
    return JSON.parse(UrlFetchApp.fetch(url, options).getContentText());
  } catch (err) {
    SpreadsheetApp.getUi().alert('Request failed: ' + err);
    return null;
  }
}

function showResult_(title, parsed) {
  if (parsed && parsed.status === 'ok') {
    SpreadsheetApp.getActiveSpreadsheet().toast(parsed.message || 'done', title, 5);
  } else {
    SpreadsheetApp.getUi().alert(
      title + ' failed: ' + ((parsed && parsed.message) || 'unknown error')
    );
  }
}
