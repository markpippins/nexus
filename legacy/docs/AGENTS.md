# AGENTS.md - Agent Coding Guidelines for Atomic Platform

This document provides build/test commands and code style guidelines for agentic coding agents working on the Atomic Platform.

## Build / Lint / Test Commands

### Java (Spring Boot Services)
Build: `cd spring/<service-name> && ./mvnw clean compile`
Test (all): `cd spring/<service-name> && ./mvnw test`
Test (single class): `cd spring/<service-name> && ./mvnw test -Dtest=ClassName`
Test (single method): `cd spring/<service-name> && ./mvnw test -Dtest=ClassName#methodName`
Package: `cd spring/<service-name> && ./mvnw clean package`
Run: `cd spring/<service-name> && ./mvnw spring-boot:run`

### TypeScript/Node.js
Build: `cd node/<service-name> && npm run build`
Dev: `cd node/<service-name> && npm run dev`
Start: `cd node/<service-name> && npm start`

### Python
Run: `cd python/<service-name> && python -m app.main` or use start script
Test: `pytest`

### Go
Build: `cd go/<service-name> && go build`
Test: `cd go/<service-name> && go test`
Test (single): `cd go/<service-name> && go test -run TestFunctionName`

### Multi-Project Orchestration
Full platform: `docker-compose up --build`
Make targets: `make help` (see Makefile for all targets)

## Java Code Style (Spring Boot)

### Imports
Group by standard (java/jakarta), third-party, internal. No wildcard imports. Static imports last only for constants/assertions.

### Naming
Classes: PascalCase (`BrokerGatewayApplication`), Methods: camelCase (`getServiceDetails`)
Constants: UPPER_SNAKE_CASE (`MAX_RETRIES`), Variables: camelCase
Packages: lowercase, com.angrysurfer.atomic.*

### Types
Use interfaces (ServiceRequest) over implementations, Optional<T> for nullable returns
Use @Nullable/@NonNull annotations, prefer specific types over generics

### Error Handling
Never return null - throw exceptions or return Optional
Use specific exceptions (ServiceNotFoundException, BadRequestException)
Log errors at ERROR with context, return proper HTTP status codes
Use @ControllerAdvice for global exception handling

### Patterns
Lombok (@Data, @Builder) for boilerplate reduction
SLF4J: `private static final Logger log = LoggerFactory.getLogger(ClassName.class)`
Constructor injection, @Service for services, @RestController for controllers, @Configuration for configs

### Testing
JUnit 5 (@Test) with Mockito (@Mock, @ExtendWith(MockitoExtension.class))
Given-When-Then pattern, suffix: *Test.java, *IntegrationTest.java, *E2ETest.java

## TypeScript/Node.js Code Style

### Imports
ES6 imports only (no require()), third-party first then local, group related imports

### Naming
Interfaces/Types: PascalCase (`ServiceRequest`), Classes: PascalCase
Functions/Variables: camelCase, Constants: UPPER_SNAKE_CASE
Private members: prefix underscore (`_internalMethod`)

### Types
Always specify return types, interface for object shapes, type for unions/primitives
`any` is forbidden - use `unknown` with type guards

### Error Handling
Include error annotations: `catch (error: any)`, log with winston .error()
Return consistent error response format, never throw from async handlers (wrap in try/catch)

### Patterns
async/await (avoid .then()), Winston logging, Express middleware
Use interfaces for service contracts, `any` type only at Express handler boundaries

## Python Code Style

### Imports
Standard library first, then third-party, then local. No wildcard imports. Sort alphabetically.

### Naming
Classes: PascalCase, Functions/Variables: snake_case, Constants: UPPER_SNAKE_CASE
Private methods: prefix underscore

### Types
Dataclasses for models, type hints (Optional[str], Dict[str, Any])
Use typing module, no type comments

### Error Handling
Specific exceptions over generic Exception, return (bool, error) tuples
Log errors with logging module, never swallow silently

### Patterns
FastAPI with pydantic, `self.logger = logging.getLogger(__name__)`, requests.Session()
Context managers for resources

### Testing
pytest convention (test_*.py files)

## Go Code Style

### Imports
Group by standard library, third-party, local. No unused imports.

### Naming
Interfaces: -er suffix (Reader, Writer)
Exported: PascalCase, Unexported: camelCase, Constants: PascalCase/camelCase

### Types
Struct for data models, error for custom errors, interface for contracts
Pointer receivers for mutations, value for immutability

### Error Handling
Never ignore errors (always check err), return errors as last value
Wrap with fmt.Errorf, use defer for cleanup

### Patterns
Standard log package, context-aware HTTP clients, JSON struct tags

### Testing
Test files: *_test.go, Test functions: TestFunctionName, table-driven tests

## Common Patterns

### Logging
Include service/component name, use structured logging, log at appropriate levels (DEBUG/INFO/WARN/ERROR), include requestId for tracing

### HTTP Clients
Set timeouts, use connection pooling/reuse, handle network errors gracefully, include version headers

### Database Access
Repository pattern, transactions for multi-step ops, handle connection errors, use connection pooling

### Service Communication
REST for external, JSON for data exchange, include correlation/request IDs, implement circuit breakers
