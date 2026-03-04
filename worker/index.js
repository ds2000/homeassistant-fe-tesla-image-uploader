// Cloudflare Worker — API proxy for Tesla Card Image Uploader
// Handles /dispatch (email verification) and /github (GitHub API proxy)

const ALLOWED_ORIGINS = [
  'https://homeassistant-fe-tesla-image-uploader.pages.dev',
  'https://ds2000.github.io',
];

function isAllowedOrigin(origin) {
  if (!origin) return false;
  if (ALLOWED_ORIGINS.includes(origin)) return true;
  if (/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)) return true;
  return false;
}

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
  };
}

function respond(body, status, origin) {
  const headers = isAllowedOrigin(origin) ? corsHeaders(origin) : {};
  return new Response(body, { status, headers });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get('Origin');

    // Preflight
    if (request.method === 'OPTIONS') {
      if (!isAllowedOrigin(origin)) return new Response(null, { status: 403 });
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    // ── /dispatch — trigger email verification workflow ──────────────
    if (url.pathname === '/dispatch') {
      if (request.method !== 'POST') {
        return respond('Method not allowed', 405, origin);
      }

      if (!isAllowedOrigin(origin)) {
        return new Response('Forbidden', { status: 403 });
      }

      // Rate limit: 2 requests per 60s per IP (requires KV binding RATE_LIMIT)
      if (env.RATE_LIMIT) {
        const ip = request.headers.get('cf-connecting-ip') || 'unknown';
        const key = `rl:${ip}`;
        const record = await env.RATE_LIMIT.get(key);
        const count = record ? parseInt(record) : 0;
        if (count >= 2) {
          return respond('Too many requests. Try again later.', 429, origin);
        }
        await env.RATE_LIMIT.put(key, String(count + 1), { expirationTtl: 60 });
      }

      // Parse and validate body
      let body;
      try {
        body = await request.json();
      } catch {
        return respond('Invalid JSON', 400, origin);
      }

      const { email, token_hash, model, variant, colour } = body;
      if (!email || !token_hash || !model || !variant || !colour) {
        return respond('Missing required fields', 400, origin);
      }

      // Forward to GitHub Actions as workflow_dispatch
      const resp = await fetch(
        'https://api.github.com/repos/ds2000/homeassistant-fe-tesla-image-uploader/actions/workflows/send-verification.yml/dispatches',
        {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${env.GITHUB_PAT}`,
            'Accept': 'application/vnd.github+json',
            'Content-Type': 'application/json',
            'User-Agent': 'tesla-image-uploader-worker',
          },
          body: JSON.stringify({
            ref: 'TESUPL0001',
            inputs: { email, token_hash, model, variant, colour },
          }),
        }
      );

      return respond(await resp.text(), resp.status, origin);
    }

    // ── /github — GitHub API proxy for uploads ──────────────────────
    if (url.pathname === '/github') {
      if (request.method !== 'POST') {
        return respond('Method not allowed', 405, origin);
      }

      if (!isAllowedOrigin(origin)) {
        return new Response('Forbidden', { status: 403 });
      }

      let payload;
      try {
        payload = await request.json();
      } catch {
        return respond('Invalid JSON', 400, origin);
      }

      const { method, path, body } = payload;
      if (!method || !path) {
        return respond('Missing required fields', 400, origin);
      }

      // Only allow API calls to this specific repo
      const allowedPrefix = '/repos/ds2000/homeassistant-fe-tesla-image-uploader/';
      if (!path.startsWith(allowedPrefix)) {
        return respond('Forbidden path', 403, origin);
      }

      const ghResp = await fetch('https://api.github.com' + path, {
        method: method,
        headers: {
          'Authorization': `Bearer ${env.GITHUB_UPLOAD_PAT}`,
          'Accept': 'application/vnd.github+json',
          'Content-Type': 'application/json',
          'User-Agent': 'tesla-image-uploader-worker',
        },
        body: body ? JSON.stringify(body) : undefined,
      });

      const respHeaders = isAllowedOrigin(origin) ? corsHeaders(origin) : {};
      respHeaders['Content-Type'] = ghResp.headers.get('Content-Type') || 'application/json';

      return new Response(await ghResp.text(), {
        status: ghResp.status,
        headers: respHeaders,
      });
    }

    // ── /status — fresh status.json (bypasses raw.githubusercontent CDN) ─
    if (url.pathname === '/status') {
      if (request.method !== 'GET') {
        return respond('Method not allowed', 405, origin);
      }

      const ghResp = await fetch(
        'https://api.github.com/repos/ds2000/homeassistant-fe-tesla-image-uploader/contents/status.json?ref=TESUPL0001',
        {
          headers: {
            'Authorization': `Bearer ${env.GITHUB_PAT}`,
            'Accept': 'application/vnd.github.raw+json',
            'User-Agent': 'tesla-image-uploader-worker',
          },
        }
      );

      const respHeaders = isAllowedOrigin(origin) ? corsHeaders(origin) : {};
      respHeaders['Content-Type'] = 'application/json';
      respHeaders['Cache-Control'] = 'no-cache, no-store';

      return new Response(await ghResp.text(), {
        status: ghResp.status,
        headers: respHeaders,
      });
    }

    return respond('Not found', 404, origin);
  },
};
