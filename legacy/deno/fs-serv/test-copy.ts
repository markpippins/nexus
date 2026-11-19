import * as http from 'http';

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

async function testCopyOperation() {
    console.log('Testing copy operation specifically...\n');
    
    // Create a request for the copy operation
    const requestData: RequestModel = {
        alias: 'test',
        path: ['subfolder', 'another.txt'], // Source file path
        operation: 'copy',
        toPath: ['subfolder', 'another_copy.txt'] // Destination path
    };

    return new Promise((resolve, reject) => {
        const options: http.RequestOptions = {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            host: 'localhost',
            port: 4040,
            path: '/fs'
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
                        console.log('✓ Copy operation successful:', response);
                        resolve(response);
                    } else {
                        console.error('✗ Copy operation failed:', {
                            statusCode: res.statusCode,
                            message: response.detail || `HTTP ${res.statusCode}`,
                            response
                        });
                        reject({
                            statusCode: res.statusCode,
                            message: response.detail || `HTTP ${res.statusCode}`,
                            response
                        });
                    }
                } catch (e) {
                    console.error('✗ Failed to parse response JSON:', body);
                    reject({
                        statusCode: res.statusCode,
                        message: 'Failed to parse response JSON',
                        rawBody: body
                    });
                }
            });
        });

        req.on('error', (error) => {
            console.error('✗ Request error:', error);
            reject(error);
        });

        req.write(JSON.stringify(requestData));
        req.end();
    });
}

async function runCopyTest() {
    try {
        await testCopyOperation();
        console.log('\nCopy operation test completed!');
    } catch (error) {
        console.error('\nCopy operation test failed:', error);
    }
}

if (require.main === module) {
    runCopyTest().catch(console.error);
}