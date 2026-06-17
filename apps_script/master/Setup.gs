/**
 * One-off formatting for the MASTER control sheet, kept in the repo so the same
 * look is reproducible in any instance. Run it from Tracker Admin > Apply
 * formatting (or Run > setupMaster in the editor).
 *
 * Edit the layout and palette here to change the control panel across instances.
 */

function setupMaster() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheets()[0];
  sheet.setName('Admin');
  sheet.setHiddenGridlines(true);

  var title = sheet.getRange('A1:F1');
  title.merge()
    .setValue('Tracker Admin')
    .setBackground('#202124')
    .setFontColor('#ffffff')
    .setFontSize(16)
    .setFontWeight('bold')
    .setVerticalAlignment('middle');
  sheet.setRowHeight(1, 48);

  sheet.getRange('A3')
    .setValue('Tracker Admin > New tracker creates a tracker (a copy of the template).')
    .setFontColor('#5f6368');
  sheet.getRange('A4')
    .setValue('Each tracker is owned by its creator and logged to BigQuery.')
    .setFontColor('#5f6368');
  sheet.setColumnWidth(1, 480);

  ss.toast('Master formatted', 'Setup', 5);
}
