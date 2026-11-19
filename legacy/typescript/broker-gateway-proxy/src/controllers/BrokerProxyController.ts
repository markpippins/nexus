import axios from 'axios'
import * as winston from 'winston';

interface ServiceRequest {
  service: string
  operation: string
  params: Record<string, any>
  requestId: string
  encrypt?: boolean
}

interface ServiceResponse<T = any> {
  ok: boolean
  data?: T
  errors?: Array<Record<string, any>>
  requestId: string
  ts: string
  version?: string
  service?: string
  operation?: string
  encrypt?: boolean
}

// Configure logging for controller
const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: winston.format.combine(
    winston.format.timestamp(),
    winston.format.errors({ stack: true }),
    winston.format.splat(),
    winston.format.json()
  ),
  defaultMeta: { service: 'broker-service-proxy', component: 'BrokerProxyController' },
  transports: [
    new winston.transports.Console({
      format: winston.format.combine(
        winston.format.colorize(),
        winston.format.printf(
          (info) => `${info.timestamp} ${info.level} [${info.service}] ${info.component} ${info.message}`
        )
      ),
    }),
  ],
});

export class BrokerProxyController {
  private brokerGatewayUrl: string

  constructor() {
    // Get broker gateway URL from environment variables, default to localhost:8081
    this.brokerGatewayUrl = process.env.BROKER_GATEWAY_URL || 'http://172.16.30.48:8081'
    logger.info('BrokerProxyController initialized', { brokerGatewayUrl: this.brokerGatewayUrl });
  }

  async proxyRequest(request: any, response: any) {
    try {
      logger.info('Processing proxy request', {
        requestId: request.body?.requestId,
        service: request.body?.service,
        operation: request.body?.operation
      });

      // Extract the request body which should be a ServiceRequest
      const serviceRequest: ServiceRequest = request.body

      // Validate that it's a proper ServiceRequest
      if (!serviceRequest || typeof serviceRequest !== 'object') {
        logger.warn('Invalid request format received', {
          requestId: 'validation-error',
          requestType: typeof serviceRequest
        });

        return response.status(400).json({
          ok: false,
          errors: [{ message: 'Invalid request format. Expected a ServiceRequest object.' }],
          requestId: 'validation-error'
        })
      }

      logger.debug('Forwarding request to broker gateway', {
        gatewayUrl: this.brokerGatewayUrl,
        service: serviceRequest.service,
        operation: serviceRequest.operation,
        requestId: serviceRequest.requestId
      });

      // Forward the request to the broker gateway
      const startTime = Date.now();
      const brokerResponse = await axios.post<ServiceResponse>(
        `${this.brokerGatewayUrl}/api/broker/submitRequest`,
        serviceRequest,
        {
          headers: {
            'Content-Type': 'application/json',
          },
          timeout: 30000, // 30 second timeout
        }
      )
      const duration = Date.now() - startTime;

      logger.info('Broker gateway response received', {
        requestId: serviceRequest.requestId,
        statusCode: brokerResponse.status,
        duration: `${duration}ms`,
        responseSize: JSON.stringify(brokerResponse.data).length
      });

      // Return the response from the broker gateway
      return response.status(brokerResponse.status).json(brokerResponse.data)

    } catch (error: any) {
      logger.error('Error proxying request to broker gateway', {
        error: error.message,
        stack: error.stack,
        requestId: request.body?.requestId || 'proxy-error',
        service: request.body?.service,
        operation: request.body?.operation
      });

      // Handle different types of errors
      if (error.response) {
        // Broker gateway returned an error response
        logger.warn('Broker gateway returned error response', {
          statusCode: error.response.status,
          statusText: error.response.statusText,
          requestId: request.body?.requestId || 'proxy-error'
        });

        return response.status(error.response.status).json({
          ok: false,
          errors: [{
            message: error.response.data?.message || 'Error from broker gateway',
            details: error.response.data
          }],
          requestId: request.body?.requestId || 'proxy-error',
        })
      } else if (error.request) {
        // Request was made but no response received
        logger.error('No response received from broker gateway', {
          requestId: request.body?.requestId || 'proxy-error',
          error: 'Connection timeout or network error'
        });

        return response.status(502).json({
          ok: false,
          errors: [{
            message: 'Unable to reach broker gateway',
            details: 'Connection timeout or network error'
          }],
          requestId: request.body?.requestId || 'proxy-error',
        })
      } else {
        // Something else happened
        logger.error('Internal proxy error', {
          error: error.message,
          requestId: request.body?.requestId || 'proxy-error'
        });

        return response.status(500).json({
          ok: false,
          errors: [{
            message: 'Internal server error while processing proxy request',
            details: error.message
          }],
          requestId: request.body?.requestId || 'proxy-error',
        })
      }
    }
  }
}
