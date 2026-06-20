/**
 * Relay web app. A deliberately dumb path to execution.
 *
 * It holds the ONE stable identity that the private Cloud Run service accepts
 * (its getIdentityToken audience is the only custom audience registered on the
 * service, and its owner has run.invoker). Children and the master call this
 * relay instead of Cloud Run, so the unbounded per-child audience problem
 * disappears.
 *
 * This file contains NO business logic. It forwards the request body to the
 * service and returns the response verbatim. All verification, allowlisting,
 * rate limiting, authorization, and logging live in the service, which has the
 * verified identity and the BigQuery registry. Because there is no logic here,
 * this file never needs editing as the product evolves.
 */

function doPost(e) {
  var body = (e && e.postData && e.postData.contents) ? e.postData.contents : '{}';

  var response = UrlFetchApp.fetch(SERVICE_URL, {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + ScriptApp.getIdentityToken() },
    payload: body,
    muteHttpExceptions: true
  });

  return ContentService
    .createTextOutput(response.getContentText())
    .setMimeType(ContentService.MimeType.JSON);
}
