-- V178: role memory and governed tag infrastructure.
-- Line comments are skipped by the parser; they cannot yield facts.
-- A nested block comment: /* outer /* inner still comment */ outer tail */

CREATE TYPE tag_origin AS ENUM ('human', 'nlp', 'structural');

CREATE TABLE role_memory (
    id BIGSERIAL PRIMARY KEY,
    key TEXT NOT NULL,
    value TEXT DEFAULT '',
    recorded_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (key)
);

CREATE TABLE governed_tag (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace TEXT NOT NULL,
    tag_key TEXT NOT NULL,
    origin tag_origin NOT NULL DEFAULT 'human',
    CONSTRAINT uq_governed_tag UNIQUE (namespace, tag_key)
);

CREATE TABLE role_memory_tag (
    role_memory_id BIGINT NOT NULL REFERENCES role_memory(id) ON DELETE CASCADE,
    governed_tag_id UUID NOT NULL REFERENCES governed_tag(id),
    PRIMARY KEY (role_memory_id, governed_tag_id)
);

CREATE UNIQUE INDEX idx_governed_tag_key ON governed_tag (namespace, tag_key);
CREATE INDEX idx_role_memory_key ON role_memory USING btree (key);

ALTER TABLE role_memory ADD COLUMN weight NUMERIC DEFAULT 1.0;
ALTER TABLE governed_tag ADD CONSTRAINT fk_governed_tag_key CHECK (length(tag_key) > 0);

INSERT INTO role_memory (key, value) VALUES ('bootstrap', 'v1'), ('aspect', 'g6');

-- Procedural body below is explicitly unsupported. Its internal semicolons
-- must NOT split statements, and no facts may be claimed from inside it.
CREATE FUNCTION touch_role_memory() RETURNS trigger AS $body$
BEGIN
  UPDATE role_memory SET recorded_at = now() WHERE id = NEW.id;
  RETURN NEW;
END;
$body$ LANGUAGE plpgsql;

SELECT 1;
