# Runtime profile — 2026-10-04

Measured with `scripts/profile-runtime.py` on the development Windows machine using a disposable
SQLite database: 10,000 completed sessions, 10,000 lunch deductions, 5,074,944 database bytes and
30 daily backup files. No user data or real accounts are accessed. Values are medians of five
calls, useful for comparing this workload; they are not a hardware-independent performance target.

| Operation | Before | After | Work per repeated call |
| --- | ---: | ---: | --- |
| Today refresh | 27.70 ms | 26.50 ms | 24 SELECTs; no integrity scans |
| Settings backup status + list | 2,042.86 ms | 10.03 ms | 3 SELECTs; integrity scans fall from 60 to 0 |

Today remains unchanged: the existing queries select the relevant calendar range and the measured
refresh is small relative to its one-second timer. Backup status and list previously validated all
30 databases independently on every refresh. The adapter now retains only derived catalog results,
keyed by resolved folder and every file's name, size, modification/creation timestamps and inode.
The initial catalog still validates the files; subsequent unchanged reads inspect file metadata.
New or removed backups, a changed file, a replacement copy and a different folder invalidate the
catalog. A new Copenhagen day creates a new daily file and therefore invalidates it as well.

The catalog is advisory UI information. Every restore and corrupt-database recovery independently
revalidates the selected database and its staged copy; they never trust the catalog cache. A file
modified while its scan runs is detected by the next signature comparison. Tests cover reuse,
new-day creation, corruption, replacement, folder changes and removal. The cache contains neither
time-entry contents nor credentials and is never persisted.
