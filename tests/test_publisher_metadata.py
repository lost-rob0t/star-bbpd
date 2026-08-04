from bbp.publisher_pool import document_properties, event_properties


def test_document_properties_preserve_canonical_dtype_and_json_encoding():
    properties = document_properties("domain")

    assert properties.type == "domain"
    assert properties.content_type == "application/json"
    assert properties.content_encoding == "utf-8"
    assert properties.delivery_mode == 2


def test_relation_properties_use_relation_dtype():
    assert document_properties("relation").type == "relation"


def test_event_properties_preserve_json_encoding():
    properties = event_properties()

    assert properties.content_type == "application/json"
    assert properties.content_encoding == "utf-8"
    assert properties.delivery_mode == 2
