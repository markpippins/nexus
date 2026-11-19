import {NextResponse} from 'next/server';

interface ServiceRequest {
  service: string;
  operation: string;
  params: Record<string, any>;
  requestId: string;
}

interface BrokerRequest {
  serviceRequest: ServiceRequest;
  brokerUrl?: string;
}

export async function POST(request: Request) {
  try {
    const requestData: BrokerRequest = await request.json();
    const { serviceRequest, brokerUrl } = requestData;

    console.log('Broker request received:', serviceRequest);

    // Use the broker URL from the request if provided, otherwise use environment variable or default
    const backendUrl =
      brokerUrl ||
      process.env.BROKER_SERVICE_URL ||
      'http://localhost:8080/api/broker/submitRequest';

    console.log('Forwarding request to broker service:', backendUrl);

    const backendResponse = await fetch(backendUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify(serviceRequest),
    });

    const responseData = await backendResponse.json();

    return NextResponse.json(responseData, {
      status: backendResponse.status,
    });
  } catch (error) {
    const requestId =
      requestData.serviceRequest?.requestId ||
      'unknown';
    const errorResponse = {
      ok: false,
      data: null,
      errors:
        error instanceof Error
          ? [{message: 'Failed to connect to the broker service', details: error.message}]
          : [{message: 'An unknown error occurred'}],
      requestId: requestId,
      ts: new Date().toISOString(),
    };
    console.error('Broker API error:', error);
    return NextResponse.json(errorResponse, {
      status: 500,
    });
  }
}