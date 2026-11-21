# Worker client integration

The `mcp_workers_bridge` Python package is the client for this Worker. It posts bounded operation envelopes to the Access-protected Worker; the Worker terminates the client HTTPS connection at Cloudflare and makes the fixed origin request through its Gateway binding. Treat this as a trust boundary for private account data. [HTTPS API](API.md)

## Collector requirements from vault

| Collector | Current behavior that the adapter must preserve |
| --- | --- |
| YouTube history | GET `/feed/history?hl=en` to read bootstrap context. POST `/youtubei/v1/browse` with `FEhistory` or continuation, JSON client context, and cookie-scoped `SAPISIDHASH` calculated for the original origin. Status, bootstrap HTML, account marker and continuation JSON matter. Current connect/read timeouts are 15/45 s with up to three attempts for controlled transient failures. |
| YouTube captions | GET `/watch?v=…&hl=en`, POST `/youtubei/v1/player?key=…` with Android `20.10.38` context, then GET signed `/api/timedtext` at a YouTube host. Empty initial cookie jar, guest cookies scoped to the origin, exact signed query, manual-track preference, XML/JSON3 body fidelity. Current timeout is 15/30 s and up to three transient attempts. |
| X | GET `x.com/home`, dynamic JavaScript assets at `abs.twimg.com`, and allowlisted GET GraphQL requests with dynamic read-only query IDs. Preserve cookie scoping, bearer, CSRF and transaction headers, exact query encoding, JavaScript and JSON bodies, and rate-limit reset. Current timeout is 10/45 s with no automatic retry. |
| Reddit | Cloud bridge uses signed-in Old Reddit HTML only. The Mac JSON transport for `/api/me.json` and listing JSON remains local. Preserve account marker, HTML feed and comment paths, query cursors and 403 classification using a bounded body prefix. Keep the two-second pacing, 10/30 s timeouts, and bounded streaming up to 12,000,000 bytes. No automatic retry. |

All collectors disable automatic redirects. Keep the existing TLS trust store for access to the Worker, preserve HTTP failure categories and distinguish Access errors from upstream responses and Cloud route denials. Cloudflare and Gateway can inspect origin-bound credentials and content, depending on policy. Review observability, TLS inspection, HTTP logging, cache behavior, and exact hostname routes before authorizing private tests. Keep origin credentials off asset hosts and ignore unsolicited redirects, `Set-Cookie` and transport-generated headers from untrusted routes.

## Integration order

1. Verify Worker-level Access on production and previews, prove Gateway egress with logs, and obtain real manual caption segments on Gateway. Verify HTTPS to the exact approved hostname from the actual Codex Cloud executor.
2. Build and test the restricted relay on `service-bridge` with synthetic headers and cookies, mock X and Reddit upstreams, streaming and body/status fidelity, gateway-denial and redirect behavior, timeout limits, and no-fallback assertions. Finalize relay v1 only after this verification.
3. Configure the Python MCP client with `SERVICE_BRIDGE_WORKER_URL`, `CF_ACCESS_CLIENT_ID`, and `CF_ACCESS_CLIENT_SECRET` privately. Reuse collector parsing and session preparation against the original URLs so cookie and authentication scope remain correct. Translate only already-prepared upstream requests into the operation envelope, disable redirects, and preserve TLS verification.
4. Validate serialized requests, responses, retries and stream cancellation against a synthetic HTTPS fixture. Add standalone signed-out captions before requesting approval for any real YouTube history, X or Reddit session. Report each collector's results independently.
5. Roll out the Worker and MCP together. Keep S3 clients, private object formats, cache-first paths, and AWS proxy/CA settings independent of the Worker transport. Promotion and Cloud settings changes require separate approval.
