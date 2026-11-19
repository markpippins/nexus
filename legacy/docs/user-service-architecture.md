# Atomic Platform Architecture: User Service Design

## Overview

The Atomic platform implements a sophisticated dual-architecture approach for user management, featuring both a primary `user-service` for social features and a `user-access-service` for legacy client compatibility. This document explains the rationale, implementation, and integration of these services.

## Architecture Rationale

### The Challenge

Modern web clients often require numeric user IDs for compatibility with existing codebases, while MongoDB's document-based approach works optimally with String-based document IDs. The platform needed to support both requirements without forcing breaking changes to client code.

### The Solution

The Atomic platform implements a dual service architecture:
- **user-service**: Primary user management for social features (posts, comments, reactions)
- **user-access-service**: Legacy compatibility service with dual ID system

## Service Architecture

### user-service

Primary user service using MongoDB with native document approach:

- **Database**: MongoDB
- **Purpose**: Social features and user-generated content
- **Collections**: users, posts, comments, reactions, forums, etc.
- **ID System**: Standard MongoDB ObjectId (String)

### user-access-service

Compatibility service with dual ID system:

- **Database**: MongoDB  
- **Purpose**: Maintain client compatibility while using document storage
- **Model**: `ValidUser`
- **ID Architecture**:
  - `mongoId`: Internal MongoDB document ID (String, annotated with `@Id`)
  - `id`: Long ID for client compatibility (converted to String in UserDTO)

## Dual ID Implementation

### ValidUser Model

The `ValidUser` model implements a dual ID system:

```java
@Document(collection = "users")
public class ValidUser implements Serializable {
    @Id
    private String mongoId;        // Internal MongoDB storage ID
    
    private Long id;               // Client-facing ID (for compatibility)
    
    // Other fields...
}
```

### UserService Implementation

The service layer manages the dual ID system:

- **Sequential ID Generation**: Uses `AtomicLong` to generate sequential Long IDs
- **Lookup Methods**: Can find users by either MongoDB ID or Long ID
- **DTO Transformation**: Converts Long ID to String in UserDTO for client compatibility

## Data Flow

### User Creation
1. Client requests user creation
2. UserService generates sequential Long ID
3. MongoDB assigns String mongoId
4. Both IDs stored in ValidUser document
5. UserDTO returned with Long ID (as String) for client compatibility

### User Retrieval
1. Client requests user by Long ID
2. Service looks up by Long ID (scanning all documents)
3. Alternatively, internal services can use MongoDB ID for direct lookup
4. UserDTO returned with correct client-facing ID

## Integration Points

### Login Service
- Depends on `user-access-service` for authentication
- Uses UserService to validate credentials
- Returns UserDTO with Long ID (as String) for client compatibility

### Broker Integration
- Both services expose operations through broker pattern
- Operations available through `@BrokerOperation` annotations
- Requests routed via broker service to appropriate service

## Migration Path

### Current State
- `user-access-service` provides backward compatibility
- `user-service` handles social features
- Both use MongoDB but with different approaches

### Future State
- `user-access-service` acts as placeholder for AIM integration
- Clients gradually migrate to new ID structure if needed
- Primary user management may consolidate to single service

## Database Configuration

### MongoDB Setup
Both services connect to MongoDB with similar configurations:
```
spring.data.mongodb.uri=mongodb://mongoUser:somePassword@localhost:27017/userservice?authSource=admin
```

### Docker Command
Use provided scripts to start MongoDB:
- `mongodb-docker-start.bat` (Windows)
- `./mongodb-docker-start.sh` (Linux/Mac)

## Development Guidelines

### Adding New User Features
- Social features (posts, comments, etc.): Implement in `user-service`
- Basic user management with client compatibility: Implement in `user-access-service`

### Client Integration
- New clients can use either ID system
- Existing clients continue to work with Long ID system
- UserDTO.id field always contains client-compatible ID

### Testing
- Unit tests cover dual ID system functionality
- Integration tests verify proper ID handling
- End-to-end tests ensure client compatibility

## Security Considerations

- Both services implement similar security measures
- Password handling consistent across services
- Authentication flow maintains security standards
- ID exposure limited to necessary contexts

## Performance Notes

- Dual ID lookup (by Long ID) requires collection scan
- For performance-critical operations, use MongoDB ID directly
- Consider indexing Long ID field if lookup performance becomes critical
- Sequential ID generation provides predictable ID space

This architecture allows the Atomic platform to maintain backward compatibility with existing web clients while leveraging MongoDB's document-based approach for modern application features.