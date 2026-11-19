# Next.js API Tester

This project contains a test runner for verifying the functionality of the Next.js API routes that interact with the backend services.

## Overview

The testing setup consists of two main files:

-   `src/tests/test-suite.json`: A JSON file that defines the test cases in a data-driven manner.
-   `src/tests/test-runner.js`: A script that reads the test suite and executes the tests against the API endpoints.

## Running the Tests

To run the tests, follow these steps:

1.  **Ensure the Next.js application is running.** The tests require the API endpoints to be live.

2.  **Execute the test runner script.** From the `web/nextjs-api-tester` directory, run the following command:

    ```bash
    node src/tests/test-runner.js
    ```

The test runner will output the results to the console, indicating whether each test passed or failed.

## Adding New Tests

To add new tests, simply add a new test case object to the `tests` array in the `src/tests/test-suite.json` file. Each test case should have the following structure:

```json
{
    "name": "A descriptive name for the test",
    "request": {
        "alias": "home",
        "path": [],
        "operation": "ls"
    },
    "expected": {
        "status": 200
    }
}
```
