package com.aibizarchitect.nexus.v1.spring.user.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import com.aibizarchitect.nexus.v1.spring.broker.spi.BrokerOperation;
import com.aibizarchitect.nexus.v1.spring.broker.spi.BrokerParam;
import com.aibizarchitect.nexus.v1.user.UserRegistrationDTO;
import com.aibizarchitect.nexus.v1.spring.user.model.UserRegistration;
import com.aibizarchitect.nexus.v1.spring.user.repository.UserRegistrationRepository;

@Service("userAccessService")
public class UserAccessService {

    private static final Logger log = LoggerFactory.getLogger(UserAccessService.class);

    private final UserRegistrationRepository userRepository;
    private final PasswordEncoder passwordEncoder;

    public UserAccessService(UserRegistrationRepository userRepository,
            PasswordEncoder passwordEncoder) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
        log.info("UserAccessService initialized (bcrypt verification)");
    }

    /**
     * Credential verification against assembly.users — the live auth surface.
     *
     * Issue 59bcd3da remediation: was `password.equals(userReg.getPassword())`
     * over a PLAINTEXT column. Since V191 the column stores only bcrypt hashes
     * (born-clean CHECK), so verification is constant-time-ish
     * BCryptPasswordEncoder.matches() over the stored hash.
     *
     * Timing side-channel hardening: an unknown alias still consumes one
     * matches() pass against a dummy hash so response latency does not
     * reveal account existence.
     */
    private static final String DUMMY_HASH =
            "$2a$10$N9qo8uLOickgx2ZMRZoMye.IjPeGqBQVLfJ9X0nYbRJRBQ8RfV9Aa";

    @BrokerOperation("validateUser")
    public UserRegistrationDTO validateUser(@BrokerParam("email") String email,
            @BrokerParam("identifier") String password) {

        log.info("Validating user {}", email);

        if (email == null || email.isBlank()
                || password == null || password.isBlank()) {
            return null;
        }

        UserRegistration userReg = userRepository.findByEmail(email).orElse(null);

        if (userReg == null) {
            // Equalize work factor for unknown users (no user enumeration).
            passwordEncoder.matches(password, DUMMY_HASH);
            return null;
        }

        if (!passwordEncoder.matches(password, userReg.getPassword())) {
            log.info("Password mismatch for user {}", email);
            return null;
        }

        return userReg.toDTO();
    }
}
