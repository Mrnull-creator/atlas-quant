
import pytest
from pydantic import SecretStr, ValidationError

from atlas_quant.platform.config import Settings


def test_default_settings_are_research_only():
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.service_name == "atlas-quant"
    assert settings.service_version == "0.1.0"
    assert settings.trading_mode == "research"


def test_custom_environment_and_trading_mode():
    settings = Settings(
        environment="testing",
        trading_mode="backtest",
        _env_file=None,
    )

    assert settings.environment == "testing"
    assert settings.trading_mode == "backtest"


def test_invalid_environment_is_rejected():
    with pytest.raises(ValidationError):
        Settings(environment="staging", _env_file=None)


def test_live_trading_mode_is_rejected():
    with pytest.raises(ValidationError):
        Settings(trading_mode="live", _env_file=None)


def test_database_url_is_secret():
    settings = Settings(
        database_url="postgresql://user:password@localhost/atlas",
        _env_file=None,
    )

    assert isinstance(settings.database_url, SecretStr)
    assert "password" not in str(settings.database_url)
    assert settings.database_url.get_secret_value() == (
        "postgresql://user:password@localhost/atlas"
    )


def test_environment_variables_are_supported(monkeypatch):
    monkeypatch.setenv("ATLAS_ENVIRONMENT", "testing")
    monkeypatch.setenv("ATLAS_TRADING_MODE", "backtest")

    settings = Settings(_env_file=None)

    assert settings.environment == "testing"
    assert settings.trading_mode == "backtest"