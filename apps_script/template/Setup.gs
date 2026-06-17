/**
 * One-off formatting for the TEMPLATE sheet, kept in the repo so the same look
 * is reproducible in any instance. Run setupTemplate() once after pushing
 * (Apps Script editor: Run > setupTemplate). It is intentionally NOT in a menu,
 * so children copied from the template never expose a reformat action.
 *
 * Edit the palette and layout here to change how every tracker's input tabs
 * look across all instances.
 */

var TEMPLATE_THEME = {
  banner: '#202124',
  bannerText: '#ffffff',
  muted: '#5f6368',
  font: 'Roboto'
};

function setupTemplate() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var setup = ensureSheet_(ss, 'setup');
  var dataSource = ensureSheet_(ss, 'data_source');
  pruneDefaultSheets_(ss, ['setup', 'data_source']);
  formatSetupTab_(setup);
  formatDataSourceTab_(dataSource);
  ss.toast('Template formatted', 'Setup', 5);
}

function formatSetupTab_(sheet) {
  var header = sheet.getRange('A1:B1');
  header.setValues([['Field', 'Type']]);
  header
    .setBackground(TEMPLATE_THEME.banner)
    .setFontColor(TEMPLATE_THEME.bannerText)
    .setFontWeight('bold')
    .setFontFamily(TEMPLATE_THEME.font);
  sheet.getRange('A1').setNote('Field must exactly match a header in data_source.');
  sheet.getRange('B1').setNote('Type is "metric" or "dimension".');
  sheet.setFrozenRows(1);
  sheet.setColumnWidth(1, 220);
  sheet.setColumnWidth(2, 120);
}

function formatDataSourceTab_(sheet) {
  // Use a cell note so guidance never collides with the user's real headers.
  sheet.getRange('A1').setNote('Paste raw data here. Row 1 = headers, row 2+ = data.');
  sheet.setFrozenRows(1);
}

function ensureSheet_(ss, name) {
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

function pruneDefaultSheets_(ss, keepNames) {
  var keep = keepNames.map(function (n) { return n.toLowerCase(); });
  ss.getSheets().forEach(function (sheet) {
    var name = sheet.getName().toLowerCase();
    if (keep.indexOf(name) === -1 && /^sheet\d*$/.test(name) &&
        ss.getSheets().length > 1) {
      ss.deleteSheet(sheet);
    }
  });
}
