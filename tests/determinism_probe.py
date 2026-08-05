from __future__ import annotations

from bbp.determinism import canonical_json_bytes, normalize_url, relation_document_id

payload = {
    "relation": relation_document_id("scan-a", "source", "related-to", "target"),
    "url": normalize_url("HTTPS://BÜCHER.example:443/%7e?a=1&a=2#frag").url,
    "values": {"beta", "alpha", "gamma"},
}
payload["values"] = sorted(payload["values"])
print(canonical_json_bytes(payload).decode("utf-8"))
