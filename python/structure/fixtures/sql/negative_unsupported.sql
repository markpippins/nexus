-- Negative fixture: every statement in this file is outside the grammar.
-- Each must yield exactly one explicit unsupported observation WITH
-- diagnostics naming the construct. Nothing may be silently dropped, and
-- nothing inside these constructs may become a fact.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE OR REPLACE VIEW active_roles AS
    SELECT * FROM role_memory WHERE value IS NOT NULL;

DO $admin$
BEGIN
  EXECUTE 'CREATE INDEX idx_temp ON temp_table (col);';
  INSERT INTO audit_log VALUES ('did things');
END;
$admin$;

EXECUTE format('ALTER TABLE %I ADD COLUMN c int', 'some_table');

CREATE TRIGGER trg_touch BEFORE INSERT ON role_memory
FOR EACH ROW EXECUTE FUNCTION touch_role_memory();
