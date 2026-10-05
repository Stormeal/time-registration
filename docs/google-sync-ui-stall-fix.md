# Google sync scheduler stalls after cutover — US29/US47

After activating shared sync, the automatic scheduler checks eligibility every five seconds
on the UI thread. It opened a SQLite unit of work (`BEGIN IMMEDIATE`) before calling
`GoogleOAuthStore.is_authorized()`. The production credential adapter loads the configured
client through `GoogleSyncSettings.load()`, which opens a second transaction against the same
database. The second transaction waits for the first transaction's write lock until the
five-second busy timeout expires. This blocks the UI repeatedly, even while no network sync
is running. Before cutover, the absent active target bypassed authorization and masked the bug.

The application facade now checks authorization before opening its eligibility transaction.
Each database transaction finishes independently. Timer persistence, atomic outbox capture,
authorization checks, and the five-minute network schedule retain their existing behavior.

Acceptance criteria:

- Repeated eligibility polls with a reviewed target and the production credential adapter can
  read the configured OAuth client without acquiring nested SQLite write locks.
- An unauthorized or unreviewed connection remains ineligible for automatic sync.
- Pending groups retain their complete-group publication grace and cancellation safeguards.

The regression combines the real SQLite settings adapter and Google credential adapter with
synthetic credential values. SQLite lock waiting is disabled in this test so nested acquisition
fails immediately instead of relying on timing assertions. It reproduced `database is locked`
before the repair and passes repeated polls after the repair. Existing scheduling and UI tests
cover eligibility, job ownership, cancellation, and shutdown. A local profile uses a disposable
database snapshot and prints only timings, counts, and function names; no hours or credentials
are logged.
