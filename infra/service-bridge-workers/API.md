# HTTPS API

## Implemented diagnostics

Use HTTPS to the approved Worker hostname. Send both service-token headers on every request.

```http
CF-Access-Client-Id: <client id>
CF-Access-Client-Secret: <client secret>
```

The edge may return an Access denial or a login redirect before the Worker runs. Clients use manual redirect handling for all API calls. Worker responses carry `X-Bridge-Version: 1`, `Cache-Control: no-store`, and JSON. A valid service-token test verifies the version header and expected body. Worker-local tests exercise the handler behind the edge enforcement.

| Request | Successful JSON body |
| --- | --- |
| `GET /v1/health` | `{"ok":true,"diagnostics":false}` (or `true` when explicitly enabled). No upstream traffic. |
| `GET /v1/diagnostics/egress/ordinary` | `{"ok":true,"route":"ordinary","startedAt":"<UTC ISO 8601>","upstreamStatus":200,"ip":"<observed IP>"}`. Ordinary Worker fetch to `https://checkip.amazonaws.com/`. |
| `GET /v1/diagnostics/egress/gateway` | Same schema with `route: "gateway"` and the VPC Network binding. |
| `GET /v1/diagnostics/captions/ordinary` | `{"ok":true,"route":"ordinary","startedAt":"<UTC ISO 8601>","statuses":{"watch":200,"player":200,"timedtext":200},"playability":{"status":"OK"},"captionType":"manual","language":"en","segments":286}`. The count shown is illustrative. The video is fixed to `aircAruvnKk`. |
| `GET /v1/diagnostics/captions/gateway` | Same schema with `route: "gateway"` and the VPC Network binding for all steps. |

Requests have no body or query parameters. Unsupported or disabled routes return 404 and an enabled route with the wrong method returns 405. A probe returning a result uses HTTP 200 and `ok: false` for failed measurements. The result includes `error`, `route`, `startedAt`, `upstreamStatus` when known, and caption-stage fields when available. Common codes are `gateway_binding_unavailable`, `upstream_failure`, `upstream_deadline`, `upstream_http`, `redirect_rejected`, `response_too_large`, `invalid_ip_observation`, `player_configuration_missing`, `invalid_player_response`, `video_access_restricted`, `manual_english_track_missing`, `invalid_caption_url`, `proof_token_required`, and `invalid_caption_response`. `playability` may contain known safe `reason` and `messages` or redacted values. The timestamps identify probe start, not IP stability.

Egress budget is ten seconds and 128 bytes. Caption budget is 45 seconds total, with limits of 8 MB watch HTML, 4 MB player JSON, and 4 MB timedtext. The Worker never automatically retries or follows redirects. Caption flow starts with an empty in-memory cookie jar for upstream-issued guest cookies. No client headers are copied into subrequests. Signed caption URLs are used only after validating authority, scheme, port, path and video ID and are omitted from output. No signed URL, response body, cookie, or credential is logged by the Worker.

## Signed-out caption relay

Set `COMPARISON_ENABLED` to `true` only during approved Access-protected testing. It defaults to `false`. This interface implements the signed-out requests used by the Python caption client and supports any valid public YouTube video ID supplied by the caller. It uses the Gateway binding for every request, has no ordinary egress fallback, and accepts no arbitrary URL or account authorization header.

| Inbound endpoint | Outbound request | Client envelope |
| --- | --- | --- |
| `POST /v1/signed-out-comparison/watch` | GET `www.youtube.com/watch` | `videoId` is the 11-character ID. `search` is exactly `v=<videoId>&hl=en`. `body: null`. Begin with an empty cookie jar. |
| `POST /v1/signed-out-comparison/player` | POST `www.youtube.com/youtubei/v1/player` | `videoId` is the same 11-character ID. `search: "key=…"`. Supply the unchanged JSON request as a string with the Android `20.10.38` English context and matching video ID. |
| `POST /v1/signed-out-comparison/timedtext` | GET `/api/timedtext` | `videoId` is the same 11-character ID. `host` is `www.youtube.com` or `youtube.com`. Supply the exact signed query from YouTube in `search`, with `body: null`. |
| `GET /v1/signed-out-comparison/ip` | GET `checkip.amazonaws.com/` | No envelope. Returns JSON with `status` and the observed `ip`. |

Use the same Access headers as the diagnostic API. Each POST envelope includes `headers`, `search`, `host`, `body`, and `videoId`. Accepted header names are `User-Agent`, `Accept-Language`, `Accept`, `Content-Type`, and `Cookie`. For signed-out caption use only guest cookies issued during the current run. The Worker returns HTTP 200 for a completed exchange with `X-Origin-Http` giving the upstream status. It returns the upstream text for status 200, discards error and redirect bodies, and returns each origin cookie percent-encoded in `X-Origin-Set-Cookie`. Split merged cookie values on commas before decoding and apply cookies to the original YouTube URL. The Worker sends no `Set-Cookie` on its own hostname. It rejects cross-stage video-ID mismatches, malformed IDs, unsupported envelope fields, and signed caption URLs for another video. Validation and transport failures use a Worker error response. This API expects textual payloads and does not implement the streaming collector relay below.

## Read-only collector relay

Set `RELAY_ENABLED` to `true` only with the Worker-level Access policy active. The relay accepts `POST` envelopes only. Every upstream request is fixed by the operation and uses the Gateway binding, with manual redirects and no ordinary-egress fallback.

```json
{
  "parameters": {},
  "query": "",
  "headers": {},
  "body": null
}
```

| Endpoint | Fixed upstream request | Parameters and headers |
| --- | --- | --- |
| `/v1/relay/x/home` | `GET https://x.com/home` | Empty parameters and query. X header allowlist. |
| `/v1/relay/x/graphql` | `GET https://x.com/i/api/graphql/{queryId}/{operationName}?…` | `queryId` and an allowlisted read `operationName`. Query keys are limited to `variables`, `features`, and `fieldToggles`. |
| `/v1/relay/x/asset` | `GET https://abs.twimg.com/responsive-web/client-web/{asset}` | Validated X bundle filename. Cookies are not sent to the asset host. |
| `/v1/relay/reddit/home` | `GET https://old.reddit.com/` | Empty parameters and query. Reddit header allowlist. |
| `/v1/relay/reddit/identity` | `GET https://old.reddit.com/api/me.json` | Empty parameters and query. |
| `/v1/relay/reddit/listing` | `GET https://old.reddit.com/user/{account}/{collection}.json` | Validated account, collection, and optional `t1_`/`t3_` cursor. The Worker adds `limit=100` and `raw_json=1`. |
| `/v1/relay/reddit/comments` | `GET https://old.reddit.com/...comments.../?context=0` | A validated Reddit post/comment permalink with no query or fragment. |
| `/v1/relay/youtube/history` | `GET https://www.youtube.com/feed/history?hl=en` | Empty parameters and query, with `body: null`. The client supplies scoped YouTube headers and cookies. |
| `/v1/relay/youtube/browse` | `POST https://www.youtube.com/youtubei/v1/browse` | Query permits only `prettyPrint=false` and an optional validated `key`. `body` is the raw JSON string containing the client context and either `browseId: "FEhistory"` or one non-empty continuation token. |

X operations are `Viewer`, `Likes`, `Bookmarks`, `BookmarkFoldersSlice`, `BookmarkFolderTimeline`, `UserTweets`, `UserTweetsAndReplies`, `UserMedia`, and `TweetResultByRestId`. X request headers are limited to Accept, Accept-Language, Authorization, Cookie, Referer, User-Agent, transaction ID, CSRF, and the documented X client state headers. Reddit accepts only Accept, User-Agent, and Cookie.

YouTube request headers are limited to Accept, Accept-Language, Authorization, Content-Type, Cookie, Origin, Referer, User-Agent, `X-Goog-Authuser`, `X-Goog-PageId`, `X-Goog-Visitor-Id`, `X-Youtube-Client-Name`, and `X-Youtube-Client-Version`. Never copy connection headers, Host, arbitrary metadata, Access settings, or inbound cookies into an upstream request.

The Worker returns HTTP 200 for a completed exchange, the actual upstream status in `X-Origin-Http`, upstream text in the response body, matching `Content-Type`, and origin cookies percent-encoded in repeatable `X-Origin-Set-Cookie` headers. It never returns upstream `Location`, and it does not expose the request's Access credentials to the origin. Request envelopes are limited to 256 KiB and responses to 12 MB. Validation, unsupported operations, unsafe paths, malformed query keys, and unexpected YouTube browse bodies are rejected before egress. Gateway, timeout, and size failures return a fixed error object.

The client retries only recognized transient upstream history statuses with a bounded retry. It never retries Access denials, redirects, 401/403 responses, invalid requests, or malformed response schemas. History and continuation state remain in the application; the Worker performs no caching or persistence.
