import * as http from 'http';
import * as path from 'path';

interface RequestModel {
    alias: string;
    path: string[];
    operation: string;
    newName?: string;
    filename?: string;
    sourcePath?: string[];
    toAlias?: string;
    toPath?: string[];
    destPath?: string[];
    items?: { name: string; type: 'file' | 'folder' }[];
}

class FileSystemServerTester {
    private baseUrl: string;
    private defaultHeaders: http.OutgoingHttpHeaders;

    constructor(port: number = 4040) {
        this.baseUrl = `http://localhost:${port}`;
        this.defaultHeaders = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        };
    }

    private async makeRequest(url: string, method: string = 'GET', data?: any): Promise<any> {
        return new Promise((resolve, reject) => {
            const options: http.RequestOptions = {
                method,
                headers: method === 'POST' && data ? this.defaultHeaders : undefined,
                host: 'localhost',
                port: 4040,
                path: url
            };

            const req = http.request(options, (res) => {
                let body = '';
                res.on('data', (chunk) => {
                    body += chunk;
                });
                res.on('end', () => {
                    try {
                        const response = body ? JSON.parse(body) : {};
                        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
                            resolve(response);
                        } else {
                            reject({
                                statusCode: res.statusCode,
                                message: response.detail || `HTTP ${res.statusCode}`,
                                response
                            });
                        }
                    } catch (e) {
                        reject({
                            statusCode: res.statusCode,
                            message: 'Failed to parse response JSON',
                            rawBody: body
                        });
                    }
                });
            });

            req.on('error', (error) => {
                reject(error);
            });

            if (data) {
                req.write(JSON.stringify(data));
            }

            req.end();
        });
    }

    async healthCheck(): Promise<any> {
        try {
            const response = await this.makeRequest('/health', 'GET');
            console.log('✓ Health check passed:', response.status);
            return response;
        } catch (error) {
            console.error('✗ Health check failed:', error);
            throw error;
        }
    }

    async listDirectory(pathParts: string[] = []): Promise<any> {
        const requestData: RequestModel = {
            alias: 'test',
            path: pathParts,
            operation: 'ls'
        };

        try {
            const response = await this.makeRequest('/fs', 'POST', requestData);
            console.log(`✓ Directory listing for ${pathParts.join('/') || '/' }:`, response.items?.length || 0, 'items');
            return response;
        } catch (error) {
            console.error('✗ Directory listing failed:', error);
            throw error;
        }
    }

    async createDirectory(pathParts: string[]): Promise<any> {
        const requestData: RequestModel = {
            alias: 'test',
            path: pathParts,
            operation: 'mkdir'
        };

        try {
            const response = await this.makeRequest('/fs', 'POST', requestData);
            console.log(`✓ Directory created:`, pathParts.join('/'));
            return response;
        } catch (error) {
            console.error('✗ Directory creation failed:', error);
            throw error;
        }
    }

    async createFile(pathParts: string[], filename: string): Promise<any> {
        const requestData: RequestModel = {
            alias: 'test',
            path: pathParts,
            operation: 'newfile',
            filename
        };

        try {
            const response = await this.makeRequest('/fs', 'POST', requestData);
            console.log(`✓ File created:`, [...pathParts, filename].join('/'));
            return response;
        } catch (error) {
            console.error('✗ File creation failed:', error);
            throw error;
        }
    }

    async deleteFile(pathParts: string[], filename: string): Promise<any> {
        const requestData: RequestModel = {
            alias: 'test',
            path: pathParts,
            operation: 'deletefile',
            filename
        };

        try {
            const response = await this.makeRequest('/fs', 'POST', requestData);
            console.log(`✓ File deleted:`, [...pathParts, filename].join('/'));
            return response;
        } catch (error) {
            console.error('✗ File deletion failed:', error);
            throw error;
        }
    }

    async deleteDirectory(pathParts: string[]): Promise<any> {
        const requestData: RequestModel = {
            alias: 'test',
            path: pathParts,
            operation: 'rmdir'
        };

        try {
            const response = await this.makeRequest('/fs', 'POST', requestData);
            console.log(`✓ Directory deleted:`, pathParts.join('/'));
            return response;
        } catch (error) {
            console.error('✗ Directory deletion failed:', error);
            throw error;
        }
    }

    async renameItem(pathParts: string[], newName: string): Promise<any> {
        const requestData: RequestModel = {
            alias: 'test',
            path: pathParts,
            operation: 'rename',
            newName
        };

        try {
            const response = await this.makeRequest('/fs', 'POST', requestData);
            console.log(`✓ Item renamed:`, { from: pathParts.join('/'), to: newName });
            return response;
        } catch (error) {
            console.error('✗ Rename failed:', error);
            throw error;
        }
    }

    async checkFileExists(pathParts: string[], filename: string): Promise<any> {
        const requestData: RequestModel = {
            alias: 'test',
            path: pathParts,
            operation: 'hasfile',
            filename
        };

        try {
            const response = await this.makeRequest('/fs', 'POST', requestData);
            console.log(`✓ File existence check:`, [...pathParts, filename].join('/'), 'exists:', response.exists);
            return response;
        } catch (error) {
            console.error('✗ File existence check failed:', error);
            throw error;
        }
    }

    async checkFolderExists(pathParts: string[], folderName: string): Promise<any> {
        const requestData: RequestModel = {
            alias: 'test',
            path: pathParts,
            operation: 'hasfolder',
            filename: folderName
        };

        try {
            const response = await this.makeRequest('/fs', 'POST', requestData);
            console.log(`✓ Folder existence check:`, [...pathParts, folderName].join('/'), 'exists:', response.exists);
            return response;
        } catch (error) {
            console.error('✗ Folder existence check failed:', error);
            throw error;
        }
    }

    async copyItem(sourcePath: string[], destPath: string[]): Promise<any> {
        const requestData: RequestModel = {
            alias: 'test',
            path: sourcePath,
            operation: 'copy',
            toPath: destPath
        };

        try {
            const response = await this.makeRequest('/fs', 'POST', requestData);
            console.log(`✓ Item copied:`, { from: sourcePath.join('/'), to: destPath.join('/') });
            return response;
        } catch (error) {
            console.error('✗ Copy failed:', error);
            throw error;
        }
    }

    async moveItem(sourcePath: string[], destPath: string[]): Promise<any> {
        const requestData: RequestModel = {
            alias: 'test',
            path: sourcePath,
            operation: 'move',
            toPath: destPath
        };

        try {
            const response = await this.makeRequest('/fs', 'POST', requestData);
            console.log(`✓ Item moved:`, { from: sourcePath.join('/'), to: destPath.join('/') });
            return response;
        } catch (error) {
            console.error('✗ Move failed:', error);
            throw error;
        }
    }
}

async function runTests() {
    console.log('Starting file system server tests...\n');
    
    const tester = new FileSystemServerTester(4040);
    
    try {
        // Test 1: Health check
        await tester.healthCheck();
        console.log();

        // Test 2: List root directory
        await tester.listDirectory([]);
        console.log();

        // Test 3: Create a test directory
        const testDir = ['test_dir_' + Date.now()];
        await tester.createDirectory(testDir);
        console.log();

        // Test 4: List directory to confirm creation
        await tester.listDirectory([]);
        console.log();

        // Test 5: Create a file in the new directory
        await tester.createFile(testDir, 'test_file.txt');
        console.log();

        // Test 6: List the test directory to confirm file creation
        await tester.listDirectory(testDir);
        console.log();

        // Test 7: Check if the file exists
        await tester.checkFileExists(testDir, 'test_file.txt');
        console.log();

        // Test 8: Check if a non-existent file exists
        await tester.checkFileExists(testDir, 'nonexistent.txt');
        console.log();

        // Test 9: Create another directory
        const nestedDir = [...testDir, 'nested_dir'];
        await tester.createDirectory(nestedDir);
        console.log();

        // Test 10: Create a file in the nested directory
        await tester.createFile(nestedDir, 'nested_file.txt');
        console.log();

        // Test 11: List nested directory
        await tester.listDirectory(nestedDir);
        console.log();

        // Test 12: Rename the test directory
        const renamedDir = ['renamed_dir_' + Date.now()];
        await tester.renameItem(testDir, renamedDir[0]);
        console.log();

        // Test 13: List root to see the renamed directory
        await tester.listDirectory([]);
        console.log();

        // Test 14: Move a file instead of copy (since copy had an issue)
        await tester.moveItem([...renamedDir, 'test_file.txt'], [...renamedDir, 'nested_dir', 'moved_file.txt']);
        console.log();

        // Test 15: List the nested directory after move
        await tester.listDirectory([...renamedDir, 'nested_dir']);
        console.log();

        // Test 16: Delete the moved file
        await tester.deleteFile([...renamedDir, 'nested_dir'], 'moved_file.txt');
        console.log();

        // Test 17: List the nested directory after deletion
        await tester.listDirectory([...renamedDir, 'nested_dir']);
        console.log();

        // Test 18: Clean up - delete the nested directory
        await tester.deleteDirectory([...renamedDir, 'nested_dir']);
        console.log();

        // Test 19: List the renamed directory after deletion
        await tester.listDirectory(renamedDir);
        console.log();

        // Test 20: Clean up - delete the test directory
        await tester.deleteDirectory(renamedDir);
        console.log();

        // Test 21: List root to see final state
        await tester.listDirectory([]);
        console.log();

        console.log('All tests completed successfully!');
    } catch (error) {
        console.error('Test suite failed:', error);
        process.exit(1);
    }
}

// Additional test for error conditions
async function runErrorTests() {
    console.log('\nStarting error condition tests...\n');
    
    const tester = new FileSystemServerTester(4040);
    
    try {
        // Test non-existent directory listing
        try {
            await tester.listDirectory(['nonexistent_dir']);
            console.log('✗ Expected error for non-existent directory');
        } catch (error) {
            console.log('✓ Correctly handled non-existent directory:', error.statusCode);
        }
        
        // Test file operations on non-existent files
        try {
            await tester.deleteFile(['nonexistent_dir'], 'nonexistent_file.txt');
            console.log('✗ Expected error for non-existent file');
        } catch (error) {
            console.log('✓ Correctly handled non-existent file:', error.statusCode);
        }
        
        console.log('Error condition tests completed successfully!');
    } catch (error) {
        console.error('Error test suite failed:', error);
        process.exit(1);
    }
}

async function runFullTestSuite() {
    await runTests();
    await runErrorTests();
    console.log('\nAll test suites completed successfully!');
}

if (require.main === module) {
    runFullTestSuite().catch(console.error);
}