from atlas_quant.platform.health import (
    liveness_check,
    readiness_check,
)


def test_liveness_reports_service_as_alive():
    status = liveness_check("market-data")

    assert status.alive is True
    assert status.ready is True
    assert status.service == "market-data"


def test_readiness_defaults_to_ready():
    status = readiness_check("market-data")

    assert status.alive is True
    assert status.ready is True


def test_readiness_can_report_unavailable_dependencies():
    status = readiness_check(
        "market-data",
        dependencies_ready=False,
    )

    assert status.alive is True
    assert status.ready is False


def test_health_status_serializes_to_dictionary():
    status = readiness_check("market-data")

    assert status.to_dict() == {
        "alive": True,
        "ready": True,
        "service": "market-data",
    }