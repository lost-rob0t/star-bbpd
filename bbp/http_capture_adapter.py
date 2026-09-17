from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

CHALLENGE_CODES = {401, 403, 407, 429, 503}


def _body_artifact(
    body: bytes,
    artifact_dir: Path,
    artifact_uri_prefix: str,
) -> tuple[str, str]:
    digest = hashlib.sha256(body).hexdigest()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"{digest}.bin"
    if not path.exists():
        path.write_bytes(body)
    return f"{artifact_uri_prefix.rstrip('/')}/{path.name}", f"sha256:{digest}"


def capture(
    url: str,
    *,
    method: str,
    timeout: float,
    max_body_bytes: int,
    artifact_dir: Path,
    artifact_uri_prefix: str,
    proxy_url: str = "",
) -> dict[str, object]:
    if method not in {"GET", "HEAD"}:
        raise ValueError("HTTP capture adapter only permits GET and HEAD")
    if max_body_bytes < 0:
        raise ValueError("max_body_bytes must be non-negative")

    handlers = []
    if proxy_url:
        handlers.append(ProxyHandler({"http": proxy_url, "https": proxy_url}))
    opener = build_opener(*handlers)
    request = Request(url, method=method, headers={"User-Agent": "star-bbpd-http-capture/0.9.2"})
    started = time.time()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
    body = b""
    truncated = False

    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        response = error

    with response:
        status = int(getattr(response, "status", response.getcode()))
        headers = dict(response.headers.items())
        if method != "HEAD" and max_body_bytes > 0:
            body = response.read(max_body_bytes + 1)
            if len(body) > max_body_bytes:
                body = body[:max_body_bytes]
                truncated = True
        final_url = response.geturl()

    ended = time.time()
    ended_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ended))
    record: dict[str, object] = {
        "method": method,
        "url": final_url,
        "response_status": status,
        "response_headers": headers,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": round((ended - started) * 1000, 3),
        "response_body_size": len(body),
        "response_truncated": truncated,
        "challenge_status": "observed" if status in CHALLENGE_CODES else "none",
    }
    if body:
        uri, digest = _body_artifact(body, artifact_dir, artifact_uri_prefix)
        record["response_body_artifact_uri"] = uri
        record["response_body_hash"] = digest
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture one bounded HTTP transaction")
    parser.add_argument("--url", required=True)
    parser.add_argument("--method", choices=("GET", "HEAD"), default="GET")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-body-bytes", type=int, default=1048576)
    parser.add_argument("--artifact-dir", default="artifacts/http-bodies")
    parser.add_argument("--artifact-uri-prefix", default="artifact://bbp/http-bodies")
    parser.add_argument(
        "--proxy-env",
        default="",
        help="read one operator-managed proxy URL from this environment variable",
    )
    args = parser.parse_args()
    proxy_url = os.environ.get(args.proxy_env, "") if args.proxy_env else ""
    record = capture(
        args.url,
        method=args.method,
        timeout=args.timeout,
        max_body_bytes=args.max_body_bytes,
        artifact_dir=Path(args.artifact_dir),
        artifact_uri_prefix=args.artifact_uri_prefix,
        proxy_url=proxy_url,
    )
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
