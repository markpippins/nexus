package com.aibizarchitect.nexus.v1.spring.user.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.UUID;
import java.util.Optional;

import org.junit.jupiter.api.BeforeEach;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.junit.jupiter.api.Test;

import com.aibizarchitect.nexus.v1.user.UserRegistrationDTO;
import com.aibizarchitect.nexus.v1.spring.user.model.UserRegistration;
import com.aibizarchitect.nexus.v1.spring.user.repository.UserRegistrationRepository;
import com.aibizarchitect.nexus.v1.spring.user.service.UserAccessService;

class UserRegistrationServiceTest {

    private UserRegistrationRepository userRepository;

    private UserAccessService userAccessService;
    private UserRegistration validUser;

    @BeforeEach
    void setUp() {
        userRepository = mock(UserRegistrationRepository.class);
        userAccessService = new UserAccessService(userRepository,
                new BCryptPasswordEncoder(10));

        validUser = new UserRegistration();
        validUser.setId(UUID.fromString("123e4567-e89b-12d3-a456-426614174000"));
        validUser.setAlias("testUser");
        validUser.setEmail("test@example.com");
        validUser.setIdentifier("testpass");
        // V191 format: the stored credential is a bcrypt hash — validateUser
        // verifies via PasswordEncoder.matches(), never plaintext equals.
        validUser.setPassword(new BCryptPasswordEncoder(10).encode("testpass"));
    }

    @Test
    void testLoginSuccess() {
        // Service calls findByEmail() with the first arg, not findByAlias()
        when(userRepository.findByEmail("testUser")).thenReturn(Optional.of(validUser));

        UserRegistrationDTO result = userAccessService.validateUser("testUser", "testpass");

        assertNotNull(result);
        assertEquals("123e4567-e89b-12d3-a456-426614174000", result.getId());
        assertEquals("testUser", result.getAlias());
    }

    @Test
    void testLoginFailureUserNotFound() {
        when(userRepository.findByEmail("nonexistent")).thenReturn(Optional.empty());

        UserRegistrationDTO result = userAccessService.validateUser("nonexistent", "password123");

        assertNull(result);
    }
}
