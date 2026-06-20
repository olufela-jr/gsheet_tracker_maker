/**
 * Master control sheet logic.
 *
 * createTracker copies the template as the clicking user (so the user owns the
 * child and it carries the dispatcher shim), shares the service account, then
 * asks the service to scaffold the input tabs and log the tracker to BigQuery.
 *
 * Script Properties used:
 *   SERVICE_URL            the Cloud Run service URL
 *   SERVICE_ACCOUNT_EMAIL  shared as editor on each child
 *   TEMPLATE_SHEET_ID      fallback if the service get_config has no template id
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

  var templateId = getTemplateId_();
  if (!templateId) {
    return;
  }

  // Copy the template AS the user, so the user owns the child and it inherits
  // the bound dispatcher shim.
  var copy;
  try {
    copy = DriveApp.getFileById(templateId).makeCopy(title);
  } catch (err) {
    ui.alert('Could not copy the template: ' + err);
    return;
  }
  var childId = copy.getId();
  var childUrl = 'https://docs.google.com/spreadsheets/d/' + childId + '/edit';

  // Let the service account edit it.
  var serviceAccount =
    (typeof SERVICE_ACCOUNT_EMAIL !== 'undefined') ? SERVICE_ACCOUNT_EMAIL : '';
  if (serviceAccount) {
    copy.addEditor(serviceAccount);
  }

  // Register: scaffold the input tabs and log the tracker to BigQuery.
  var parsed = postToService_({
    action: 'scaffold',
    spreadsheet_id: childId,
    url: childUrl,
    title: title,
    client: client,
    sub_brand: subBrand,
    created_by: Session.getEffectiveUser().getEmail()
  });
  if (parsed && parsed.status === 'ok') {
    // Non-blocking: a toast plus a clickable link logged on the Admin tab,
    // so creating trackers in a row does not require dismissing a dialog.
    SpreadsheetApp.getActiveSpreadsheet().toast(title + ' created', 'New tracker', 5);
    logCreatedTracker_(title, client, subBrand, childUrl);
  } else {
    ui.alert(
      'Created the sheet but registration failed: ' +
      ((parsed && parsed.message) || 'unknown error')
    );
  }
}

/**
 * Append a clickable record of the new tracker to the Admin tab, so the URL is
 * captured without a blocking dialog.
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

/**
 * Prefer the template id from the service (central source of truth); fall back
 * to a Script Property.
 */
function getTemplateId_() {
  var parsed = postToService_({ action: 'get_config' });
  if (parsed && parsed.status === 'ok' && parsed.detail &&
      parsed.detail.template_sheet_id) {
    return parsed.detail.template_sheet_id;
  }
  var fallback = (typeof TEMPLATE_SHEET_ID !== 'undefined') ? TEMPLATE_SHEET_ID : '';
  if (!fallback) {
    SpreadsheetApp.getUi().alert(
      'No template id from the service and none in Config.gs.');
  }
  return fallback;
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

function postToService_(payload) {
  var url = (typeof RELAY_URL !== 'undefined') ? RELAY_URL : '';
  if (!url) {
    SpreadsheetApp.getUi().alert('RELAY_URL is not set in Config.gs.');
    return null;
  }
  // Call the relay; our identity token rides in the body for the service to
  // verify. The relay forwards it to the private service.
  payload.token = ScriptApp.getIdentityToken();
  var options = {
    method: 'post',
    contentType: 'application/json',
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
