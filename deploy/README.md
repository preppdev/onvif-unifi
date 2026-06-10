# Hosted enrollment (digitaltidewater.com on Vercel)

Provision an appliance in two steps — install Ubuntu, then:

```bash
curl -fsSL https://provision:PASSWORD@www.digitaltidewater.com/onvif | sudo bash
```

A clean URL gated by HTTP basic auth. A small Vercel function checks the password
and returns the enrollment script, building it from environment variables — so the
Tailscale key and enroll key live in Vercel's env, **never committed**.

> Use the **`www`** host. The apex `digitaltidewater.com` 307-redirects to `www`,
> and `curl` does not resend basic-auth credentials across a host change.

## Setup (one time)

1. **Add the route** to the digitaltidewater.com project:
   - Next.js App Router: copy `vercel/onvif-route.ts` to `app/onvif/route.ts`.
   - Non-Next.js project: put it at `api/onvif.ts` (it serves `/api/onvif`) and add
     a rewrite in `vercel.json` so `/onvif` maps to it:
     ```json
     { "rewrites": [{ "source": "/onvif", "destination": "/api/onvif" }] }
     ```

2. **Set environment variables** (Vercel → Project → Settings → Environment Variables):

   | Variable | Value |
   |---|---|
   | `ONVIF_PROVISION_USER` | `provision` (or any username) |
   | `ONVIF_PROVISION_PASSWORD` | a strong password |
   | `ONVIF_TAILSCALE_AUTHKEY` | your reusable `tskey-auth-...` |
   | `ONVIF_FLEET_ENROLL_KEY` | the fleet server's `FLEET_ENROLL_KEY` |
   | `ONVIF_FLEET_SERVER_URL` | `http://fleet:8080` (optional; this is the default) |

3. **Deploy.** Test it:
   ```bash
   curl -fsSL https://provision:PASSWORD@www.digitaltidewater.com/onvif    # prints the script
   ```

## Provision a box

```bash
curl -fsSL https://provision:PASSWORD@www.digitaltidewater.com/onvif | sudo bash
```

## Security notes

- Vercel is HTTPS by default — credentials and the script are encrypted in transit.
- Secrets live in Vercel env vars, not in the repo or a static file. The basic-auth
  password gates access; pick a strong one.
- On leak: change `ONVIF_PROVISION_PASSWORD`, and rotate the Tailscale key (admin
  console) + `FLEET_ENROLL_KEY` (fleet `.env`) and redeploy.
- Recommended: **tag** the Tailscale key (`tag:onvif`) + an ACL, so even a leaked
  key can only enroll locked-down nodes.

> `enroll.sh.template` is the equivalent script if you ever host it statically
> instead. The fleet server's `GET /enroll/<token>` endpoint is a tailnet/LAN
> alternative that needs no public web host.
