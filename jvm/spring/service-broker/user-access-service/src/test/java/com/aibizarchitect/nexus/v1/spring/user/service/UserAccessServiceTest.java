package com.aibizarchitect.nexus.v1.spring.user.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.util.UUID;
import java.util.Optional;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;

import com.aibizarchitect.nexus.v1.user.UserRegistrationDTO;
import com.aibizarchitect.nexus.v1.spring.user.model.UserRegistration;
import com.aibizarchitect.nexus.v1.spring.user.repository.UserRegistrationRepository;

/**
 * Issue 59bcd3da remediation: validateUser verifies against a bcrypt HASH
 * (V191 backfill + born-clean CHECK). The plaintext-equals path these tests
 * used to pin is itself now a regression — one test asserts a plaintext
 * column value can never authenticate.
 */
@ExtendWith(MockitoExtension.class)
class UserAccessServiceTest {

    private static final String PLAINTEXT = "testpass";

    @Mock
    private UserRegistrationRepository userRepository;

    private UserAccessService userAccessService;

    private PasswordEncoder realEncoder;
    private UserRegistration testUser;

    @BeforeEach
    void setUp() {
        // Real encoder: the point is that matches() works against a genuine
        // V191-format hash and fails against any other value.
        realEncoder = new BCryptPasswordEncoder(10);
        userAccessService = new UserAccessService(userRepository, realEncoder);

        testUser = new UserRegistration();
        testUser.setAlias("testuser");
        testUser.setEmail("test@example.com");
        testUser.setIdentifier(PLAINTEXT); // legacy mirrored column, per entity contract
        testUser.setPassword(realEncoder.encode(PLAINTEXT)); // V191 format
        testUser.setId(UUID.fromString("123e4567-e89b-12d3-a456-426614174000"));
    }

    @Test
    void validateUser_WithValidCredentials_ShouldReturnUserDto() {
        when(userRepository.findByEmail("test@example.com"))
                .thenReturn(Optional.of(testUser));

        UserRegistrationDTO result = userAccessService.validateUser("test@example.com", PLAINTEXT);

        assertNotNull(result);
        assertEquals("testuser", result.getAlias());
        assertEquals("test@example.com", result.getEmail());
        assertEquals("123e4567-e89b-12d3-a456-426614174000", result.getId());
        verify(userRepository, times(1)).findByEmail("test@example.com");
    }

    @Test
    void validateUser_WithNonExistentUser_ShouldReturnNull() {
        when(userRepository.findByEmail("nonexistent@example.com"))
                .thenReturn(Optional.empty());

        assertNull(userAccessService.validateUser("nonexistent@example.com", "anyPassword"));
        verify(userRepository, times(1)).findByEmail("nonexistent@example.com");
    }

    @Test
    void validateUser_WithBlankCredentials_ShouldReturnNullWithoutQuery() {
        assertNull(userAccessService.validateUser(null, "password"));
        assertNull(userAccessService.validateUser("email@example.com", null));
        assertNull(userAccessService.validateUser("", "password"));
        assertNull(userAccessService.validateUser("email@example.com", "  "));
        // no repository interaction for blank credentials
        verifyNoInteractions(userRepository);
    }

    @Test
    void validateUser_WithWrongPassword_ShouldReturnNull() {
        when(userRepository.findByEmail("test@example.com"))
                .thenReturn(Optional.of(testUser));

        assertNull(userAccessService.validateUser("test@example.com", "wrongpass"));
        verify(userRepository, times(1)).findByEmail("test@example.com");
    }

    @Test
    void validateUser_PlaintextColumnValueCanNeverAuthenticate() {
        // Regression pin: if a row ever regresses to plaintext at rest
        // (seeder-style), matches() must NOT accept the raw column value.
        UserRegistration regressed = new UserRegistration();
        regressed.setAlias("legacy");
        regressed.setEmail("legacy@example.com");
        regressed.setIdentifier("legacypass");
        regressed.setPassword("legacypass"); // plaintext AT REST — V191 CHECK now forbids this
        when(userRepository.findByEmail("legacy@example.com"))
                .thenReturn(Optional.of(regressed));

        assertNull(userAccessService.validateUser("legacy@example.com", "legacypass"));
    }

    @Test
    void validateUser_VerificationUsesBcryptNotEquals() {
        // Structural pin: the stored hash verifies with the encoder and the
        // encoder is real bcrypt (hash format sanity, cost prefix present).
        String hash = testUser.getPassword();
        assertTrue(hash.startsWith("$2"), "V191 stored format must be bcrypt");
        assertEquals(60, hash.length());
        assertTrue(realEncoder.matches(PLAINTEXT, hash));
    }
}
