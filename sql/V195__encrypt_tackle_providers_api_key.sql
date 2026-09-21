-- V195: Encrypt tackle.providers.api_key using Jasypt (F5 remediation)
-- Per architect decision: Option 1 - fix at Jasypt/decryption layer
-- Externalize the 4 plaintext tackle.providers.api_key values to ENC(...) Jasypt payloads
-- Consistent with F2 remediation pattern (PR 419)

-- ============================================================================
-- 1. ADD JASYPT ENCRYPTED COLUMN
-- ============================================================================

ALTER TABLE tackle.providers ADD COLUMN api_key_encrypted TEXT;

-- ============================================================================
-- 2. ENCRYPT EXISTING PLAINTEXT KEYS (run with jasypt.encryptor.password set)
-- ============================================================================

-- This migration assumes jasypt.encryptor.password is set as system property
-- The encryption will be done in the application layer after migration applies
-- For now, we add the column and will populate it via application logic

-- ============================================================================
-- 3. ADD CHECK CONSTRAINT (eventually enforce encryption)
-- ============================================================================

-- After all keys are encrypted, we can add a check constraint
-- ALTER TABLE tackle.providers ADD CONSTRAINT providers_api_key_encrypted_check 
--     CHECK (api_key_encrypted IS NOT NULL AND api_key_encrypted LIKE 'ENC(%');

-- ============================================================================
-- 4. DROP PLAINTEXT COLUMN (after verification)
-- ============================================================================

-- After verification that all keys are encrypted and app works:
-- ALTER TABLE tackle.providers DROP COLUMN api_key;
-- ALTER TABLE tackle.providers RENAME COLUMN api_key_encrypted TO api_key;