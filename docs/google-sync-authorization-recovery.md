# Recover a rejected Google sign-in

## Symptom and cause

The 0.3.0/0.3.1 migration dialog can report "Could not verify this operation" when creating
or joining a migration. A saved Google token can match the configured client while Google
rejects its refresh with `invalid_grant`. This fails before reading the migration ledger.
The credential adapter previously let `google.auth.exceptions.RefreshError` reach the generic
operation error handler, hiding the necessary authorization step.

## Recovery in the installed versions

1. Close the migration dialog and open Settings → Google Sheets.
2. Click **Disconnect this computer**. This removes the saved sign-in and OAuth client setup on this
   Windows account; it does not remove time records, the Sheet URL, or the shared Sheet.
3. Restore the Desktop OAuth client ID and secret with **Save OAuth client**. Enter these only
   in QI Flow. Keep the same shared Sheet connection.
4. Click **Authorize this computer** and finish Google sign-in in the browser.
5. Retry **Review shared Sheet migration → Create or join migration** with the same roster.

## Legacy settings revision

An unchanged connection saved by V1 may have no `google_sync_generation` setting.
The settings reader treated it as revision zero, while migration guards compared the stored
missing value against zero and raised "Sync settings changed; reopen reviewed migration."
Reopening did not help. Saving an unchanged legacy connection now persists revision zero
without resetting its target or enabling sync. Actual connection changes still advance the
revision and invalidate running jobs.

## Repair acceptance criteria — US28/US46

- A non-retryable Google refresh rejection becomes a sanitized authorization-required error
  explaining the recovery sequence, rather than the generic migration failure.
- The failed refresh leaves saved credentials intact until the user explicitly reconnects.
  The failed refresh itself changes no time records or shared Sheet data and logs no token
  or response contents. Earlier steps may have saved data, so review the operation after
  reconnecting before retrying.
- A retryable refresh failure is not misclassified as an invalid sign-in.
- Successful bounded refresh, cancellation, and client-matching safeguards remain intact.
- An unchanged legacy connection can create, contribute to, and complete a reviewed migration;
  its original Sheet rows and local hours are retained. Real settings changes still reject
  obsolete migration jobs.

Regression tests exercise the real credential adapter against rejected and temporary refresh
failures using synthetic credentials. The live diagnostic reproduced `invalid_grant` without
publishing a migration or changing any hours. An integration regression covers the complete
cutover from legacy settings that have no saved revision.
