'use server';

import {NextRequest, NextResponse} from 'next/server';

export async function POST(request: NextRequest) {
  try {
    const formData = await request.formData();
    console.log('Upload request received:', formData);

    const backendUrl =
      process.env.UPLOAD_SERVICE_URL ||
      'http://localhost:8080/api/upload/submitRequestWithFile';

    const backendResponse = await fetch(backendUrl, {
      method: 'POST',
      body: formData,
      // 'Content-Type' header is set automatically by fetch with FormData
    });

    if (!backendResponse.ok) {
      const errorData = await backendResponse.text();
      console.error('Backend error:', errorData);
      try {
        // Try parsing as JSON for a more structured error
        const errorJson = JSON.parse(errorData);
        return NextResponse.json(errorJson, {
          status: backendResponse.status,
        });
      } catch (e) {
        // Fallback to text if not JSON
        return NextResponse.json(
          {message: 'Backend request failed', details: errorData},
          {status: backendResponse.status}
        );
      }
    }

    const responseData = await backendResponse.json();

    return NextResponse.json(responseData, {
      status: backendResponse.status,
    });
  } catch (error) {
    const errorMessage =
      error instanceof Error
        ? `Failed to process upload: ${error.message}`
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
