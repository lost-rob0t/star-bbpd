# BBPD extended actor family

This slice adds four public actor boundaries while keeping StarIntel logical identity separate from transport queues.

## Actors

### `bbot`

`star://<authority>/actor/bbot`

BBOT 3.x runs with full JSON event output and a deployment allowlist of modules. The BBOT actor rejects modules such as `nuclei` that have their own StarIntel actor boundary. This keeps authorization, rate limits, provenance and failure accounting explicit.

### `nuclei`

`star://<authority>/actor/nuclei`

Nuclei requires `authorized_scope=true`. The default command:

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

The first adapter captures one bounded `GET` or `HEAD` exchange and emits a StarIntel 0.9.2 `http-transaction`. Response-body bytes stay in artifact custody; the document contains URI/hash/size metadata only.

A deployment may set `tools.http-proxy.proxy_env` to the name of an environment variable containing one operator-managed proxy URL. Targets cannot supply proxy endpoints or credentials.

`proxy_actor_uri` is a logical deployment binding for a registered egress/proxy actor. It is not parsed into an endpoint and grants no authorization. RuntimePlan/StarLang dispatch owns actor invocation when that capability is available.

### `screenshot`

`star://<authority>/actor/screenshot`

The local fallback uses gowitness v3 JSONL output. HTML and per-request network-log capture are disabled by default; screenshot bytes are stored as artifacts. `web-capture` stores the artifact URI/hash/size, page URL/final URL/status/title and challenge state.

The preferred same-session path is the shared Pro Actors WebDriver system. That system owns the opaque browser session and is the component allowed to apply an already-authorized CAPTCHA result in the same session.

## CAPTCHA domain handoff

Capture documents use the approved StarIntel CAPTCHA domain semantics rather than naming a provider directly.

When a supported challenge is observed, evidence may preserve:

- `challenge_status`;
- `captcha_detection_id`;
- `captcha_capability = "captcha.solve"`;
- opaque `browser_session_ref` when a browser session exists;
- opaque `network_context_ref` for source-network binding;
- logical `proxy_actor_uri` when egress is actor-managed.

BBPD never writes CAPTCHA answers, provider credentials, cookie jars, raw browser session identifiers, proxy credentials, or unrestricted provider response bodies into ordinary documents.

The execution flow is:

1. capture actor preserves its source request/browser session and emits challenge evidence;
2. StarIntel runtime policy determines whether CAPTCHA solving is enabled and authorized;
3. registry/RuntimePlan discovers a healthy exact `captcha.solve` capability;
4. the server-owned `CaptchaDomainServer` selects an eligible solver according to policy and source-context requirements;
5. solver returns an opaque typed result reference;
6. the shared WebDriver/source actor applies that result in the original authorized session;
7. downstream acceptance is recorded separately from solver completion;
8. the source actor resumes or returns a typed blocked/partial outcome.

If the domain server is disabled/unavailable, capture remains valid evidence and does not busy-loop or silently reinterpret the challenge as a missing target.

## Proxy/network binding

The CAPTCHA broker's exact capability matching includes proxy/network/session requirements. BBPD therefore records opaque source-context references rather than trying to rotate proxies or reconstruct browser identity itself.

There is no evasion-oriented proxy rotation, fingerprint fabrication, or target-controlled proxy credential injection in this slice.

## StarIntel 0.9.2 network-capture profile

The profile is additive over the immutable v0.9 envelope:

- wire envelope remains `schema_version = 0.9.0`;
- base 0.9.1 documents remain valid;
- new canonical dtypes are `http-transaction` and `web-capture`;
- raw body/DOM/screenshot bytes remain artifacts;
- sensitive HTTP headers are redacted before persistence;
- challenged captures advertise `captcha.solve` without selecting a provider;
- JSON-LD projection is provided by `starintel_doc.network_capture`.

Cross-repo runtime implementation is tracked by `starintel-server#216` and `starintel-pro-actors#81`.
