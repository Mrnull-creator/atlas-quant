from fastapi.testclient import TestClient

from atlas_quant.platform.errors import AtlasQuantError
from atlas_quant.services.common.application import create_service_app


class SampleValidationError(AtlasQuantError):
    """Test application error with a client-facing status code."""

    error_code = "ATLAS_VALIDATION_ERROR"
    http_status_code = 422


def test_application_uses_default_metadata():
    app = create_service_app()

    assert app.title == "atlas-quant"
    assert app.version == "0.1.0"


def test_application_accepts_custom_metadata():
    app = create_service_app(
        service_name="market-data",
        version="1.0.0",
    )

    assert app.title == "market-data"
    assert app.version == "1.0.0"
    assert app.state.service_name == "market-data"


def test_liveness_endpoint():
    app = create_service_app(service_name="market-data")

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "alive": True,
        "service": "market-data",
    }


def test_readiness_endpoint():
    app = create_service_app(service_name="market-data")

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "service": "market-data",
    }


def test_openapi_document_is_available():
    app = create_service_app(
        service_name="market-data",
        version="1.0.0",
    )

    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200

    document = response.json()

    assert document["info"]["title"] == "market-data"
    assert document["info"]["version"] == "1.0.0"


def test_readiness_changes_with_application_lifecycle():
    app = create_service_app(service_name="market-data")

    assert app.state.ready is False

    with TestClient(app) as client:
        assert app.state.ready is True

        response = client.get("/health/ready")

        assert response.status_code == 200
        assert response.json()["ready"] is True

    assert app.state.ready is False


def test_atlas_quant_exception_returns_standard_response():
    app = create_service_app()

    @app.get("/test-error")
    async def trigger_error():
        raise SampleValidationError(
            "Invalid market data supplied."
        )

    with TestClient(app) as client:
        response = client.get("/test-error")

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "ATLAS_VALIDATION_ERROR",
            "message": "Invalid market data supplied.",
        }
    }


def test_unexpected_exception_returns_safe_response():
    app = create_service_app()

    @app.get("/test-unexpected-error")
    async def trigger_unexpected_error():
        raise RuntimeError("Sensitive internal information")

    with TestClient(
        app,
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/test-unexpected-error")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected internal error occurred.",
        }
    }

    assert "Sensitive internal information" not in response.text