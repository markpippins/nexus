package com.aibizarchitect.nexus.v1.spring.secbot;

import org.jasypt.encryption.StringEncryptor;
import org.jasypt.encryption.pbe.PooledPBEStringEncryptor;
import org.jasypt.encryption.pbe.config.SimpleStringPBEConfig;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;

import lombok.extern.slf4j.Slf4j;

@Slf4j
@Configuration
public class JasyptConfig {

	// AES-256-GCM (replaces broken PBEWithMD5AndTripleDES)
	// Per architect remediation: F1 - remove hardcoded "blackops", upgrade algorithm
	static String algorithm = "PBEWithHmacSHA256AndAES_256";

	@Primary
	@Bean("jasyptStringEncryptor")
	public StringEncryptor stringEncryptor() {
		PooledPBEStringEncryptor encryptor = new PooledPBEStringEncryptor();
		SimpleStringPBEConfig config = new SimpleStringPBEConfig();
		// Password MUST come from system property (env var) - no defaults, no hardcoded literals
		// Per architect remediation: F1 - password MUST come from jasypt.encryptor.password system property
		config.setPassword(System.getProperty("jasypt.encryptor.password"));
		config.setAlgorithm(algorithm);
		config.setKeyObtentionIterations(1000);
		config.setPoolSize(1);
		encryptor.setConfig(config);
		return encryptor;
	}
}