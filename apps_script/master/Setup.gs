/**
 * One-off formatting for the MASTER control sheet, kept in the repo so the same
 * look is reproducible in any instance. Run it from Tracker Admin > Apply
 * formatting (or Run > setupMaster in the editor).
 *
 * It (re)builds three tabs: Console (the cell-driven control panel), How-to (a
 * guide to the setup schema), and Log (a running record of created trackers).
 * Edit the layout and palette here to change the control panel across instances.
 */

var NAVY = '#1F3864';
var WHITE = '#ffffff';
var GREY = '#5f6368';
var LIGHT = '#f1f3f4';
var BORDER = '#c9d2e3';

function setupMaster() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  buildConsole_(ss);
  buildHowTo_(ss);
  buildLog_(ss);
  ss.setActiveSheet(consoleSheet_());
  ss.toast('Master control panel rebuilt', 'Setup', 5);
}

/** Reuse a tab by name; on a brand-new sheet, adopt the empty default Sheet1. */
function getOrCreateSheet_(ss, name) {
  var sheet = ss.getSheetByName(name);
  if (sheet) {
    return sheet;
  }
  var sheets = ss.getSheets();
  if (sheets.length === 1 &&
      sheets[0].getName() === 'Sheet1' && sheets[0].getLastRow() === 0) {
    sheets[0].setName(name);
    return sheets[0];
  }
  return ss.insertSheet(name);
}

function buildConsole_(ss) {
  var sheet = getOrCreateSheet_(ss, CONSOLE_TAB);
  sheet.clear();
  sheet.clearNotes();
  sheet.getRange(1, 1, sheet.getMaxRows(), sheet.getMaxColumns())
    .clearDataValidations();
  sheet.setHiddenGridlines(true);

  // Navy banner across A1:D1, no merge (the title overflows across the row).
  sheet.getRange('A1:D1')
    .setBackground(NAVY).setFontColor(WHITE).setFontSize(16)
    .setFontWeight('bold').setFontFamily('Arial').setVerticalAlignment('middle');
  sheet.getRange('A1').setValue('Tracker Console');
  sheet.setRowHeight(1, 46);
  sheet.getRange('A2')
    .setValue('Operate on an existing tracker: paste its URL, choose an action, ' +
              'then Tracker Admin ▸ Send.')
    .setFontColor(GREY);

  var labels = [
    ['A3', 'Target sheet URL'],
    ['A4', 'Action'],
    ['A7', 'Client'],
    ['A8', 'Sub-brand'],
    ['A9', 'Title'],
    ['A11', 'Status'],
    ['A12', 'Last run']
  ];
  labels.forEach(function (pair) {
    sheet.getRange(pair[0]).setValue(pair[1]).setFontWeight('bold').setFontColor(NAVY);
  });
  // Section subheader that also tells the operator how to create a new one.
  sheet.getRange('A6')
    .setValue('Create a new tracker: fill these in (URL can stay blank), then ' +
              'Tracker Admin ▸ New tracker.')
    .setFontColor(GREY).setFontWeight('bold').setWrap(true);
  sheet.setRowHeight(6, 34);

  // Input cells: white with a light border so they read as fields to fill in.
  ['B3', 'B4', 'B7', 'B8', 'B9'].forEach(function (a1) {
    sheet.getRange(a1)
      .setBackground(WHITE)
      .setBorder(true, true, true, true, false, false, BORDER,
                 SpreadsheetApp.BorderStyle.SOLID);
  });

  // Action dropdown, defaulting to run_all.
  var rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(CONSOLE_ACTIONS, true)
    .setAllowInvalid(false)
    .build();
  sheet.getRange(CELL_ACTION).setDataValidation(rule).setValue('run_all');

  // Status cells: the script writes here, so tint them to read as output.
  sheet.getRange('B11:B12').setBackground(LIGHT).setFontColor(GREY);
  sheet.getRange(CELL_LASTRUN).setNumberFormat('yyyy-mm-dd hh:mm');

  sheet.getRange(CELL_URL)
    .setNote('Paste the full URL (or ID) of the tracker sheet to act on.');
  sheet.getRange(CELL_ACTION)
    .setNote('Refresh = run_all. scaffold prepares/creates the input tabs and ' +
             'registers the sheet (uses the details below).');

  sheet.setColumnWidth(1, 170);
  sheet.setColumnWidth(2, 560);

  ss.setActiveSheet(sheet);
  ss.moveActiveSheet(1);
}

function buildHowTo_(ss) {
  var sheet = getOrCreateSheet_(ss, HOWTO_TAB);
  sheet.clear();
  sheet.setHiddenGridlines(true);

  sheet.getRange('A1:E1')
    .setBackground(NAVY).setFontColor(WHITE).setFontSize(16)
    .setFontWeight('bold').setFontFamily('Arial').setVerticalAlignment('middle');
  sheet.getRange('A1').setValue('How to set up a tracker');
  sheet.setRowHeight(1, 46);

  heading_(sheet, 'A3', '1. Fill in two tabs on your tracker');
  para_(sheet, 'A4',
    'Every tracker needs a "setup" tab (the schema) and a "data_source" tab ' +
    '(your raw data). Everything else, including mapping and the ' +
    'daily/weekly/monthly views, is generated for you.');

  heading_(sheet, 'A6', '2. The setup tab: one row per field');
  para_(sheet, 'A7',
    'A  Field: the field name; raw fields must match a data_source header exactly. ' +
    'Names and headers must be unique.\n' +
    'B  Type: metric, dimension, date (tag exactly one field as date), or calculated.\n' +
    'C  Formula: the [Field]-token expression for a calculated field, e.g. [Spend]/[Clicks]. ' +
    'Its cells simply compute from the metric cells beside them, so it follows every slicer.\n' +
    'D  Format: optional number format: currency, percent, or number.\n' +
    'E  Show in views: dimensions only; check to add it as a filter dropdown (slicer) on ' +
    'the views.\n' +
    'F  Break-out table: dimensions only; check to add a totals-per-value table for it on every view.\n' +
    'G  Mapping: dimensions only; check to list its values in the mapping tab. ' +
    'Show / Break-out imply it; leave all three blank to keep a high-cardinality ' +
    'dimension out of Mapping.');
  sheet.setRowHeight(7, 160);

  heading_(sheet, 'A9', '3. Example setup');
  var example = [
    ['Field', 'Type', 'Formula', 'Format', 'Show in views', 'Break-out table', 'Mapping'],
    ['Day', 'date', '', '', '', '', ''],
    ['Region', 'dimension', '', '', 'TRUE', 'TRUE', ''],
    ['Channel', 'dimension', '', '', '', '', 'TRUE'],
    ['Market', 'dimension', '', '', '', 'TRUE', ''],
    ['Spend', 'metric', '', 'currency', '', '', ''],
    ['Clicks', 'metric', '', 'number', '', '', ''],
    ['CPC', 'calculated', '[Spend]/[Clicks]', 'currency', '', '', '']
  ];
  sheet.getRange(10, 1, example.length, 7).setValues(example);
  sheet.getRange(10, 1, 1, 7)
    .setBackground(NAVY).setFontColor(WHITE).setFontWeight('bold');
  sheet.getRange(10, 1, example.length, 7)
    .setBorder(true, true, true, true, true, true, BORDER,
               SpreadsheetApp.BorderStyle.SOLID);
  para_(sheet, 'A19',
    'Channel has only Mapping checked, so its values are listed in the mapping ' +
    'tab but it gets no filter dropdown or break-out. Region and Market have Break-out table checked, so each gets its own ' +
    'totals-per-value table. Rows render in this order, so a calculated metric ' +
    'like CPC shows exactly where you place it.');
  sheet.setRowHeight(19, 44);

  heading_(sheet, 'A21', '4. The data_source tab');
  para_(sheet, 'A22',
    'Row 1 is headers, row 2+ is your data. Header names must match the raw ' +
    'Field names in setup exactly.');

  heading_(sheet, 'A24', '5. What gets generated');
  para_(sheet, 'A25',
    'mapping (the filter values per dimension) and the daily / weekly / monthly ' +
    'views (mapping also carries the available dates, which feed the date ' +
    'dropdowns). Each view stacks blocks: a header (date controls on the first ' +
    'row, dimension slicers below, live Today / days-left stats at the right), ' +
    'then a KPI totals row and the by-period block scoped by the controls: ' +
    'daily has Date from / to dropdowns of the available dates (blank = the ' +
    'last 14 days of data, newest first); weekly has calendar pickers ' +
    'defaulting to the last 28 days (up to 6 weeks); monthly has a Year ' +
    'dropdown of the years in your data (defaulting to the current year) ' +
    'with months past today left blank. Weekly and monthly also get a ' +
    'comparison block just below the ' +
    'KPI totals: two From/To date ranges side by side per metric with a % change ' +
    'row underneath; the dates are dropdowns of the available dates and the ' +
    'rows fill in once both are picked. ' +
    'Any dimension flagged Break-out table gets its own totals-per-value ' +
    'table (capped at 50 values). A comparison tab lets you pick two campaigns ' +
    '(or other dimension values) and date ranges side by side, with a trend chart. ' +
    'The monthly view also carries a line chart.');
  sheet.setRowHeight(25, 72);

  heading_(sheet, 'A27', '6. Run it');
  para_(sheet, 'A28',
    'Paste the tracker URL into the Console tab, pick an action (Refresh = ' +
    'run_all), then Tracker Admin ▸ Send. The outcome appears in the ' +
    'Console Status cell.');

  sheet.setColumnWidth(1, 520);
  sheet.setColumnWidth(2, 100);
  sheet.setColumnWidth(3, 170);
  sheet.setColumnWidth(4, 90);
  sheet.setColumnWidth(5, 110);
  sheet.setColumnWidth(6, 120);
  sheet.setColumnWidth(7, 100);
}

/** Ensure a Log tab exists with a header; adopt the old Admin tab if present. */
function buildLog_(ss) {
  var sheet = ss.getSheetByName(LOG_TAB);
  if (!sheet) {
    var admin = ss.getSheetByName('Admin');  // the pre-redesign log tab
    sheet = admin || ss.insertSheet(LOG_TAB);
    sheet.setName(LOG_TAB);
  }
  sheet.setHiddenGridlines(true);
  if (sheet.getLastRow() === 0) {
    sheet.getRange('A1:C1')
      .setValues([['Created', 'Client / Sub-brand', 'Tracker']])
      .setBackground(NAVY).setFontColor(WHITE).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }
  sheet.setColumnWidth(1, 150);
  sheet.setColumnWidth(2, 220);
  sheet.setColumnWidth(3, 320);
}

function heading_(sheet, a1, text) {
  sheet.getRange(a1).setValue(text)
    .setFontWeight('bold').setFontColor(NAVY).setFontSize(12);
}

function para_(sheet, a1, text) {
  sheet.getRange(a1).setValue(text).setFontColor(GREY).setWrap(true);
}
