const BACKEND_ORIGIN = process.env.BACKEND_ORIGIN || "http://localhost:5001";

function buildBackendUrl(pathSegments, searchParams) {
  const path = Array.isArray(pathSegments) ? pathSegments.join("/") : "";
  const url = new URL(`${BACKEND_ORIGIN}/api/${path}`);
  const queryString = searchParams.toString();
  if (queryString) {
    url.search = queryString;
  }
  return url;
}

async function proxy(request, context) {
  const params = await context.params;
  const targetUrl = buildBackendUrl(params.path, request.nextUrl.searchParams);
  const headers = new Headers();

  const contentType = request.headers.get("content-type");
  if (contentType) {
    headers.set("content-type", contentType);
  }

  const cookie = request.headers.get("cookie");
  if (cookie) {
    headers.set("cookie", cookie);
  }

  const accept = request.headers.get("accept");
  if (accept) {
    headers.set("accept", accept);
  }

  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.text();

  try {
    const backendResponse = await fetch(targetUrl, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
    });

    const responseHeaders = new Headers();
    const responseContentType = backendResponse.headers.get("content-type");
    if (responseContentType) {
      responseHeaders.set("content-type", responseContentType);
    }

    const setCookie = backendResponse.headers.get("set-cookie");
    if (setCookie) {
      responseHeaders.set("set-cookie", setCookie);
    }

    return new Response(backendResponse.body, {
      status: backendResponse.status,
      headers: responseHeaders,
    });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: `Unable to reach backend service at ${BACKEND_ORIGIN}.`,
        detail: error instanceof Error ? error.message : String(error),
      },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
