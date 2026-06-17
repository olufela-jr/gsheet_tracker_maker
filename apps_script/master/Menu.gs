/**
 * Master control sheet menu.
 *
 * The master is where users mint new trackers. It holds no generation logic;
 * "New tracker" copies the template (so the child carries the dispatcher shim)
 * and registers the child with the service.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Tracker Admin')
    .addItem('New tracker', 'createTracker')
    .addSeparator()
    .addItem('Apply formatting', 'setupMaster')
    .addToUi();
}
