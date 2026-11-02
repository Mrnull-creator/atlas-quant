
from pathlib import Path

import pandas as pd


INPUT_FILE = Path(
    "data/processed/eurusd_m1_next_open_backtest.csv"
)
OUTPUT_FILE = Path(
    "data/processed/eurusd_next_open_monthly_audit.csv"
)


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Backtest results not found: {INPUT_FILE}"
        )

    data = pd.read_csv(
        INPUT_FILE,
        index_col=0,
        parse_dates=True,
    ).sort_index()

    if data.empty:
        raise ValueError("The results file is empty.")

    equity = pd.to_numeric(data["equity"], errors="raise")

    if not equity.index.is_unique:
        raise ValueError("Duplicate timestamps in results.")

    if not equity.index.is_monotonic_increasing:
        raise ValueError("Results are not chronological.")

    if not equity.map(lambda value: pd.notna(value) and value > 0).all():
        raise ValueError("Equity contains invalid or nonpositive values.")

    initial_equity = 10_000.0

    # Include starting capital as the baseline for the first month.
    month_end_equity = equity.resample("ME").last()

    monthly_start = pd.Series(
        [initial_equity],
        index=[equity.index[0] - pd.Timedelta(nanoseconds=1)],
    )

    monthly_path = pd.concat(
        [monthly_start, month_end_equity]
    ).sort_index()

    monthly_returns = monthly_path.pct_change().dropna()

    monthly = pd.DataFrame(
        {
            "month_end_equity": month_end_equity,
            "return_pct": monthly_returns * 100,
        }
    )


    # Identify partial months using timezone-naive monthly periods.
    # This avoids timezone warnings and correctly labels month-end rows.
    first_month = equity.index.tz_localize(None).to_period("M")[0]
    last_month = equity.index.tz_localize(None).to_period("M")[-1]

    monthly_periods = monthly.index.tz_localize(None).to_period("M")

    monthly["period_status"] = "Full month"
    monthly.loc[monthly_periods == first_month, "period_status"] = (
        "Partial first month"
    )
    monthly.loc[monthly_periods == last_month, "period_status"] = (
        "Partial last month"
    )

    if first_month == last_month:
        monthly.loc[monthly_periods == first_month, "period_status"] = (
            "Partial month"
        )

    # Correct the label if the data starts and ends in the same month.
    if first_month == last_month:
        monthly.loc[first_month.to_timestamp(), "period_status"] = (
            "Partial month"
        )

    yearly_equity = equity.resample("YE").last()
    yearly_returns = yearly_equity.pct_change()

    if not yearly_returns.empty:
        yearly_returns.iloc[0] = (
            yearly_equity.iloc[0] / initial_equity - 1
        )

    yearly = pd.DataFrame(
        {
            "year_end_equity": yearly_equity,
            "return_pct": yearly_returns * 100,
        }
    )

    peak_equity = equity.cummax()
    drawdown = equity / peak_equity - 1

    print("\n========== DATA CHECK ==========")
    print(f"First timestamp: {equity.index.min()}")
    print(f"Last timestamp:  {equity.index.max()}")
    print(f"Observations:    {len(equity):,}")
    print(f"Starting equity: ${initial_equity:,.2f}")
    print(f"Final equity:    ${equity.iloc[-1]:,.2f}")
    print(
        f"Total return:    "
        f"{(equity.iloc[-1] / initial_equity - 1):.2%}"
    )
    print(f"Minimum equity:  ${equity.min():,.2f}")
    print(f"Maximum drawdown: {drawdown.min():.2%}")

    print("\n========== YEARLY PERFORMANCE ==========")
    print(yearly.round(2).to_string())

    print("\n========== WORST 10 MONTHS ==========")
    print(
        monthly.sort_values("return_pct")
        .head(10)
        .round(2)
        .to_string()
    )

    print("\n========== BEST 10 MONTHS ==========")
    print(
        monthly.sort_values("return_pct", ascending=False)
        .head(10)
        .round(2)
        .to_string()
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(OUTPUT_FILE)

    print(f"\nMonthly audit saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()