# Security model

Single owner. Not multi-tenant. Treat the host as holding your cloud credentials.

| Requirement | Control | Verified by |
|---|---|---|
| Keys stay server-side | Settings object hides secrets from `repr`; no endpoint returns them; the browser holds only a session cookie (HMAC-signed, `HttpOnly`, `SameSite=Strict`, 12 h) | `test_no_secret_in_state`; e2e "token appears nowhere in API output or logs" |
| Secrets out of logs | `security.redact` scrubs known secret *values* plus token shapes (`sk-`, `hf_`, `ghp_`, `Authorization: Bearer`, `key=…`) from every job event, stored error and log record | `test_redaction`, `test_logs_are_redacted_in_api` |
| Terminal owner-only | Disabled unless `ENABLE_TERMINAL=1`; WebSocket needs the owner cookie **and** a same-origin `Origin`; one session at a time; idle (15 min) and absolute (4 h) timeouts; scrubbed environment; process group killed on disconnect | e2e scenario D (unauth, cross-origin, working shell, no secrets in env) |
| Never run downloaded repo code on the app server | Registry installs target Modal. `installer.plan()` states `executes_third_party_code_on_app_server: false`. The only files fetched locally are listed model blobs, sha256-pinned, read by onnxruntime and never executed. Inspection reads API metadata and small text files only; scaffolded adapter manifests cannot run until the owner sets `reviewed_by_owner: true` and pins a 40-char commit | `test_install_plan_*`, `test_scaffold_and_validate_manifest` |
| Pinned dependencies | `requirements.lock` (all resolved versions), registry revisions are commit SHAs, vendored xterm.js 6.0.0 | review |
| CSRF / cross-site | Cookie writes require a same-origin `Origin`; bearer-token callers exempt | `test_cookie_writes_require_same_origin` |
| Brute force | token compare is constant-time; 8 failures / 5 min per client → 429 | `test_login_wrong_token_and_throttle` |
| Path traversal | media endpoint is a whitelist of filenames + `safe_join` | `test_media_whitelist_and_traversal`, `test_safe_join` |
| XSS from inspected repos | frontend uses `textContent` only; no `innerHTML` anywhere | review |
| No auto-publishing | there is no publishing code | n/a |

## Known limits — read these

* **The terminal is a real shell on the app host as the app's OS user.** The scrubbed environment hides secrets from `env`, but
  the same user can read `data/` (including `owner_token` and the database) and your `.env` if it is in reach. For real isolation
  set `TERMINAL_CMD` to enter a separate container/user with no secrets mounted (example in `.env.example`) — **untested here**.
  If you do not need it, leave it off.
* The session cookie's key is derived from the owner token; rotating the token signs everyone out. There is one owner; there is no
  user management, 2FA or audit log. Put it behind HTTPS (and ideally a VPN or an identity-aware proxy) before exposing it.
* `Secure` cookies are only set when the request arrives as HTTPS — run behind a TLS-terminating proxy that forwards the scheme.
* Redaction is pattern-based; a secret in an unusual format that is not in the process environment can slip through. Do not paste
  secrets into chat.
* The draft renderer and the template writer have no external inputs beyond the text you type. The LLM adapters send your brief
  and script text to the provider you configure.
* Voice cloning (Chatterbox) is not implemented. If you add it, use only voices you have the right to use, and label synthetic
  hosts as such where the platform requires it.
* Modal functions run with whatever secrets you attach; attach only `HF_TOKEN` (read-only) and nothing else.
