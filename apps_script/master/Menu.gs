/**
 * Master control sheet menu.
 *
 * The master is the only Apps Script in the system. Trackers carry no script;
 * the master both creates them and operates on them by pointing the private
 * service at a sheet. The operator drives it from the Console tab, so the menu
 * is just: Send (act on the URL in the Console) and New tracker.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Tracker Admin')
    .addItem('Send', 'sendFromConsole')
    .addItem('New tracker', 'newTrackerFromConsole')
    .addSeparator()
    .addItem('Apply formatting', 'setupMaster')
    .addToUi();
}
