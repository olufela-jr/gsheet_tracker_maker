/**
 * The on-sheet control panel.
 *
 * The operator drives everything from the Console tab: paste a tracker URL,
 * choose an action, then Tracker Admin > Send. Results are written back to the
 * Status cell instead of a toast, so the last outcome is always visible. This
 * file holds the cell addresses and small read/write helpers; Setup.gs builds
 * the tab and Service.gs acts on it.
 */

var CONSOLE_TAB = 'Console';
var HOWTO_TAB = 'How-to';
var LOG_TAB = 'Log';

// Input and output cells on the Console tab (see buildConsole_ in Setup.gs).
var CELL_URL = 'B3';
var CELL_ACTION = 'B4';
var CELL_CLIENT = 'B7';
var CELL_SUBBRAND = 'B8';
var CELL_TITLE = 'B9';
var CELL_STATUS = 'B11';
var CELL_LASTRUN = 'B12';

// Actions the Console dropdown offers. run_all (Refresh) is the default; the
// last, scaffold, prepares/creates the input tabs and registers the sheet.
var CONSOLE_ACTIONS = [
  'run_all',
  'validate',
  'generate_mapping',
  'create_named_ranges',
  'build_views',
  'scaffold'
];

function consoleSheet_() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheetByName(CONSOLE_TAB);
}

/** Read the Console inputs into a plain object, or null if the tab is missing. */
function readConsole_() {
  var sheet = consoleSheet_();
  if (!sheet) {
    return null;
  }
  function val(a1) {
    return String(sheet.getRange(a1).getValue()).trim();
  }
  return {
    url: val(CELL_URL),
    action: val(CELL_ACTION) || 'run_all',
    client: val(CELL_CLIENT),
    subBrand: val(CELL_SUBBRAND),
    title: val(CELL_TITLE) || 'Performance Tracker'
  };
}

/** Write a status line and a timestamp back to the Console. */
function writeStatus_(message) {
  var sheet = consoleSheet_();
  if (!sheet) {
    return;
  }
  sheet.getRange(CELL_STATUS).setValue(message);
  sheet.getRange(CELL_LASTRUN).setValue(new Date());
  SpreadsheetApp.flush();
}

/** Append a clickable record of a tracker to the Log tab. */
function logTracker_(title, client, subBrand, url) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(LOG_TAB);
  if (!sheet) {
    return;
  }
  var label = String(title).replace(/"/g, '""');
  var row = sheet.getLastRow() + 1;
  sheet.getRange(row, 1).setValue(new Date());
  sheet.getRange(row, 2).setValue(client + ' / ' + subBrand);
  sheet.getRange(row, 3).setFormula('=HYPERLINK("' + url + '","' + label + '")');
}
