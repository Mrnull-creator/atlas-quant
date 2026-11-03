import pytest

from atlas_quant.platform.errors import (
    AtlasQuantError,
    BacktestError,
    ConfigurationError,
    DataError,
    RiskError,
    StrategyError,
)


@pytest.mark.parametrize(
    "error_class",
    [
        AtlasQuantError,
        ConfigurationError,
        DataError,
        StrategyError,
        BacktestError,
        RiskError,
    ],
)
def test_application_errors_default_to_http_500(error_class):
    error = error_class("Test error")

    assert error.http_status_code == 500


def test_application_error_accepts_custom_http_status():
    error = DataError(
        "Invalid market data",
        http_status_code=422,
    )

    assert error.http_status_code == 422


def test_custom_http_status_does_not_change_error_code():
    error = RiskError(
        "Risk limit exceeded",
        http_status_code=422,
    )

    assert error.error_code == "ATLAS_RISK_ERROR"
    assert error.http_status_code == 422


def test_error_details_are_preserved():
    error = DataError(
        "Invalid market data",
        details={"symbol": "EURUSD"},
        http_status_code=422,
    )

    assert error.to_dict() == {
        "error_code": "ATLAS_DATA_ERROR",
        "message": "Invalid market data",
        "details": {"symbol": "EURUSD"},
    }