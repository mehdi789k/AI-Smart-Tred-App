# P1.5 Staging Recovery Drill

## Scope

This drill used an isolated TimescaleDB 2.13.1 / PostgreSQL 15 container,
separate database credentials, port `55432`, and a disposable container. The
existing development database on port `5432` was not modified.

## Evidence

- Alembic applied revisions `0001` through `0009` successfully.
- `0004_ohlcv_contract` now detects an existing composite primary key before
  creating a duplicate natural-key constraint.
- A custom-format dump completed in `0.530` seconds.
- The dump excluded TimescaleDB internal schemas:
  `_timescaledb_catalog`, `_timescaledb_internal`, and `_timescaledb_config`.
- Restore completed in `7.202` seconds with `--exit-on-error`.
- A restore marker row was recovered after dropping its table.
- `alembic_version` remained at `0009` after restore.
- `scripts/migrate_db.py --verify-only` completed successfully after restore.

## RTO/RPO interpretation

- Measured local recovery time (RTO proxy): `7.202` seconds for this small
  staging dataset, excluding container startup and network transfer.
- The backup timestamp is the recovery point for this drill. A production RPO
  value is not claimed because backup scheduling and WAL/archive retention were
  not configured in this local environment.

## Operational safeguards

- `scripts/restore_db.ps1` now uses `--exit-on-error`; restore failures cannot
  be reported as success.
- Backup and restore avoid TimescaleDB internal schemas, which are recreated by
  the installed extension and must not be treated as application data.
- The disposable staging container must be removed after the drill; no
  production or development database is a restore target.

## Remaining release gate

Run the same drill in managed staging with the real backup schedule, object
storage retention, WAL policy, encrypted backup artifact, and an approved
restore target. Record the resulting RPO from the backup/WAL timestamps before
closing P1.5.

## Validator and retention gate

The repeatable validator is now
`scripts\validate_recovery.ps1`. It requires an existing backup (or explicit
`-CreateBackup`), explicit `-ConfirmRestore`, a backup age no greater than the
15-minute RPO target, `pg_isready`, an `alembic_version` row, and recovery time
within the one-hour RTO target. It emits only the backup filename, age, schema
revision, elapsed time, and configured thresholds; credentials are never
included.

Retention defaults are configured through
`ORDER_AUDIT_RETENTION_DAYS`, `BROKER_RESPONSE_RETENTION_DAYS`,
`LOG_RETENTION_DAYS`, and `BACKUP_RETENTION_DAYS`, each set to 90 days. Backup
file cleanup is opt-in and restricted to `*.dump` files in the managed backup
directory by `scripts\backup_db.ps1 -ApplyRetention`.

These changes establish an executable staging gate. They do not change the
measured drill above or claim production RPO/RTO until managed scheduling,
WAL/archive retention, encryption, and the approved restore target are tested.
