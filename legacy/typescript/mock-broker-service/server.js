const express = require('express');
const cors = require('cors');

const app = express();
const PORT = 8099;

// Enable CORS for all origins (development only)
app.use(cors({
  origin: '*',
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization']
}));

app.use(express.json());

// Mock user database
let users = [
  { id: 1, name: 'John Doe', email: 'john@example.com' },
  { id: 2, name: 'Jane Smith', email: 'jane@example.com' }
];

let nextUserId = 3;

// Service Broker endpoint
app.post('/api/broker/submitRequest', (req, res) => {
  const { service, operation, params, requestId } = req.body;
  
  console.log(`Request received: service=${service}, operation=${operation}, requestId=${requestId}`);
  console.log('Params:', params);

  try {
    let result = null;

    if (service === 'userService') {
      switch (operation) {
        case 'getById':
          const userId = params.id;
          result = users.find(user => user.id === userId);
          if (!result) {
            return res.json({
              ok: false,
              data: null,
              errors: [{ message: `User with id ${userId} not found` }],
              requestId,
              ts: new Date().toISOString()
            });
          }
          break;

        case 'create':
          const newUser = {
            id: nextUserId++,
            name: params.user.name,
            email: params.user.email
          };
          users.push(newUser);
          result = newUser;
          break;

        case 'list':
          result = users;
          break;

        default:
          return res.json({
            ok: false,
            data: null,
            errors: [{ message: `Unknown operation: ${operation}` }],
            requestId,
            ts: new Date().toISOString()
          });
      }
    } else {
      return res.json({
        ok: false,
        data: null,
        errors: [{ message: `Unknown service: ${service}` }],
        requestId,
        ts: new Date().toISOString()
      });
    }

    // Success response
    res.json({
      ok: true,
      data: result,
      errors: [],
      requestId,
      ts: new Date().toISOString()
    });

  } catch (error) {
    console.error('Error processing request:', error);
    res.status(500).json({
      ok: false,
      data: null,
      errors: [{ message: 'Internal server error', details: error.message }],
      requestId,
      ts: new Date().toISOString()
    });
  }
});

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

app.listen(PORT, () => {
  console.log(`🚀 Mock Service Broker running on http://localhost:${PORT}`);
  console.log(`📋 Available endpoints:`);
  console.log(`   POST /api/broker/submitRequest`);
  console.log(`   GET  /health`);
  console.log(`📊 Mock users: ${users.length} users loaded`);
});