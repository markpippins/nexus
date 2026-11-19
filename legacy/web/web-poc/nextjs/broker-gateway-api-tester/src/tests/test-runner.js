import axios from 'axios';
import fs from 'fs';
import path from 'path';

const TEST_SUITE_PATH = path.join(__dirname, 'test-suite.json');
const API_URL = 'http://localhost:3000/api/fs-serv';

const runTests = async () => {
    const testSuite = JSON.parse(fs.readFileSync(TEST_SUITE_PATH, 'utf-8'));

    for (const test of testSuite.tests) {
        try {
            const response = await axios.post(API_URL, test.request);

            if (response.status === test.expected.status) {
                console.log(`[PASS] ${test.name}`);
            } else {
                console.error(`[FAIL] ${test.name}: Expected status ${test.expected.status}, got ${response.status}`);
            }
        } catch (error) {
            console.error(`[FAIL] ${test.name}: ${error.message}`);
        }
    }
};

runTests();
