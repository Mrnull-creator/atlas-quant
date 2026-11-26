from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Standardized, auditable performance metrics for a backtest.

    Return, drawdown, exposure, and turnover values are decimal fractions.
    Monetary values use the simulator's account currency (currently USD).
    Gross loss is reported as a positive magnitude.
    """

    observations: int
    initial_equity: float
    final_equity: float
    net_profit_loss: float
    total_return: float

    max_drawdown: float
    max_drawdown_duration_periods: int

    risk_free_rate_annual: float
    periods_per_year: int
    sharpe_ratio: float | None
    sortino_ratio: float | None

    gross_period_profit: float
    gross_period_loss: float
    profit_factor: float | None

    trade_count: int
    open_trade_count: int

    average_exposure: float | None
    max_exposure: float | None

    total_turnover: float
    turnover_per_period: float | None

    spread_cost: float
    slippage_cost: float
    commission_cost: float
    total_execution_cost: float
