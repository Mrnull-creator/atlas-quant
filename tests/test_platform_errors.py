
import pytest

from atlas_quant.platform.errors import (
    AtlasQuantError,
    ConfigurationError,
    DataError,
    StrategyError,
    BacktestError,
    RiskError,
)


@pytest.mark.parametrize(
    "error_class, expected_code",
    [
        (ConfigurationError, "ATLAS_CONFIGURATION_ERROR"),
        (DataError, "ATLAS_DATA_ERROR"),
        (StrategyError, "ATLAS_STRATEGY_ERROR"),
        (BacktestError, "ATLAS_BACKTEST_ERROR"),
        (RiskError, "ATLAS_RISK_ERROR"),
    ],
)
def test_error_codes(error_class, expected_code):
    error = error_class("Test error")

    assert error.error_code == expected_code
    assert isinstance(error, AtlasQuantError)


def test_error_message():
    error = RiskError("Risk limit exceeded")

    assert str(error) == "Risk limit exceeded"
    assert error.message == "Risk limit exceeded"


def test_error_details():
    error = RiskError(
        "Risk limit exceeded",
        details={"limit": 100},
    )

    assert error.details == {"limit": 100}


def test_error_serialization():
    error = RiskError(
        "Risk limit exceeded",
        details={"limit": 100},
    )

    assert error.to_dict() == {
        "error_code": "ATLAS_RISK_ERROR",
        "message": "Risk limit exceeded",
        "details": {"limit": 100},
    }


def test_empty_details_default():
    error = DataError("Invalid market data")

    assert error.details == {}