import math
import statistics
from numbers import Integral, Real

import pandas as pd

from atlas_quant.services.strategy_research.domain.performance import (
    PerformanceMetrics,
)


_REQUIRED_COLUMNS = {
    "equity",
    "position",
    "units",
    "traded_units",
    "execution_price",
    "commission",
    "slippage_cost",
    "bid_open",
    "ask_open",
    "bid_close",
    "ask_close",
}


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a finite number.")

    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number.")

    return result


def _validate_inputs(
    results: pd.DataFrame,
    initial_equity: float,
    periods_per_year: int,
    risk_free_rate_annual: float,
) -> tuple[float, int, float]:
    if not isinstance(results, pd.DataFrame):
        raise TypeError("results must be a pandas DataFrame.")

    initial_equity = _finite_number(initial_equity, "initial_equity")
    if initial_equity <= 0:
        raise ValueError("initial_equity must be greater than zero.")

    if (
        isinstance(periods_per_year, bool)
        or not isinstance(periods_per_year, Integral)
        or periods_per_year <= 0
    ):
        raise ValueError("periods_per_year must be a positive integer.")

    risk_free_rate_annual = _finite_number(
        risk_free_rate_annual,
        "risk_free_rate_annual",
    )
    if risk_free_rate_annual <= -1:
        raise ValueError("risk_free_rate_annual must be greater than -1.")

    return (
        initial_equity,
        int(periods_per_year),
        risk_free_rate_annual,
    )


def _empty_metrics(
    initial_equity: float,
    periods_per_year: int,
    risk_free_rate_annual: float,
) -> PerformanceMetrics:
    return PerformanceMetrics(
        observations=0,
        initial_equity=initial_equity,
        final_equity=initial_equity,
        net_profit_loss=0.0,
        total_return=0.0,
        max_drawdown=0.0,
        max_drawdown_duration_periods=0,
        risk_free_rate_annual=risk_free_rate_annual,
        periods_per_year=periods_per_year,
        sharpe_ratio=None,
        sortino_ratio=None,
        gross_period_profit=0.0,
        gross_period_loss=0.0,
        profit_factor=None,
        trade_count=0,
        open_trade_count=0,
        average_exposure=None,
        max_exposure=None,
        total_turnover=0.0,
        turnover_per_period=None,
        spread_cost=0.0,
        slippage_cost=0.0,
        commission_cost=0.0,
        total_execution_cost=0.0,
    )


def _numeric_column(results: pd.DataFrame, name: str) -> list[float]:
    try:
        values = pd.to_numeric(results[name], errors="raise").tolist()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Column '{name}' must contain numeric values.") from exc

    converted: list[float] = []
    for value in values:
        if isinstance(value, bool):
            raise ValueError(f"Column '{name}' cannot contain boolean values.")

        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"Column '{name}' must contain only finite values.")

        converted.append(number)

    return converted


def _validate_results(results: pd.DataFrame) -> dict[str, list[float]]:
    missing = _REQUIRED_COLUMNS.difference(results.columns)
    if missing:
        raise ValueError(
            "Backtest results are missing required columns: "
            + ", ".join(sorted(missing))
        )

    if not isinstance(results.index, pd.DatetimeIndex):
        raise TypeError("Backtest results must have a DatetimeIndex.")

    if results.index.tz is None:
        raise ValueError("Backtest result timestamps must be timezone-aware.")

    if results.index.hasnans:
        raise ValueError("Backtest result timestamps cannot contain NaT.")

    if not results.index.is_monotonic_increasing:
        raise ValueError("Backtest result timestamps must be chronological.")

    if results.index.has_duplicates:
        raise ValueError("Backtest result timestamps must be unique.")

    columns = {
        name: _numeric_column(results, name)
        for name in _REQUIRED_COLUMNS
        if name != "execution_price"
    }

    execution_prices = pd.to_numeric(
        results["execution_price"],
        errors="coerce",
    ).tolist()

    columns["execution_price"] = [
        float(value) if pd.notna(value) else math.nan
        for value in execution_prices
    ]

    for name in ("equity",):
        if any(value <= 0 for value in columns[name]):
            raise ValueError(f"Column '{name}' must contain positive values.")

    for name in ("bid_open", "ask_open", "bid_close", "ask_close"):
        if any(value <= 0 for value in columns[name]):
            raise ValueError(f"Column '{name}' must contain positive prices.")

    for bid_name, ask_name in (
        ("bid_open", "ask_open"),
        ("bid_close", "ask_close"),
    ):
        if any(
            ask < bid
            for bid, ask in zip(columns[bid_name], columns[ask_name])
        ):
            raise ValueError(f"{ask_name} cannot be less than {bid_name}.")

    if any(value < 0 for value in columns["traded_units"]):
        raise ValueError("traded_units cannot contain negative values.")

    for name in ("commission", "slippage_cost"):
        if any(value < 0 for value in columns[name]):
            raise ValueError(f"Column '{name}' cannot contain negative costs.")

    for value in columns["position"]:
        if value not in (-1.0, 0.0, 1.0):
            raise ValueError("position values must be -1, 0, or 1.")

    for traded_units, execution_price in zip(
        columns["traded_units"],
        columns["execution_price"],
    ):
        if traded_units > 0 and (
            not math.isfinite(execution_price) or execution_price <= 0
        ):
            raise ValueError(
                "Rows with traded units must have a positive execution_price."
            )

    return columns


def _trade_counts(positions: list[float]) -> tuple[int, int]:
    completed_trades = 0
    previous_position = 0.0

    for position in positions:
        if previous_position != 0 and position == 0:
            completed_trades += 1
        elif (
            previous_position != 0
            and position != 0
            and position != previous_position
        ):
            # A reversal closes the previous episode and opens another.
            completed_trades += 1

        previous_position = position

    open_trades = 1 if positions and positions[-1] != 0 else 0
    return completed_trades, open_trades


def calculate_performance(
    results: pd.DataFrame,
    initial_equity: float,
    periods_per_year: int,
    risk_free_rate_annual: float = 0.0,
) -> PerformanceMetrics:
    """Calculate standardized metrics from canonical next-open results.

    The input is the output of the canonical next-open simulator. The
    simulator's equity already includes execution costs; this function
    reports cost attribution but never deducts those costs again.

    Profit factor is calculated from positive and negative net period P&L,
    not realized trade P&L. It is undefined when either side is zero.

    Sharpe uses sample standard deviation of periodic excess returns.
    Sortino uses the root mean square of all negative periodic excess returns.
    Both ratios are annualized using the explicitly supplied periods_per_year.
    """
    (
        initial_equity,
        periods_per_year,
        risk_free_rate_annual,
    ) = _validate_inputs(
        results,
        initial_equity,
        periods_per_year,
        risk_free_rate_annual,
    )

    if results.empty:
        return _empty_metrics(
            initial_equity,
            periods_per_year,
            risk_free_rate_annual,
        )

    columns = _validate_results(results)
    observations = len(results)

    equity = columns["equity"]
    final_equity = equity[-1]
    net_profit_loss = final_equity - initial_equity
    total_return = final_equity / initial_equity - 1.0

    # Include initial equity as the starting high-water mark.
    running_peak = initial_equity
    max_drawdown = 0.0
    current_drawdown_duration = 0
    max_drawdown_duration = 0

    for value in equity:
        running_peak = max(running_peak, value)
        drawdown = value / running_peak - 1.0
        max_drawdown = min(max_drawdown, drawdown)

        if drawdown < 0:
            current_drawdown_duration += 1
            max_drawdown_duration = max(
                max_drawdown_duration,
                current_drawdown_duration,
            )
        else:
            current_drawdown_duration = 0

    # Period P&L and returns are derived from equity, not from a separate
    # return column, so the analytics remain tied to the actual equity curve.
    period_pnl = [equity[0] - initial_equity]
    period_pnl.extend(
        current - previous
        for previous, current in zip(equity, equity[1:])
    )

    gross_period_profit = sum(value for value in period_pnl if value > 0)
    gross_period_loss = -sum(value for value in period_pnl if value < 0)

    profit_factor = None
    if gross_period_profit > 0 and gross_period_loss > 0:
        profit_factor = gross_period_profit / gross_period_loss

    period_returns = [value / initial_equity for value in period_pnl]
    period_returns[1:] = [
        current / previous - 1.0
        for previous, current in zip(equity, equity[1:])
    ]

    period_risk_free_rate = (
        (1.0 + risk_free_rate_annual) ** (1.0 / periods_per_year) - 1.0
    )
    excess_returns = [
        value - period_risk_free_rate
        for value in period_returns
    ]

    sharpe_ratio = None
    if len(excess_returns) >= 2:
        standard_deviation = statistics.stdev(excess_returns)
        if standard_deviation > 0:
            sharpe_ratio = (
                statistics.mean(excess_returns)
                / standard_deviation
                * math.sqrt(periods_per_year)
            )

    downside_squared = [
        min(value, 0.0) ** 2
        for value in excess_returns
    ]
    downside_deviation = math.sqrt(
        sum(downside_squared) / len(downside_squared)
    )

    sortino_ratio = None
    if downside_deviation > 0:
        sortino_ratio = (
            statistics.mean(excess_returns)
            / downside_deviation
            * math.sqrt(periods_per_year)
        )

    trade_count, open_trade_count = _trade_counts(columns["position"])

    exposures = [
        abs(units)
        * ((bid + ask) / 2.0)
        / equity_value
        for units, bid, ask, equity_value in zip(
            columns["units"],
            columns["bid_close"],
            columns["ask_close"],
            equity,
        )
    ]

    average_exposure = sum(exposures) / observations
    max_exposure = max(exposures)

    # Use the opening midpoint as the turnover reference price. This avoids
    # including slippage in turnover while counting both legs of reversals.
    total_traded_notional = sum(
        traded_units * ((bid + ask) / 2.0)
        for traded_units, bid, ask in zip(
            columns["traded_units"],
            columns["bid_open"],
            columns["ask_open"],
        )
    )
    total_turnover = total_traded_notional / initial_equity
    turnover_per_period = total_turnover / observations

    spread_cost = sum(
        traded_units * (ask - bid) / 2.0
        for traded_units, bid, ask in zip(
            columns["traded_units"],
            columns["bid_open"],
            columns["ask_open"],
        )
    )
    slippage_cost = sum(columns["slippage_cost"])
    commission_cost = sum(columns["commission"])
    total_execution_cost = (
        spread_cost + slippage_cost + commission_cost
    )

    return PerformanceMetrics(
        observations=observations,
        initial_equity=initial_equity,
        final_equity=final_equity,
        net_profit_loss=net_profit_loss,
        total_return=total_return,
        max_drawdown=max_drawdown,
        max_drawdown_duration_periods=max_drawdown_duration,
        risk_free_rate_annual=risk_free_rate_annual,
        periods_per_year=periods_per_year,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        gross_period_profit=gross_period_profit,
        gross_period_loss=gross_period_loss,
        profit_factor=profit_factor,
        trade_count=trade_count,
        open_trade_count=open_trade_count,
        average_exposure=average_exposure,
        max_exposure=max_exposure,
        total_turnover=total_turnover,
        turnover_per_period=turnover_per_period,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        commission_cost=commission_cost,
        total_execution_cost=total_execution_cost,
    )
