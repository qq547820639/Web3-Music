-- 007_ledger_lock_privileges.sql
-- The ledger domain (services/api/app/domain/ledger.py and services/worker/ledger.py)
-- serializes per-workspace ledger operations with SELECT ... FOR UPDATE on
-- ledger_accounts and ledger_transactions. PostgreSQL requires the UPDATE
-- privilege for FOR UPDATE row locks, but migration 001 only granted SELECT on
-- ledger_accounts and SELECT,INSERT on ledger_transactions. Without UPDATE, the
-- first credit hold raises "permission denied for table ledger_accounts".
-- Grant the missing UPDATE privilege to both application roles so they can take
-- those locks (the accounts/transactions are only locked, never updated).

GRANT UPDATE ON ledger_accounts TO music_app, music_worker;
GRANT UPDATE ON ledger_transactions TO music_app, music_worker;
