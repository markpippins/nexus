# File System Server Test Suite

This test suite verifies the functionality of the file-system-server.

## Running the Tests

1. Ensure the file-system-server is running on port 4040:
   ```bash
   cd /mnt/c/dev/WORK/atomic/node/file-system-server
   FS_ROOT_DIR=/tmp/test-fs-root npx tsx fs-serv.ts
   ```

2. Run the tests:
   ```bash
   npx tsx test-fs-server.ts
   ```

## Test Coverage

The test suite covers the following operations:

- [x] Health check (`/health` endpoint)
- [x] List directory contents (`ls` operation)
- [x] Create directory (`mkdir` operation)
- [x] Create file (`newfile` operation)
- [x] Delete file (`deletefile` operation)
- [x] Delete directory (`rmdir` operation)
- [x] Rename file/directory (`rename` operation)
- [x] Check file existence (`hasfile` operation)
- [x] Check folder existence (`hasfolder` operation)
- [x] Move file/directory (`move` operation)
- [ ] Copy file/directory (`copy` operation) - *Currently failing*

## Error Handling Tests

The test suite also verifies proper error handling for:

- [x] Non-existent directories
- [x] Non-existent files
- [x] Invalid operations

## Additional Test Files

- `test-copy.ts` - Specific test for copy operation
- `test-error-conditions.ts` - Tests for error scenarios

## Known Issues

1. The `copy` operation is currently failing with a 404 error. This may be due to:
   - Path resolution issues
   - Missing implementation in the server
   - Incorrect request format

## Usage Example

```typescript
import { FileSystemServerTester } from './test-fs-server';

const tester = new FileSystemServerTester(4040);

// Health check
await tester.healthCheck();

// Create directory
await tester.createDirectory(['my_new_dir']);

// Create file
await tester.createFile(['my_new_dir'], 'my_file.txt');

// List directory contents
const contents = await tester.listDirectory(['my_new_dir']);
console.log(contents.items); // Array of file/directory objects
```