import { NextResponse } from "next/server";

interface RequestPacket {
  id: number;
  name: string;
  message: string;
}

export async function POST(request: Request) {
  try {
    const body: RequestPacket = await request.json();
    console.log(
      "Received request: id={}, name={}, message={}",
      body.id,
      body.name,
      body.message
    );
    const responseText = `Hello API received request with id: ${body.id}, name: ${body.name}, message: ${body.message}`;
    return new Response(responseText, {
      status: 200,
      headers: {
        "Content-Type": "text/plain",
      },
    });
  } catch (error) {
    return new Response("Invalid JSON body", {
      status: 400,
      headers: {
        "Content-Type": "text/plain",
      },
    });
  }
}
