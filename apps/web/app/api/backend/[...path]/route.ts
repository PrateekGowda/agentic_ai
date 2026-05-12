import { NextRequest } from "next/server";

const backendUrl = process.env.ORCHESTRATOR_BASE_URL ?? "http://localhost:8000";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = new URL(path.join("/"), `${backendUrl.replace(/\/$/, "")}/`);
  target.search = request.nextUrl.search;

  const response = await fetch(target, {
    method: request.method,
    headers: {
      "content-type": request.headers.get("content-type") ?? "application/json",
    },
    body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.text(),
  });

  return new Response(response.body, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const DELETE = proxy;
