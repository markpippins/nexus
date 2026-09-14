#!/usr/bin/env bash
# system-logs-retention.sh — V161 retention pruning for tackle.system_logs.
#
# Prunes routine categories per tackle.system_logs_retention_policy; the
# audit categories (REGISTRY_AUDIT / NEBULA_AUDIT / KG_AUDIT) are structurally
# excepted (policy-table CHECK + hard WHERE exclusion in the prune function,
# plus the independent V157 erase guard). See
# sql/V161__system_logs_retention_policy.sql for the policy design.
#
# Called by config/systemd/system-logs-retention.{service,timer}.
# Env: PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE (defaults below match the
# repo unit convention, e.g. config/systemd/nexus-mesh-register.service).
set -euo pipefail

PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-pguser}"
PGPASSWORD="${PGPASSWORD:-pgpass}"
PGDATABASE="${PGDATABASE:-nexus}"
export PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE

psql -X -qAt -c "SELECT tackle.prune_system_logs(false);"
