
# Broker Service and Gateway

This document provides a detailed explanation of the broker service and gateway, including the API, SPI, and usage examples.

## Overview

The broker service acts as a central hub for dispatching requests to various backend services. It provides a single entry point for clients and routes requests to the appropriate service based on the service name and operation specified in the request. The broker gateway is a Spring Boot application that exposes the broker service as a REST API.

## Architecture

The broker system is composed of the following modules:

-   `broker-service-api`: Defines the data structures for requests and responses.
-   `broker-service-spi`: Defines the annotations used to mark services and their operations.
-   `broker-service`: The core implementation of the broker.
-   `broker-gateway`: A Spring Boot application that exposes the broker as a REST API.

### Broker Service API (`broker-service-api`)

The API module defines the following classes:

-   `ServiceRequest`: Represents a request to the broker. It contains the following fields:
    -   `service`: The name of the service to call.
    -   `operation`: The name of the operation to perform.
    -   `params`: A map of parameters for the operation.
    -   `requestId`: A unique identifier for the request.
-   `ServiceResponse`: Represents a response from the broker. It contains the following fields:
    -   `ok`: A boolean indicating whether the request was successful.
    -   `data`: The result of the operation.
    -   `errors`: A list of errors that occurred during the operation.
    -   `requestId`: The ID of the original request.
    -   `ts`: A timestamp indicating when the response was generated.
    -   `version`: The version of the API.
    -   `service`: The name of the service that was called.
    -   `operation`: The name of the operation that was performed.

### Broker Service SPI (`broker-service-spi`)

The SPI module defines the following annotations:

-   `@BrokerOperation`: Marks a method in a service as an operation that can be called by the broker. The value of the annotation is the name of the operation. If the value is not specified, the name of the method is used.
-   `@BrokerParam`: Marks a parameter of a broker operation. The value of the annotation is the name of the parameter.

### Broker Service (`broker-service`)

The broker service is the core of the system. It is responsible for the following:

-   Receiving `ServiceRequest` objects.
-   Resolving the service bean and method to call based on the `service` and `operation` fields in the request.
-   Binding the parameters from the `params` map to the method arguments.
-   Invoking the method.
-   Wrapping the result in a `ServiceResponse` object.
-   Handling exceptions and returning appropriate error responses.

The `BrokerController` class is a Spring `RestController` that exposes the broker service as a REST API. It has a single endpoint, `/api/broker/submitRequest`, which accepts a `ServiceRequest` object and returns a `ServiceResponse` object.

### Broker Gateway (`broker-gateway`)

The broker gateway is a Spring Boot application that packages the broker service and exposes it as a standalone service. It includes the `broker-service` module as a dependency and enables component scanning for the `com.angrysurfer` package, which allows it to find and initialize the `BrokerController`.

## Usage

To use the broker, you need to create a service that exposes one or more operations using the `@BrokerOperation` and `@BrokerParam` annotations. Then, you can send a `ServiceRequest` to the broker gateway to call one of the operations.

### Example: File Service

The `file-service` module provides a good example of how to create a service that can be called by the broker. The `RestFsService` class exposes several file system operations, such as `listFiles`, `createDirectory`, and `createFile`.

Here is an example of how to call the `listFiles` operation on the `RestFsService`:

```json
{
    "service": "restFsService",
    "operation": "listFiles",
    "params": {
        "alias": "my-alias",
        "path": ["/path/to/directory"]
    },
    "requestId": "my-request-id"
}
```

This request would be sent to the `/api/broker/submitRequest` endpoint of the broker gateway. The broker would then resolve the `restFsService` bean, find the `listFiles` method, and invoke it with the specified parameters. The result of the operation would be returned in a `ServiceResponse` object.

Here is an example of how to call the `copy` operation on the `RestFsService`:

```json
{
    "service": "restFsService",
    "operation": "copy",
    "params": {
        "fromAlias": "my-alias",
        "fromPath": ["/path/to/source/file"],
        "toAlias": "my-alias",
        "toPath": ["/path/to/destination/file"]
    },
    "requestId": "my-request-id"
}
```

### Example: Login Service Integration

The `login-service` integrates with the broker and calls operations on the `user-access-service`. When a login request is made, the `loginService` calls user-related operations from `user-access-service`:

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

The login service uses the UserService from `user-access-service` which manages users in MongoDB with a dual ID system (Long ID for client compatibility and String mongoId for database storage).

### Calling the Broker from a Java Client

The `RestFsClient` and `ReactiveRestFsClient` classes in the `file-service` module demonstrate how to call the broker from a Java client. The `RestFsClient` uses Spring's `RestTemplate` to send requests to the broker, while the `ReactiveRestFsClient` uses `WebClient`.

Here is an example of how to use the `RestFsClient` to call the `listFiles` operation:

```java
RestFsClient client = new RestFsClient(new RestTemplate());
FsListResponse response = client.listFiles("my-alias", List.of("/path/to/directory"));
```

This code creates a `RestFsClient`, which sends a `ServiceRequest` to the broker and returns the `FsListResponse` from the `ServiceResponse`.

## Database Architecture

The system features a mixed database architecture:

- **MongoDB Services**: `user-service`, `user-access-service`, `broker-gateway`, and others use MongoDB for document-based storage
- **JPA Services**: Services with actual JPA entities continue to use JPA with MySQL
- **Configuration**: Some services previously configured for JPA have had those configurations commented out since they don't contain JPA entities
