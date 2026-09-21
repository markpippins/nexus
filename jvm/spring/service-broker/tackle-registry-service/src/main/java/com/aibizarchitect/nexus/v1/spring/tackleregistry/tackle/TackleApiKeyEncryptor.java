package com.aibizarchitect.nexus.v1.spring.tackleregistry.tackle;

import org.jasypt.encryption.StringEncryptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

@Component
public class TackleApiKeyEncryptor {

    private final StringEncryptor encryptor;

    @Autowired
    public TackleApiKeyEncryptor(StringEncryptor encryptor) {
        this.encryptor = encryptor;
    }

    public String encrypt(String plaintext) {
        if (plaintext == null || plaintext.isBlank()) {
            return null;
        }
        // Skip if already encrypted (ENC prefix)
        if (plaintext.startsWith("ENC(")) {
            return plaintext;
        }
        return "ENC(" + encryptor.encrypt(plaintext) + ")";
    }

    public String decrypt(String ciphertext) {
        if (ciphertext == null || ciphertext.isBlank()) {
            return null;
        }
        // Skip if not encrypted
        if (!ciphertext.startsWith("ENC(") || !ciphertext.endsWith(")")) {
            return ciphertext;
        }
        String encrypted = ciphertext.substring(4, ciphertext.length() - 1);
        return encryptor.decrypt(encrypted);
    }

    public boolean isEncrypted(String value) {
        return value != null && value.startsWith("ENC(") && value.endsWith(")");
    }
}