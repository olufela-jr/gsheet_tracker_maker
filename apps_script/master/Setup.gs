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

  // Navy banner across A1:F1, no merge (the title overflows across the row).
  sheet.getRange('A1:F1')
    .setBackground('#1F3864')
    .setFontColor('#ffffff')
    .setFontSize(16)
    .setFontWeight('bold')
    .setFontFamily('Arial')
    .setVerticalAlignment('middle');
  sheet.getRange('A1').setValue('Tracker Admin');
  sheet.setRowHeight(1, 48);

  sheet.getRange('A3')
    .setValue('New tracker creates a clean sheet; Operate on tracker acts on a sheet by URL.')
    .setFontColor('#5f6368');
  sheet.getRange('A4')
    .setValue('Each tracker is owned by its creator and logged to BigQuery.')
    .setFontColor('#5f6368');
  sheet.setColumnWidth(1, 480);

  ss.toast('Master formatted', 'Setup', 5);
}
