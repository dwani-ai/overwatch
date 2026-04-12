Dwani API — Nginx allowlist gateway
===================================

Purpose
-------
Bots scanning your VM hit random paths (/wp-admin, /.env, /swagger, …). This
gateway returns 404 for anything not explicitly allowed, so Uvicorn only sees
real API traffic.

Run (Docker Compose)
--------------------
From repo root:

  docker compose -f compose.yml up -d

compose.yml and compose-external-only.yml include nginx-gateway on :80 and
expose the API on 18888 only inside the stack (no direct host binding).

Optional merge file compose.gateway.yml is only for custom stacks; the main
compose files already define nginx-gateway.

Tune `rate` / `burst` in nginx/gateway/nginx.conf for abuse protection.

Disable Swagger on the API (recommended in production)
------------------------------------------------------
Set on dwani-server:

  DISABLE_API_DOCS=1

Then /docs, /redoc, /openapi.json are not registered (nginx allowlist already
blocks them unless you add those paths).

Other hardening (VM / host)
---------------------------
1. UFW: allow only 22 (locked to your IP), 80, 443; deny others.
2. Do not publish vllm / TTS ports (10802, 10804) to 0.0.0.0 in production —
   keep them on the Docker internal network only (edit compose.yml).
3. Fail2ban: jail on nginx 404/429 for repeat offenders (use a filter on
   access.log and a short findtime).
4. Cloudflare (or similar) in front: WAF, bot fight, optional IP allowlist for
   /v1/*.
5. TLS: terminate HTTPS at nginx (certbot) or at the CDN; proxy to upstream
   over a private network or stunnel if needed.

Updating the allowlist
----------------------
When you add a route in src/server/main.py, add a matching line to the
`map ... $dwani_allowed` block in nginx/gateway/nginx.conf.
