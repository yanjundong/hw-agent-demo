// Cloudflare Pages Function：把浏览器对 /invocations 的同源请求转发到
// AgentArts Gateway（authorizer = CUSTOM_JWT）。
//
// 为什么需要它：
//   1. 浏览器只与 *.pages.dev 通信，完全避开 CORS；
//   2. Authorization 透传的是 MSAL 获取的 Microsoft Entra id_token，
//      Gateway 校验 JWT 后注入 X-HW-AgentGateway-User-Id 与 WAT；
//   3. 禁止透传任何 X-HW-AgentGateway-* 头，防止伪造平台身份。
const FORWARDED_HEADERS = [
  "accept",
  "authorization",
  "content-type",
  "x-hw-agentarts-session-id",
];
const SESSION_HEADER = "x-hw-agentarts-session-id";

function getUpstreamUrl(env) {
  const value = (env && env.AGENTARTS_INVOCATIONS_URL || "").trim();
  if (!value) {
    throw new Error("AGENTARTS_INVOCATIONS_URL is not configured");
  }
  return new URL(value);
}

export async function onRequestPost({ request, env }) {
  let upstreamUrl;
  try {
    upstreamUrl = getUpstreamUrl(env);
  } catch (error) {
    console.error("AgentArts proxy misconfigured", error);
    return Response.json(
      { detail: "Frontend proxy is not configured" },
      { status: 500 },
    );
  }

  const headers = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (!headers.has(SESSION_HEADER)) {
    headers.set(SESSION_HEADER, crypto.randomUUID());
  }

  try {
    const upstreamResponse = await fetch(upstreamUrl.toString(), {
      method: "POST",
      headers,
      body: await request.arrayBuffer(),
      redirect: "manual",
    });
    const responseHeaders = new Headers(upstreamResponse.headers);
    // SSE 长连接与鉴权错误都不允许被 CDN 缓存
    responseHeaders.set("Cache-Control", "no-store");
    return new Response(upstreamResponse.body, {
      status: upstreamResponse.status,
      statusText: upstreamResponse.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("AgentArts proxy request failed", error);
    return Response.json(
      { detail: "AgentArts Gateway is unavailable" },
      { status: 502 },
    );
  }
}
