import express from 'express';
import cors from 'cors';
import * as winston from 'winston';
import { BrokerProxyController } from './controllers/BrokerProxyController';

// Define types for Express
type Request = express.Request;
type Response = express.Response;
type NextFunction = express.NextFunction;

// Configure logging
const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: winston.format.combine(
    winston.format.timestamp(),
    winston.format.errors({ stack: true }),
    winston.format.splat(),
    winston.format.json()
  ),
  defaultMeta: { service: 'broker-service-proxy' },
  transports: [
    new winston.transports.Console({
      format: winston.format.combine(
        winston.format.colorize(),
        winston.format.printf(
          (info) => `${info.timestamp} ${info.level} [${info.service}] ${info.message}`
        )
      ),
    }),
  ],
});

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

// Log all incoming requests
app.use((req: express.Request, res: express.Response, next: express.NextFunction) => {
  logger.info('Incoming request', {
    method: req.method,
    url: req.url,
    query: req.query,
    headers: req.headers
  });
  next();
});

// Define the route for proxying requests
app.post('/api/broker/submitRequest', async (req: express.Request, res: express.Response) => {
  logger.info('Broker proxy request received', {
    bodySize: JSON.stringify(req.body).length,
    requestId: req.body?.requestId
  });
  return controller.proxyRequest(req, res);
});

// Health check endpoint
app.get('/health', (req: express.Request, res: express.Response) => {
  logger.info('Health check endpoint called');
  res.status(200).json({ status: 'OK', timestamp: new Date().toISOString() });
});

// Handle all other routes with 404
app.use((req: express.Request, res: express.Response) => {
  logger.warn('Route not found', { method: req.method, url: req.url });
  res.status(404).json({ error: 'Route not found' });
});

// Get port from environment variable or default to 3333
const port = parseInt(process.env.BROKER_PROXY_PORT || '3333', 10);
const host = process.env.HOST || '0.0.0.0';

app.listen(port, host, () => {
  logger.info(`Broker Service Proxy server running on ${host}:${port}`, { port, host });
  logger.info(`Broker Gateway URL: ${process.env.BROKER_GATEWAY_URL || 'http://localhost:8081'}`, {
    brokerGatewayUrl: process.env.BROKER_GATEWAY_URL || 'http://localhost:8081'
  });
});

export default app;
