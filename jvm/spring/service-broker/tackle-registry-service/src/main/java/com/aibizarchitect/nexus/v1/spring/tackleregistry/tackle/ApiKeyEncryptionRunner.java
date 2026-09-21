package com.aibizarchitect.nexus.v1.spring.tackleregistry.tackle;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.CommandLineRunner;
import org.springframework.context.annotation.Profile;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * One-time runner to encrypt existing plaintext API keys in the providers table.
 * Run once after deploying the Jasypt configuration with the keystore password.
 * Enable with: -Dspring.profiles.active=encrypt-api-keys
 * After successful encryption, this runner can be removed or disabled.
 */
@Component
@Profile("encrypt-api-keys")
public class ApiKeyEncryptionRunner implements CommandLineRunner {

    private final JdbcTemplate jdbc;
    private final TackleApiKeyEncryptor encryptor;

    @Autowired
    public ApiKeyEncryptionRunner(JdbcTemplate jdbc, TackleApiKeyEncryptor encryptor) {
        this.jdbc = jdbc;
        this.encryptor = encryptor;
    }

    @Override
    public void run(String... args) {
        System.out.println("Starting API key encryption for tackle.providers...");

        List<String> providerIds = jdbc.queryForList(
                "SELECT id FROM tackle.providers WHERE api_key IS NOT NULL AND api_key NOT LIKE 'ENC(%'",
                String.class);

        if (providerIds.isEmpty()) {
            System.out.println("No plaintext API keys found to encrypt.");
            return;
        }

        int encryptedCount = 0;
        for (String id : providerIds) {
            String plaintext = jdbc.queryForObject(
                    "SELECT api_key FROM tackle.providers WHERE id = ?",
                    String.class, id);

            if (plaintext != null && !plaintext.isBlank() && !plaintext.startsWith("ENC(")) {
                String encrypted = "ENC(" + encryptor.encrypt(plaintext) + ")";
                int updated = jdbc.update(
                        "UPDATE tackle.providers SET api_key = ? WHERE id = ?",
                        encrypted, id);

                if (updated > 0) {
                    System.out.println("Encrypted API key for provider: " + id);
                    encryptedCount++;
                }
            }
        }

        System.out.println("Encryption complete. " + encryptedCount + " API keys encrypted.");
    }
}