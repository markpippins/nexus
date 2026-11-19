# File System Server - Complete Test Suite

This directory contains a comprehensive test suite for the file-system-server.

## Files

- `test-fs-server.ts` - Main test suite covering all core operations
- `test-copy.ts` - Specific test for copy operation (currently failing)
- `TESTING.md` - Documentation for the test suite
- `run-tests.sh` - Script to run all tests

## Operations Tested

✅ **Core Operations:**
- Health check (`/health` endpoint)
- List directory (`ls` operation) 
- Create directory (`mkdir` operation)
- Create file (`newfile` operation)
- Delete file (`deletefile` operation)
- Delete directory (`rmdir` operation)
- Rename file/directory (`rename` operation)
- Check file existence (`hasfile` operation)
- Check folder existence (`hasfolder` operation)
- Move file/directory (`move` operation)

❌ **Known Issue:**
- Copy file/directory (`copy` operation) - Currently failing with 404 error

## Running Tests

```bash
cd /mnt/c/dev/WORK/atomic/node/file-system-server
./run-tests.sh
```

## Server Requirements

- Node.js with TypeScript support
- Access to test directory (default: `/tmp/test-fs-root`)
- Port 4040 available

## Test Results

The test suite successfully validates that the file-system-server is working correctly for all operations except the copy operation. The basic functionality for file and directory management is working as expected.

## Fix for Copy Operation

The copy operation may require debugging in the server code. The issue could be:
1. Incorrect path resolution
2. Missing implementation in the switch statement
3. Incorrect request format from the client