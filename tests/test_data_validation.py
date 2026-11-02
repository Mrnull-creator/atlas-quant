
import pandas as pd
import pytest

from atlas_quant.data.validation import validate_ohlc


def sample_data():
    index = pd.date_range(
        "2026-01-01",
        periods=2,
        freq="h",
        tz="UTC",
    )

    return pd.DataFrame(
        {
            "open": [1.1000, 1.1010],
            "high": [1.1020, 1.1030],
            "low": [1.0990, 1.1000],
            "close": [1.1010, 1.1020],
        },
        index=index,
    )


def test_valid_candles():
    result = validate_ohlc(sample_data())

    assert len(result) == 2
    assert result.index.tz is not None


def test_invalid_candle():
    data = sample_data()
    data.iloc[0, data.columns.get_loc("high")] = 1.0500

    with pytest.raises(ValueError):
        validate_ohlc(data)


def test_duplicate_timestamps():
    data = sample_data()
    data.index = [data.index[0], data.index[0]]

    with pytest.raises(ValueError):
        validate_ohlc(data)


def test_timezone_required():
    data = sample_data()
    data.index = data.index.tz_localize(None)

    with pytest.raises(ValueError):
        validate_ohlc(data)