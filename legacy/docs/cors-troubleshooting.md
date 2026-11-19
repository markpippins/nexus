# CORS Troubleshooting Guide

## Changes Made to Fix CORS Issues

### 1. Spring Boot Broker Gateway

#### Added CorsFilter.java
Created a high-priority servlet filter that handles CORS headers at the earliest stage:
- Location: `spring/broker-gateway/src/main/java/com/angrysurfer/atomic/broker/gateway/CorsFilter.java`
- Handles preflight OPTIONS requests
- Sets proper CORS headers including credentials support
- Logs all CORS preflight requests for debugging

#### Updated CorsConfig.java
- Changed from `allowedOrigins("*")` to `allowedOriginPatterns("*")`
- Added `allowCredentials(true)`
- Added `maxAge(3600)` for preflight caching

#### Updated BrokerController.java
- Made `@CrossOrigin` annotation more explicit
- Added all HTTP methods including OPTIONS

#### Updated application.properties
- Added debug logging for CORS-related classes

### 2. Angular Throttler Client

#### Updated broker.service.ts
Added proper CORS settings to fetch requests:
```typescript
mode: 'cors',
credentials: 'include',
```

## Testing the Fix

### 1. Rebuild and Restart
```bash
# On selenium machine
cd spring
mvn clean install
# Restart the broker-gateway application
```

### 2. Check Server Logs
Look for these log messages on startup:
```
Initializing CORS Config - allowing all origins, methods, and headers
BrokerController initialized
```

When a request comes in, you should see:
```
Handling CORS preflight request from origin: http://172.16.30.57:3000
```

### 3. Test from Browser Console
On the Titanium machine, open browser console and test:

```javascript
fetch('http://selenium:8080/api/broker/submitRequest', {
  method: 'OPTIONS',
  headers: {
    'Origin': 'http://172.16.30.57:3000',
    'Access-Control-Request-Method': 'POST',
    'Access-Control-Request-Headers': 'content-type'
  }
}).then(r => console.log('Preflight response:', r.headers));
```

### 4. Check Response Headers
The response should include:
```
Access-Control-Allow-Origin: http://172.16.30.57:3000
Access-Control-Allow-Credentials: true
Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS, PATCH, HEAD
Access-Control-Allow-Headers: Origin, X-Requested-With, Content-Type, Accept, Authorization, X-CSRF-TOKEN
```

## Common Issues

### Issue: "CORS policy: No 'Access-Control-Allow-Origin' header"
**Solution:** Ensure the CorsFilter is being loaded. Check logs for "Handling CORS preflight request"

### Issue: "CORS policy: Credentials flag is 'true', but the 'Access-Control-Allow-Credentials' header is ''"
**Solution:** This is fixed by the CorsFilter which explicitly sets credentials to true

### Issue: "CORS policy: Response to preflight request doesn't pass access control check"
**Solution:** The CorsFilter now returns 200 OK for OPTIONS requests

### Issue: Still getting CORS errors after restart
**Possible causes:**
1. Browser cache - Try hard refresh (Ctrl+Shift+R) or incognito mode
2. Wrong port - Verify broker gateway is running on port 8080
3. Firewall - Ensure port 8080 is accessible from Titanium to Selenium
4. Wrong URL - Check that Angular app is using correct broker URL

## Network Architecture

```
Titanium (Client Browser)
    ↓
    | HTTP Request (CORS)
    ↓
Beryllium:3000 (Angular Throttler)
    ↓
    | HTTP POST to /api/broker/submitRequest
    ↓
Selenium:8080 (Broker Gateway)
    ↓
    | Routes to services
    ↓
Beryllium:4040 (File System Server)
```

## Verification Commands

### Check if broker gateway is running
```bash
# On selenium
curl http://localhost:8080/health
```

### Check CORS headers from another machine
```bash
# From beryllium or titanium
curl -H "Origin: http://172.16.30.57:3000" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: content-type" \
     -X OPTIONS \
     -v \
     http://selenium:8080/api/broker/submitRequest
```

Look for these headers in the response:
- `Access-Control-Allow-Origin`
- `Access-Control-Allow-Credentials`
- `Access-Control-Allow-Methods`
- `Access-Control-Allow-Headers`
