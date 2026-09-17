from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from starintel_doc import Document as StarIntelDocument

from bbp.determinism import apply_provenance, deterministic_document_id, stable_timestamp
from bbp.document_boundary import CanonicalDocument, SCHEMA_VERSION, TargetDocument, load_schema_contract


def build_observation(
    target: TargetDocument,
    *,
    actor: str,
    observation_type: str,
    value: Any,
    subject_id: str = "",
    method: str = "",
    instrument: str = "",
    observed_at: str | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> CanonicalDocument:
    if not actor or not observation_type:
        raise ValueError("actor and observation_type are required")
    timestamp = observed_at or stable_timestamp(target.to_dict())
    identity = {
        "dataset": target.dataset,
        "observation_type": observation_type,
        "subject_id": subject_id,
        "value": value,
        "method": method,
        "instrument": instrument,
    }
    data = {
        "observer_id": actor,
        "subject_id": subject_id or target.id,
        "observation_type": observation_type,
        "value": deepcopy(value),
        "method": method,
        "instrument": instrument,
        "observed_at": timestamp,
    }
    document = {
        "_id": deterministic_document_id("observation", identity),
        "dataset": target.dataset,
        "dtype": "observation",
        "schema_version": SCHEMA_VERSION,
        "version": 1,
        "date_added": timestamp,
        "date_updated": timestamp,
        "sources": deepcopy(target.sources),
        "evidence": [],
        "data": data,
        "extensions": deepcopy(target.extensions),
    }
    if extensions:
        document["extensions"].update(deepcopy(dict(extensions)))
    document = apply_provenance(
        target.to_dict(),
        document,
        actor,
        f"observation:{observation_type}",
    )
    schema = load_schema_contract().schema
    return CanonicalDocument(StarIntelDocument.from_dict(document, schema))
