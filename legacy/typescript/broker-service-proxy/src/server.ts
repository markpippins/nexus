import express, { Request, Response } from 'express';
import cors from 'cors';
import { BrokerProxyController } from './controllers/BrokerProxyController';

const app = express();
const controller = new BrokerProxyController();

// CORS middleware - allow requests from any origin during development
app.use(cors({
  origin: '*', // In production, replace with specific origins
  credentials: true,
  optionsSuccessStatus: 200
}));

// Middleware to parse JSON
app.use(express.json());

// Define the route for proxying requests
app.post('/api/broker/submitRequest', async (req: Request, res: Response) => {
  return controller.proxyRequest(req, res);
});

// Health check endpoint
app.get('/health', (req: Request, res: Response) => {
  res.status(200).json({ status: 'OK', timestamp: new Date().toISOString() });
});

// Handle all other routes with 404
app.use((req: Request, res: Response) => {
  res.status(404).json({ error: 'Route not found' });
});

// Get port from environment variable or default to 3333
const port = parseInt(process.env.BROKER_PROXY_PORT || '3334', 10);
const host = process.env.HOST || '0.0.0.0';

app.listen(port, host, () => {
  console.log(`Broker Service Proxy server running on ${host}:${port}`);
  console.log(`Broker Gateway URL: ${process.env.BROKER_GATEWAY_URL || 'http://localhost:8081'}`);
});

export default app;