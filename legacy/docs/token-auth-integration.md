# Token-Based Authentication Integration

Documentation for the integration between file-service and login-service using token-based authentication.

## Overview

The Atomic Platform now implements token-based authentication for file operations. Instead of requiring users to directly provide their alias, all file operations now use authentication tokens to verify user identity and access rights.

## Architecture

### Services Involved
- **login-service**: Generates and validates authentication tokens
- **file-service**: Verifies tokens and performs file operations on behalf of authenticated users
- **user-access-service**: Provides user validation and credential verification

### Authentication Flow
1. User authenticates with login-service to receive a token
2. User includes token in file-service requests
3. file-service validates token with login-service
4. If valid, file-service performs requested operation
5. If invalid, file-service returns authentication error

## File Service API Changes

### Previous Implementation (Alias-based)
```json
{
  "service": "restFsService",
  "operation": "listFiles",
  "params": {
    "alias": "username123",
    "path": ["documents", "private"]
  }
}
```

### Current Implementation (Token-based)
```json
{
  "service": "restFsService", 
  "operation": "listFiles",
  "params": {
    "token": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
    "path": ["documents", "private"]
  }
}
```

## Supported Operations

All file service operations now use token-based authentication:

- `listFiles` - List files in a directory
- `changeDirectory` - Change current directory
- `createDirectory` - Create a new directory  
- `removeDirectory` - Remove a directory
- `createFile` - Create a new file
- `deleteFile` - Delete a file
- `rename` - Rename a file or directory
- `copy` - Copy files between users (requires tokens for both source and destination)
- `hasFile` - Check if file exists
- `hasFolder` - Check if folder exists

## Security Benefits

1. **User Isolation**: Users can only access their own files
2. **Token Validation**: All requests are validated against login-service
3. **Session Management**: Tokens can be invalidated on logout
4. **Reduced Surface**: No longer need to expose user aliases directly

## Error Handling

### Authentication Errors
- Invalid token: Returns 401 Unauthorized
- Expired token: Returns token expiration error
- Malformed token: Returns validation error

### Access Control
- Attempt to access another user's files: Returns permission denied
- Insufficient privileges: Returns access denied