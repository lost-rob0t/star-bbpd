from bbp.settlement import FailureKind, PublishResult, failure_decision


def test_transient_first_failure_requeues():
    decision = failure_decision(FailureKind.TRANSIENT, redelivered=False)
    assert decision.requeue is True
    assert decision.reason == "transient-first-attempt"


def test_transient_redelivery_is_rejected():
    decision = failure_decision(FailureKind.TRANSIENT, redelivered=True)
    assert decision.requeue is False
    assert decision.reason == "transient-retry-exhausted"


def test_permanent_failure_never_requeues():
    decision = failure_decision(FailureKind.PERMANENT, redelivered=False)
    assert decision.requeue is False


def test_publish_result_reports_transient_failure():
    result = PublishResult.failure(
        "documents.ingest.domain",
        3,
        FailureKind.TRANSIENT,
        "connection lost",
    )
    assert result.ok is False
    assert result.transient is True
