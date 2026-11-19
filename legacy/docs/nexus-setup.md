# Nexus Setup Guide

## Network Configuration

### Machine Setup
- **Beryllium** (172.16.30.57):
  - Nexus Console: Port 3000
  - Image Server: Port 8081
  - File System Server: Port 4040
  
- **Selenium** (current machine):
  - Broker Gateway: Port 8080
  
- **Titanium** (client machine):
  - Browser accessing Nexus app

## Configuring Server Profiles in Nexus

The Nexus app needs to be configured with the correct broker URL to connect to the broker gateway on Selenium.

### Option 1: Configure via UI (Recommended)

1. Open the Nexus app at `http://172.16.30.57:3000`

2. Click on the "Server Profiles" button/menu

3. Create a new profile or edit the existing one with these settings:
   ```
   Name: Selenium Broker
   Broker URL: http://selenium:8080
   (or use IP: http://<selenium-ip>:8080)
   Image URL: http://172.16.30.57:9081
   Auto Connect: ✓ (optional)
   ```

4. Save the profile

5. Connect to the profile

### Option 2: Edit Default Profile in Code

Edit `web/angular/nexus/src/services/server-profile.service.ts`:

```typescript
const DEFAULT_PROFILES: ServerProfile[] = [
  {
    id: 'default-remote',
    name: 'Selenium Broker',
    brokerUrl: 'http://selenium:8080',  // Change this
    imageUrl: 'http://172.16.30.57:8081',
    searchUrl: 'http://172.16.30.57:8082/search',
    autoConnect: true
  }
];
```

## Common Issues

### Issue: "CORS policy: No 'Access-Control-Allow-Origin' header"

**Cause:** The Angular app is trying to connect to `localhost:8080` instead of the selenium broker.

**Solution:** 
1. Check the server profile configuration in the Angular app
2. Ensure the broker URL points to selenium, not localhost
3. Check browser console for the actual URL being called

### Issue: "Failed to fetch" or "Network Error"

**Possible causes:**
1. Broker gateway not running on selenium
2. Firewall blocking port 8080
3. Wrong IP address or hostname

**Verification:**
```bash
# From beryllium, test if selenium broker is reachable
curl http://selenium:8080/health

# Or using IP
curl http://<selenium-ip>:8080/health
```

Expected response:
```json
{
  "status": "UP",
  "service": "broker-gateway",
  "timestamp": "2024-11-07T...",
  "details": {...}
}
```

### Issue: CORS error even with correct configuration

**Check these:**

1. **Verify CORS headers in browser DevTools:**
   - Open Network tab
   - Look for the OPTIONS preflight request
   - Check Response Headers should include:
     - `Access-Control-Allow-Origin: http://172.16.30.57:3000`
     - `Access-Control-Allow-Credentials: true`
     - `Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS, PATCH, HEAD`

2. **Check broker gateway logs:**
   ```
   CORS Filter - Method: OPTIONS, URI: /api/broker/submitRequest, Origin: http://172.16.30.57:3000
   CORS Filter - Handling preflight OPTIONS request from origin: http://172.16.30.57:3000
   ```

3. **Clear browser cache:**
   - Hard refresh: Ctrl+Shift+R (or Cmd+Shift+R on Mac)
   - Or use incognito/private mode

4. **Verify the Angular dev server is not proxying:**
   - Check `angular.json` for proxy configuration
   - The app should make direct requests to selenium:8080

## Testing the Connection

### 1. Test from Browser Console

Open browser console on Titanium and run:

```javascript
fetch('http://selenium:8080/health', {
  method: 'GET',
  mode: 'cors',
  credentials: 'include'
})
.then(r => r.json())
.then(data => console.log('Health check:', data))
.catch(err => console.error('Error:', err));
```

### 2. Test Broker Request

```javascript
fetch('http://selenium:8080/api/broker/submitRequest', {
  method: 'POST',
  mode: 'cors',
  credentials: 'include',
  headers: {
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    service: 'loginService',
    operation: 'login',
    requestId: 'test-123',
    params: {
      alias: 'testuser',
      identifier: 'testpass'
    }
  })
})
.then(r => r.json())
.then(data => console.log('Broker response:', data))
.catch(err => console.error('Error:', err));
```

## Architecture Diagram

```
┌─────────────┐
│  Titanium   │ (Client Browser)
│   Browser   │
└──────┬──────┘
       │ HTTP Request
       ↓
┌─────────────────────────────┐
│      Beryllium              │
│  172.16.30.57               │
│                             │
│  ┌─────────────────────┐   │
│  │ Angular Throttler   │   │
│  │ Port 3000           │   │
│  └──────────┬──────────┘   │
│             │               │
│  ┌──────────┴──────────┐   │
│  │ Image Server        │   │
│  │ Port 8081           │   │
│  └─────────────────────┘   │
│                             │
│  ┌─────────────────────┐   │
│  │ File System Server  │   │
│  │ Port 4040           │   │
│  └─────────────────────┘   │
└─────────────────────────────┘
       │
       │ CORS Request to
       │ http://selenium:8080/api/broker/submitRequest
       ↓
┌─────────────────────────────┐
│      Selenium               │
│                             │
│  ┌─────────────────────┐   │
│  │ Broker Gateway      │   │
│  │ Port 8080           │   │
│  │                     │   │
│  │ - CorsFilter        │   │
│  │ - BrokerController  │   │
│  └──────────┬──────────┘   │
│             │               │
│             ├─→ Routes to beryllium:4040/fs
│             └─→ MongoDB (user/login services)
└─────────────────────────────┘
```

## Debugging Checklist

- [ ] Broker gateway is running on selenium (check with `/health` endpoint)
- [ ] Angular app server profile has correct broker URL (not localhost)
- [ ] Browser can reach selenium:8080 from Titanium
- [ ] CORS headers are present in OPTIONS response
- [ ] No proxy configuration in angular.json
- [ ] Browser cache cleared
- [ ] Check broker gateway logs for CORS filter messages
- [ ] Verify Origin header matches what CORS filter expects
