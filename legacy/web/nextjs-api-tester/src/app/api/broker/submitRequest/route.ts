import {NextResponse} from 'next/server';

interface ServiceRequest {
  service: string;
  operation: string;
  params: Record<string, any>;
  requestId: string;
}

export async function POST(request: Request) {
  try {
    const body: ServiceRequest = await request.json();
    console.log('Broker request received:', body);

    const backendUrl =
      process.env.BROKER_SERVICE_URL ||
      'http://localhost:8080/api/broker/submitRequest';

    const backendResponse = await fetch(backendUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify(body),
    });

    const responseData = await backendResponse.json();

    return NextResponse.json(responseData, {
      status: backendResponse.status,
    });
  } catch (error) {
    const requestId =
      (error as any)?.config?.data?.requestId || 'unknown';
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
    return NextResponse.json(errorResponse, {
      status: 500,
    });
  }
}