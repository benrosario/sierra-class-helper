# Google sign-in website demo

`POST /demo/chat` allows **three lifetime message attempts per Google account**.
The API verifies Google's ID token itself and identifies the account by its
signed `sub` claim. No website account database, password system, or shared demo
secret is needed. A person can own multiple Google accounts, so this is an account
limit, not proof of one human.

The Sierra API changes are implemented here. Adding the Google button and chat UI
to the Cloudflare website, configuring Google, and deployment are separate steps.

## Setup

1. In [Google Auth Platform](https://console.cloud.google.com/auth/overview),
   select/create a project and configure the app's branding and audience. Create
   an OAuth client of type **Web application**. Add your website's exact origins
   under **Authorized JavaScript origins**, including the development origin if
   needed. Complete Google's publishing/branding requirements for public access.
2. Set `SIERRA_GOOGLE_CLIENT_ID` on the API to that client's public ID, for example
   `123456-example.apps.googleusercontent.com`. Use the **same client ID** when
   initializing Google Identity Services on the website. This ID is public; the
   ID-token flow does not require a Google client secret on the API or website.
3. Set `SIERRA_DEMO_ALLOWED_ORIGINS` on the API to your exact website origins,
   comma-separated, such as `https://example.com,https://www.example.com`. No
   trailing slashes, paths, credentials, or wildcards. HTTPS is required in
   production. HTTP localhost origins are allowed only in development.
4. Keep `SIERRA_DATA_DIR` on a persistent disk (the existing `/data` volume).
   `demo.sqlite` stores lifetime usage. Deleting or replacing the database resets
   allowances; never include it in Git or deployment uploads. Back it up with the
   volume. One API instance may use multiple worker processes on this same disk.
   Separate replicas with separate disks do not share quotas; use a shared
   transactional store before scaling to that topology.
5. Optionally set `SIERRA_DEMO_DAILY_LIMIT` (positive integer, default `100`). This
   limits accepted demo attempts across all Google accounts per UTC day, in
   addition to each account's lifetime cap.
6. Configure the existing Discord integration with the same random
   `SIERRA_BOT_API_TOKEN` (32+ characters) on both API and bot. `/chat` and `/search`
   require this server-only secret; Google login tokens cannot access them. Never
   put this token or an OpenAI key in the website.
7. Install the updated dependencies and deploy the API and bot. An unset Google
   client ID disables the demo with `503`. Invalid configuration fails startup.
   No Google credentials are generated or published automatically by this repo.

**Previous draft integration:** `SIERRA_DEMO_API_TOKEN` is no longer read and
`X-Demo-User` is ignored. A shared secret plus a caller-chosen ID cannot authenticate
any demo request. Identity now comes exclusively from the verified Google token.

## Website integration (Cloudflare or another host)

Use the [Google Identity Services JavaScript callback flow](https://developers.google.com/identity/gsi/web/guides/display-button)
to render the official sign-in button. The callback receives `credential`, a
short-lived Google **ID token**. Keep it in memory and attach it to each demo
request. The API does not accept Google access tokens, authorization codes, email
addresses, or a decoded account-ID string as substitutes.

```javascript
// After loading https://accounts.google.com/gsi/client on the website:
let googleIdToken = null;

google.accounts.id.initialize({
  client_id: "YOUR_PUBLIC_CLIENT_ID.apps.googleusercontent.com",
  callback: ({ credential }) => {
    googleIdToken = credential;
    // Enable the demo's message input.
  },
});
google.accounts.id.renderButton(document.getElementById("google-sign-in"), {
  theme: "outline",
  size: "large",
});

async function sendDemoMessage(message, conversationHistory = []) {
  if (!googleIdToken) throw new Error("Sign in with Google first.");
  const response = await fetch("https://YOUR_API_HOST/demo/chat", {
    method: "POST",
    credentials: "omit",
    headers: {
      "Authorization": `Bearer ${googleIdToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      message,
      conversation_history: conversationHistory,
    }),
    cache: "no-store",
  });
  const result = await response.json();
  if (response.status === 401) {
    googleIdToken = null;
    // Show sign-in again; Google will issue a fresh token.
  }
  return { status: response.status, ...result };
}
```

This is an integration example, not an installed website feature. Use your site's
actual API host/client ID and allow the Google script in any Content Security
Policy. Do not put tokens in URLs, logs, or persistent browser storage. Clearing
browser state and obtaining a fresh token do not reset server-side quota.

No login cookie or session is created by this API: authentication requires the
explicit Authorization header on every request. This example uses the JavaScript
callback flow, not Google's form-post login flow. If your website later adds
cookie-based sessions or Google's form-post callback, implement the corresponding
CSRF checks there; those endpoints are not provided by this API.

A Cloudflare backend proxy is optional: it can forward the Google token unchanged
to this API over HTTPS, but it must not replace it with a claimed user ID. Direct
browser calls work only from configured origins. CORS is a browser control, not
authentication; Google-token verification and quotas apply to non-browser callers
too. Configure any reverse proxy to trust forwarded client-IP headers only from
known infrastructure; the application never reads raw X-Forwarded-For itself.

The UI should show `messages_remaining`, send one message at a time, avoid
automatic retries, and prompt for sign-in on `401`. Disable sending when the
lifetime quota is exhausted. Render model text safely (sanitize Markdown/disable
raw HTML), and display a notice to check AI course advice with Sierra College.

## API contract

```text
POST /demo/chat
Authorization: Bearer <Google ID token>
Content-Type: application/json
```

```json
{
  "message": "What computer science courses are available?",
  "num_courses": 3,
  "conversation_history": []
}
```

`message` is required, nonblank, and at most 2,000 characters. `num_courses`
defaults to 3 and must be 1–5. Optional history allows at most 10 messages with
`user` or `assistant` roles, at most 8,000 characters per entry and 12,000 total.
System/developer roles and unknown fields are rejected. Trim history on the
website as needed. HTTP bodies (including chunked bodies) are capped at 64 KiB
before JSON parsing. Retrieved context is capped at 24,000 characters and generated
output at 2,000 completion tokens. These input/output protections also apply to
bot chat routes.

Success (`200`):

```json
{
  "response": "Here are some relevant courses...",
  "courses_searched": 3,
  "data_updated_at": "2026-09-18T00:00:00+00:00",
  "messages_remaining": 2
}
```

`data_updated_at` may be null. The first, second, and third admitted requests
return 2, 1, and 0 remaining. Concurrent replies may arrive out of order; each
reports the allowance when that request was admitted.

After three attempts (`429`):

```json
{
  "detail": {
    "code": "demo_limit_reached",
    "message": "You have used all 3 demo messages.",
    "messages_remaining": 0
  }
}
```

Other statuses:

| Status | Meaning |
|---|---|
| `401` | Missing, malformed, expired, forged, or wrong-app Google token; sign in again |
| `413` | Request body too large |
| `422` | Invalid request fields/history |
| `429`, `demo_daily_limit_reached` | Global daily capacity exhausted; try after UTC midnight |
| `429`, `demo_auth_rate_limited` | Authentication burst limit; includes `Retry-After: 60` |
| `503` | Google sign-in unconfigured, Google certificates unavailable, quota storage unavailable, or chat failure |

All responses use `Cache-Control: no-store`. Internal verifier/provider exception
details and credentials are not returned to callers.

## Verification and accounting

- `google-auth` validates Google's signature, issuer, audience, issuance time, and
  expiry. The API requires the configured audience, checks `azp` when present, and
  derives a namespaced identity from `sub`. Email/name changes do not change it.
- Public signing certificates are cached according to Google's HTTP cache headers.
  Network waits are bounded and blocking verification runs off the event loop.
  No token or profile cache is stored. A Google transport outage fails closed.
- Before verification, per-worker burst limits allow 30 attempts/minute per client
  IP and 300/minute globally. These are supplemental in-memory protections and
  reset on restart; the lifetime and daily **paid-work quotas** live in SQLite.
- A SQLite write transaction reserves both quotas before retrieval or generation.
  Invalid/unauthorized requests, verification outages, and daily-cap rejection
  consume no message slots. Generation/retrieval failures, disconnects, and
  retries after admission **do** consume a slot because upstream charges may have
  occurred. Quota storage failures never start paid work.
- There is no quota-reset endpoint. Google `sub` values are namespaced and SHA-256
  hashed before storage. Email addresses, names, and tokens are not saved in the
  quota database. Demo calls are not recorded as Discord analytics.

## Tests

```bash
python -m pytest tests/test_google_identity.py tests/test_demo_security.py
python -m pytest
```

Feature/security tests use locally generated RSA keys and the real Google token
verifier. Certificate responses and model/retrieval calls are substituted, so they
need no Google account, network connection, API key, or paid model calls. Existing
live integration tests retain their credential/index prerequisites.

References: [Google ID-token verification](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token),
[google-auth verification and caching](https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.id_token.html),
[FastAPI CORS](https://fastapi.tiangolo.com/tutorial/cors/),
[SQLite transactions](https://www.sqlite.org/lang_transaction.html).
