'use server';

import {NextResponse} from 'next/server';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    console.log('FS request received:', body);

    // This would be the URL of your actual backend service.
    // Updated to match the working fs-utils service on port 4040
    const backendUrl =
      process.env.FS_SERVICE_URL || 'http://127.0.0.1:4040/fs';

    const backendResponse = await fetch(backendUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify(body),
    });

    if (!backendResponse.ok) {
      const errorData = await backendResponse.text();
      console.error('Backend error:', errorData);
      return NextResponse.json(
        {message: 'Backend request failed', details: errorData},
        {status: backendResponse.status}
      );
    }

    const responseData = await backendResponse.json();

    return NextResponse.json(responseData, {
      status: backendResponse.status,
    });
  } catch (error) {
    const errorMessage =
      error instanceof Error
        ? `Failed to connect to the FS service: ${error.message}`
        : 'An unknown error occurred';
    console.error(errorMessage);
    return NextResponse.json(
      {message: errorMessage},
      {
        status: 500,
      }
    );
  }
}
