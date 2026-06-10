// Vercel route that serves the password-gated appliance enrollment script.
//
// Next.js App Router: place at  app/onvif/route.ts  in the digitaltidewater.com
// project -> serves https://digitaltidewater.com/onvif
// (For a non-Next.js project, see deploy/README.md for the api/ + rewrite variant.)
//
// Set these in Vercel → Project → Settings → Environment Variables (secrets stay
// OUT of the repo):
//   ONVIF_PROVISION_USER       e.g. "provision"
//   ONVIF_PROVISION_PASSWORD   the basic-auth password
//   ONVIF_TAILSCALE_AUTHKEY    reusable tskey-auth-...
//   ONVIF_FLEET_ENROLL_KEY     the fleet server's FLEET_ENROLL_KEY
//   ONVIF_FLEET_SERVER_URL     optional, default http://fleet:8080

export const runtime = "edge";
export const dynamic = "force-dynamic";

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function GET(req: Request): Promise<Response> {
  const user = process.env.ONVIF_PROVISION_USER || "provision";
  const pass = process.env.ONVIF_PROVISION_PASSWORD || "";
  const unauthorized = () =>
    new Response("authentication required\n", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="ONVIF Provisioning"' },
    });

  const header = req.headers.get("authorization") || "";
  if (!pass || !header.startsWith("Basic ")) return unauthorized();
  const expected = "Basic " + btoa(`${user}:${pass}`);
  if (!timingSafeEqual(header, expected)) return unauthorized();

  const tskey = process.env.ONVIF_TAILSCALE_AUTHKEY || "";
  const enroll = process.env.ONVIF_FLEET_ENROLL_KEY || "";
  const serverUrl = process.env.ONVIF_FLEET_SERVER_URL || "http://fleet:8080";
  if (!tskey || !enroll) {
    return new Response("# server missing ONVIF_TAILSCALE_AUTHKEY / ONVIF_FLEET_ENROLL_KEY\n", {
      status: 500,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  }

  const script = `#!/usr/bin/env bash
# ONVIF gateway appliance enrollment (digitaltidewater.com)
set -euo pipefail
curl -fsSL https://raw.githubusercontent.com/preppdev/onvif-unifi/main/install.sh | \\
  TAILSCALE_AUTHKEY='${tskey}' \\
  FLEET_SERVER_URL='${serverUrl}' \\
  FLEET_ENROLL_KEY='${enroll}' \\
  bash
`;
  return new Response(script, {
    status: 200,
    headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" },
  });
}
