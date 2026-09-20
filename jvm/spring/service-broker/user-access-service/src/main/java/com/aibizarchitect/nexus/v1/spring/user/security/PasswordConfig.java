package com.aibizarchitect.nexus.v1.spring.user.security;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;

/**
 * Password hashing for the Spring auth surfaces (issue 59bcd3da remediation).
 *
 * BCrypt at rest is enforced end to end: V191 backfills existing plaintext
 * rows (assembly.users + gateway.users), the born-clean CHECK constraints
 * (users_password_bcrypt_check) reject any plaintext write at the database,
 * and this encoder is the only sanctioned way to produce or verify a stored
 * hash. spring-security-crypto is already on the gateway classpath; this
 * adds the explicit (small) dependency to the module that owns the auth
 * surface.
 */
@Configuration
public class PasswordConfig {

    @Bean
    public PasswordEncoder passwordEncoder() {
        // Cost 10 matches V191's gen_salt('bf', 10); ~100ms/verify keeps
        // login latency negligible while staying current guidance.
        return new BCryptPasswordEncoder(10);
    }
}
