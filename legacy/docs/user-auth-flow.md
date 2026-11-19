# User Authentication Flow

Complete documentation of the user authentication and authorization flow in the Atomic Platform.

## Authentication Sequence

### 1. User Login Process

```
Client → login-service → user-access-service
```

1. Client sends credentials (alias and password) to login-service
2. login-service validates credentials via user-access-service
3. If valid, login-service generates a UUID token
4. Token is stored in login-service's ConcurrentHashMap with user data
5. Token is returned to client

### 2. File Operations

```
Client → file-service → login-service → User Validation → File Operation
```

1. Client sends token with file operation request
2. file-service calls login-service to validate token
3. If token is valid, gets user alias from stored data
4. Performs file operation using retrieved alias
5. Returns result to client

### 3. Logout Process

```
Client → login-service → Token Cleanup
```

1. Client sends logout request with token
2. login-service removes token from ConcurrentHashMap
3. Returns success/failure response

## Service Dependencies

### login-service Dependencies
- **user-access-service**: For credential validation
- **broker-service**: For request routing
- **file-service**: (indirect) For token validation in file operations

### file-service Dependencies  
- **login-service**: For token validation
- **broker-service**: For request routing
- **RestFsClient**: For actual file system operations

## Data Flow

### Authentication Data Structure
```java
class UserRegistrationDTO {
    String id;           // User's internal ID
    String alias;        // User's display name/username
    String identifier;   // Hashed password
    String email;        // User's email
    String avatarUrl;    // Profile picture URL
    // ... other fields
}
```

### Token Structure
- **Format**: UUID (Universally Unique Identifier)
- **Storage**: ConcurrentHashMap<UUID, UserRegistrationDTO> in login-service
- **Generation**: UUID.randomUUID() upon successful login
- **Expiration**: Currently no automatic expiration (would require enhancement)

## Security Considerations

### Token Security
- Tokens are securely generated UUIDs
- Tokens are stored in memory (ConcurrentHashMap) only
- No tokens are stored in persistent storage
- Tokens are invalidated immediately on logout

### Validation Process
- All file operations require valid tokens
- Token validity checked on each request
- Invalid/expired tokens result in authentication error
- No fallback to alias-based access

## Error Scenarios

### Authentication Failures
- Wrong credentials → Login returns error
- Invalid token → File operations return 401
- Expired token → File operations return error
- Malformed token → Returns validation error

### Recovery Process
- User must re-authenticate to get new token
- Old tokens become automatically invalid
- New operations use new token