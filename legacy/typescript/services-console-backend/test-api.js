#!/usr/bin/env node

const http = require('http');

const BASE_URL = 'http://localhost:3001/api';

const endpoints = [
  '/health',
  '/languages',
  '/categories', 
  '/vendors',
  '/frameworks',
  '/operatingSystems',
  '/environmentTypes',
  '/serverTypes',
  '/servers',
  '/serviceTypes',
  '/services',
  '/configTypes',
  '/serviceConfigs',
  '/serviceDependencies',
  '/deployments'
];

function testEndpoint(endpoint) {
  return new Promise((resolve, reject) => {
    const url = endpoint === '/health' ? `http://localhost:3001${endpoint}` : `${BASE_URL}${endpoint}`;
    
    const req = http.get(url, (res) => {
      let data = '';
      
      res.on('data', (chunk) => {
        data += chunk;
      });
      
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          resolve({
            endpoint,
            status: res.statusCode,
            success: res.statusCode < 400,
            dataLength: Array.isArray(json) ? json.length : (json ? 1 : 0)
          });
        } catch (e) {
          resolve({
            endpoint,
            status: res.statusCode,
            success: false,
            error: 'Invalid JSON response'
          });
        }
      });
    });
    
    req.on('error', (err) => {
      resolve({
        endpoint,
        status: 'ERROR',
        success: false,
        error: err.message
      });
    });
    
    req.setTimeout(5000, () => {
      req.destroy();
      resolve({
        endpoint,
        status: 'TIMEOUT',
        success: false,
        error: 'Request timeout'
      });
    });
  });
}

async function runTests() {
  console.log('🧪 Testing Services Console API endpoints...\n');
  
  const results = await Promise.all(endpoints.map(testEndpoint));
  
  console.log('📊 Test Results:');
  console.log('═'.repeat(60));
  
  results.forEach(result => {
    const status = result.success ? '✅' : '❌';
    const count = result.dataLength ? ` (${result.dataLength} records)` : '';
    const error = result.error ? ` - ${result.error}` : '';
    
    console.log(`${status} ${result.endpoint.padEnd(25)} ${result.status}${count}${error}`);
  });
  
  const successCount = results.filter(r => r.success).length;
  const totalCount = results.length;
  
  console.log('═'.repeat(60));
  console.log(`📈 Success Rate: ${successCount}/${totalCount} (${Math.round(successCount/totalCount*100)}%)`);
  
  if (successCount === totalCount) {
    console.log('🎉 All endpoints are working correctly!');
  } else {
    console.log('⚠️  Some endpoints are not responding correctly.');
    console.log('💡 Make sure the server is running: npm run dev');
  }
}

runTests().catch(console.error);