# Google Sheets sync protocol V2

The audit remediation replaces snapshot replacement with an immutable change log. The current
implementation stores protocol state and captures eligible local commands atomically; ordinary V2
publication remains disabled until reviewed initialization or
migration are implemented and verified.
The public V1 sync operation refuses before reading the Sheet or importing records and explains
that a reviewed upgrade is required. Local tracking remains available.

## Destination and durable state

Every record belongs to `SyncTarget(spreadsheet_id, log_id)`. A recreated log has a new immutable
log ID. Changes, outbox entries, acknowledgements, materialized heads, conflicts, malformed-data
problems, and migration state are namespaced by both identifiers in migration `0007`.

`UnitOfWork.sync_for(target)` shares the local SQLite transaction. Changes have stable opaque
IDs, UTC creation instants, device provenance, entity kind and ID, causal parent IDs, an operation,
and an immutable JSON payload. Device identity and numeric revisions never establish ancestry.
Business clocks and IDs are injected. Credentials and machine operational settings are excluded.

Each atomic group repeats its group ID, sorted member IDs, aggregate base heads, and SHA-256
digest of all canonical members excluding the digest field. Aggregate bases serialize as sorted
records. Canonical JSON owns deeply immutable content and rejects duplicate keys, non-finite
numbers, unsupported schemas, and lossy values. Complete groups must verify before enqueue or
acknowledgement; incomplete remote observations remain staged until reconciliation.

Same-ID identical content is idempotent. Differing content preserves the original and the incoming
variant as a durable problem; quarantined data blocks acknowledgement. Observing remote changes
does not create a local publication outbox. Materialized heads describe the provenance of local
payloads, rather than every graph tip observed. Conflict closure requires all heads in the latest
stored review. Neither tombstones nor graph history use the recovery audit's 30-day expiry.

## Publication and Undo contract

The local capture implementation records eligible completed work, its completed deductions, daily
office status and notes, assignments, deletion, and restoration with their changes in the same
transaction. Active work and its deductions stay local. A command's group carries the reviewed
parent/deduction heads so concurrent edits cannot silently combine incompatible aggregates.

Finish completion and its pending descendants use a durable 30-second publication grace deadline. Publication-attempt state
is persisted before network work and survives timeouts and restart. Undo remains a local command;
every captured completion gets a durable withdrawal, including one not yet attempted. A subsequent completion
descends from that withdrawal. Retries retain original IDs and destinations.

The transport appends complete groups as canonical JSON `stringValue` cells to dedicated
`QI_FLOW_SYNC_V2`; it never clears a shared tab, reserve rows from a client-side read, or
interpret payloads as formulas. Exact readback verifies content before acknowledging the specific
pending IDs. Network work runs outside SQLite transactions. Configuration changes and explicit disable advance a local generation and stop capture. The publication phase binds this generation to prevent obsolete publication or application to a new target. Reviewed
migration must establish baseline heads before setting `migration_complete` and enabling capture.

The adapter checks a dedicated V2 manifest before every append and preserves unsupported or
changed manifests as target-bound problems. Incomplete groups remain staged without permanently
quarantining a group that may complete on a later read. Exact verified retries acknowledge existing
records without another append. Concurrent local edits are excluded from the older job's exact
acknowledgement set. Snapshot replacement also refuses directly at the adapter boundary.

The append protocol is an engineering inference from [Google's append request](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#appendcellsrequest)
and [batch update guarantees](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate).
Synthetic fixtures verify behavior; the actual two-client scratch-workbook acceptance check remains
required before release. Publication transport alone does not validate or materialize domain data;
causal reconciliation now validates complete candidate aggregates before writes. Production
activation remains gated on reviewed migration and runtime wiring.

## Causal materialization

The graph uses explicit parent IDs and aggregate bases, rather than numeric revisions, timestamps,
or device identity. Incomplete groups and missing/mismatched/cyclic ancestry remain durable staged
state. Independent heads and concurrent parent/deduction edits become durable conflicts. A conflict
retains stable identity across repeated pulls and updates its competing heads when new data arrives.
Quarantined duplicate IDs also invalidate descendants that would otherwise claim proven ancestry.

V2 payloads require explicit known fields, aware completed intervals, valid enums and integers, and
nonnull creation/update metadata. Missing fields do not imply a legacy format. A known V1 format
must instead be transformed deliberately during migration. Upserts cannot carry implicit deletion.
Day IDs are canonical ISO dates, including tombstones.

Candidate work/deduction aggregates use the shared domain validator before any adapter write.
Invalid groups cannot partially materialize; valid unrelated components may still import. Parents
are written before children. Remote writes never create outbox echoes or replace local running
timers. Soft-deleted/withdrawn work retains immutable protocol provenance. A restored pending edit
is reconciled against remote history before publication, preventing silent resurrection.

The composed service records confirmed success only when no pending changes, conflicts, staging,
or raw problems remain. Its callbacks recheck target/generation ownership. It is not yet wired to
production Settings; guided migration and conflict choices remain required.

## Migration and release prerequisites

V1 writers must be paused and upgraded before cutover. Reviewed migration must preserve the
remote snapshot, the original sync tab, unrelated workbook content, and a local safety backup.
Every declared participant contributes and acknowledges its snapshot. Unproven differing values
remain conflicts. Stable initialization and migration IDs make interruption resumable.

Unknown schemas, malformed rows, incomplete groups, missing ancestry, conflicting IDs, and invalid
timesheet aggregates require durable, visible reconciliation. Quota failures leave pending changes
intact. Automatic scheduling remains gated on verified protocol and migration. Two-client scratch
workbook concurrency and V1 cutover remain manual release acceptance work.
