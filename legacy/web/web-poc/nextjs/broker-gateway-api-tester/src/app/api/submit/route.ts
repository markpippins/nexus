import { NextResponse } from "next/server";

export async function POST(request: Request) {
  console.log("Post endpoint was called");
  return new Response("Post request received!", {
    status: 200,
    headers: {
      "Content-Type": "text/plain",
    },
  });
}
