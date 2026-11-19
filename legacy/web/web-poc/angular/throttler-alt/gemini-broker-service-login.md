# Broker Service - Authentication and Login Flow

This document details the authentication mechanism within the broker service architecture, focusing on how a client application would log in a user and use the resulting credentials to make authenticated requests to other backend services.

## Architectural Role

Authentication is handled by a dedicated microservice, let's call it `authService`. Its responsibilities are:
1.  Verifying user credentials (e.g., username/password).
2.  Issuing secure, short-lived access tokens (e.g., JSON Web Tokens - JWTs).
3.  Providing an endpoint for other services (via the API Gateway) to validate tokens.

The API Gateway / Broker is responsible for intercepting incoming requests, extracting the access token, validating it (by communicating with `authService`), and then, if valid, forwarding the request to the target service (like `restFsService`) with user context.

## 1. User Login

To log in, the client sends a request to the broker targeting the `authService`.

### Login Request

This is a standard `ServiceRequest` object.

- **Service:** `authService`
- **Operation:** `login`

```json
{
    "service": "authService",
    "operation": "login",
    "params": {
        "username": "testuser",
        "password": "securepassword123"
    },
    "requestId": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
}
```

### Successful Login Response

If the credentials are valid, the `authService` returns a `ServiceResponse` containing an access token and user information. The client is responsible for securely storing this token (e.g., in memory or `sessionStorage`).

```json
{
    "ok": true,
    "data": {
        "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IlRlc3QgVXNlciIsImlhdCI6MTUxNjIzOTAyMn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "user": {
            "id": "user-01",
            "username": "testuser",
            "displayName": "Test User"
        }
    },
    "requestId": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "ts": 1678886400000
}
```

### Failed Login Response

If the credentials are incorrect, the service returns a standard error response.

```json
{
    "ok": false,
    "errors": [
        {
            "code": "AUTH_FAILED",
            "message": "Invalid username or password."
        }
    ],
    "requestId": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "ts": 1678886401000
}
```

## 2. Making Authenticated Requests

Once the client has obtained an access token, it must include it in the `Authorization` header for all subsequent requests to protected services.

The client-side API layer (e.g., the `BrokerService` in Angular) should be modified to automatically add this header if a token is present.

**Example HTTP Header:**
```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### Authenticated Request to `restFsService`

The `ServiceRequest` body sent to the broker remains the same. The authentication is handled at the transport layer by the API Gateway.

```json
{
    "service": "restFsService",
    "operation": "listFiles",
    "params": {
        "alias": "testuser-files",
        "path": ["documents"]
    },
    "requestId": "f6e5d4c3-b2a1-4f6e-8d9c-5b4a3f2e1d0c"
}
```

### Gateway's Role in Authenticated Requests

1.  Client sends the request with the `Authorization` header.
2.  The API Gateway receives the request.
3.  It extracts the token from the header.
4.  It calls the `authService`'s internal `validateToken` operation to verify the token's signature and expiration.
5.  If the token is valid, the gateway forwards the original request to the `restFsService`. It might also inject user information (like `userId`) into the request headers for the downstream service to use.
6.  If the token is invalid or expired, the gateway immediately returns a `401 Unauthorized` HTTP status code to the client, and the request never reaches the `restFsService`.

---

# Gemini Broker Service Login Flow

This document details the login flow as invoked by the broker service, focusing on the relevant classes and the overall process.

## Overview

The login flow is handled by the `login-service` Java module via the broker service.

## Backend Authentication

The authentication process is managed by the `login-service` and `user-service` modules.

### Relevant Classes

- **`com.angrysurfer.atomic.login.LoginService`**: The entry point for the login operation via the broker.
- **`com.angrysurfer.atomic.user.service.UserService`**: Handles user-related operations, including retrieving user data from the database.
- **`com.angrysurfer.atomic.user.model.User`**: The JPA entity representing a user in the database.
- **`com.angrysurfer.atomic.user.UserDTO`**: A data transfer object representing a user, which is returned to the client.

### Login Flow

1.  A request is sent to the broker service with the `service` set to `loginService` and the `operation` set to `login`. The request parameters include the user's `alias` and `password`.

    ```json
    {
        "service": "loginService",
        "operation": "login",
        "params": {
            "alias": "user-alias",
            "identifier": "user-password"
        },
        "requestId": "my-request-id"
    }
    ```

2.  The broker service routes the request to the `login` method in the `LoginService` class, which is annotated with `@BrokerOperation("login")`.

3.  The `login` method in `LoginService` calls the `findByAlias` method of the `UserService` to retrieve the user from the database.

4.  The `UserService` uses the `UserRepository` to find the `User` entity by its alias.

5.  The `LoginService` compares the provided password with the `identifier` field of the retrieved `User` object.

6.  If the password is correct, a `LoginResponse` object is created, which contains a `UserDTO` with the user's information. This response is then wrapped in a `ServiceResponse` and returned.

7.  If the password is incorrect or the user is not found, a `LoginResponse` with an error message is returned.