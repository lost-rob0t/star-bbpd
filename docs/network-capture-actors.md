# BBPD extended actor family

This slice adds four public actor boundaries while keeping StarIntel application identity separate from transport queues.

## Actors

### `bbot`

`star://<authority>/actor/bbot`

BBOT 3.x is run with JSON stdout and a deployment allowlist of modules. The BBOT actor intentionally rejects modules such as `nuclei` that have their own StarIntel actor boundary. This keeps accounting, authorization, rate limits and provenance explicit.

### `nuclei`

`star://<authority>/actor/nuclei`

Nuclei requires `authorized_scope=true` in the target options. The default command:

- emits JSONL;
- omits raw request/response pairs;
- disables Interactsh/OAST;
- restricts local/private-network access;
- excludes `dos`, `fuzz`, `bruteforce`, `local`, headless, code and JavaScript templates;
- enforces deployment rate/concurrency limits;
- permits target-selected tags/templates only from deployment allowlists.

Results become StarIntel `observation` documents plus relations. This actor is detection-oriented, not an exploitation runner.

### `http-proxy`

`star://<authority>/actor/http-proxy`

The first transport adapter captures one bounded `GET` or `HEAD` transaction and emits a StarIntel 0.9.2 `http-transaction`. Response body bytes are stored under artifact custody with only URI/hash/size metadata in the document.

A deployment may set `tools.http-proxy.proxy_env` to the name of an environment variable containing one static operator-managed HTTP/SOCKS-compatible proxy URL. Targets cannot supply proxy endpoints or credentials.

`proxy_actor_uri` is preserved as logical provenance for a deployment-selected egress/proxy actor. It is not parsed into a transport endpoint and does not grant authorization.

### `screenshot`

`star://<authority>/actor/screenshot`

The screenshot actor uses gowitness v3 JSONL output. It disables HTML and per-request network-log capture by default and writes screenshot bytes to artifact custody. The StarIntel `web-capture` stores the artifact URI/hash/size, page URL/final URL/status/title and challenge state.

A static operator proxy may be configured through the deployment. Target options cannot rotate proxy endpoints.

## Challenge handoff

Both capture dtypes include `challenge_status` and optional `challenge_actor_uri`.

BBPD records challenges such as 403/407/429/503 responses or obvious human-verification pages. It does not implement CAPTCHA solving, browser fingerprint evasion, or proxy rotation for bypass. A configured challenge actor URI is provenance/routing metadata for an external operator-approved workflow; no solver credentials or challenge tokens are embedded in normal documents.

## StarIntel profile

Network capture uses the additive 0.9.2 profile:

- wire envelope remains `schema_version = 0.9.0`;
- base 0.9.1 documents remain valid;
- new canonical dtypes are `http-transaction` and `web-capture`;
- raw body/DOM/screenshot bytes remain artifacts;
- sensitive HTTP headers are redacted before persistence;
- JSON-LD projection is provided by `starintel_doc.network_capture`.
