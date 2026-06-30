/**
 * Master control sheet menu.
 *
 * The master is the only Apps Script in the system. Trackers carry no script;
 * the master both creates them and operates on them by pointing the private
 * service at a sheet. All logic lives in the service.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Tracker Admin')
    .addItem('New tracker', 'createTracker')
    .addItem('Operate on tracker', 'operateOnTracker')
    .addSeparator()
    .addItem('Apply formatting', 'setupMaster')
    .addToUi();
}
