import axios from 'axios'

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

export class BrokerProxyController {
  private brokerGatewayUrl: string

  constructor() {
    // Get broker gateway URL from environment variables, default to localhost:8081
    this.brokerGatewayUrl = process.env.BROKER_GATEWAY_URL || 'http://localhost:8081'
  }

  async proxyRequest(request: any, response: any) {
    try {
      // Extract the request body which should be a ServiceRequest
      const serviceRequest: ServiceRequest = request.body

      // Validate that it's a proper ServiceRequest
      if (!serviceRequest || typeof serviceRequest !== 'object') {
        return response.status(400).json({
          ok: false,
          errors: [{ message: 'Invalid request format. Expected a ServiceRequest object.' }],
          requestId: 'validation-error'
        })
      }

      // Forward the request to the broker gateway
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

      // Return the response from the broker gateway
      return response.status(brokerResponse.status).json(brokerResponse.data)

    } catch (error: any) {
      console.error('Error proxying request to broker gateway:', error.message)

      // Handle different types of errors
      if (error.response) {
        // Broker gateway returned an error response
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