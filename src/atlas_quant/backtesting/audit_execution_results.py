from pathlib import Path

import pandas as pd


INPUT_FILE = Path(
    "data/processed/eurusd_m1_execution_backtest.csv"
)


def main():
    data = pd.read_csv(
        INPUT_FILE,
        index_col=0,
        parse_dates=True,
    ).sort_index()

    if data.empty:
        raise ValueError("The results file is empty.")

    equity = data["equity"].astype(float)

    if (equity <= 0).any():
        print(
            "WARNING: Equity reached zero or below. "
            "Percentage-return summaries may be misleading."
        )

    initial_equity = 10_000.0

    # End-of-month equity values.
    monthly_equity = equity.resample("ME").last()

    # Include initial capital as the baseline before the first month.
    monthly_start = pd.Series(
        [initial_equity],
        index=[equity.index[0] - pd.Timedelta(nanoseconds=1)],
    )

    monthly_path = pd.concat(
        [monthly_start, monthly_equity]
    ).sort_index()

    monthly_returns = monthly_path.pct_change().dropna()

    monthly = pd.DataFrame({
        "equity": monthly_equity,
        "return_pct": monthly_returns * 100,
    })

    monthly["year"] = monthly.index.year

    # Year-end equity and calendar-year returns.
    yearly_equity = equity.resample("YE").last()
    previous_equity = yearly_equity.shift(1)

    yearly_returns = yearly_equity / previous_equity - 1

    if len(yearly_returns):
        yearly_returns.iloc[0] = (
            yearly_equity.iloc[0] / initial_equity - 1
        )

    yearly = pd.DataFrame({
        "year_end_equity": yearly_equity,
        "return_pct": yearly_returns * 100,
    })

    print("\n========== YEARLY PERFORMANCE ==========")
    print(yearly.round(2).to_string())

    print("\n========== WORST 10 MONTHS ==========")
    print(
        monthly.sort_values("return_pct")
        .head(10)[["equity", "return_pct"]]
        .round(2)
        .to_string()
    )

    print("\n========== BEST 10 MONTHS ==========")
    print(
        monthly.sort_values("return_pct", ascending=False)
        .head(10)[["equity", "return_pct"]]
        .round(2)
        .to_string()
    )

    print("\n========== EQUITY CHECK ==========")
    print(f"Starting equity: ${initial_equity:,.2f}")
    print(f"Final equity:    ${equity.iloc[-1]:,.2f}")
    print(f"Minimum equity:  ${equity.min():,.2f}")
    print(f"Maximum drawdown: {data['drawdown'].min():.2%}")

    output_file = Path(
        "data/processed/eurusd_execution_monthly_audit.csv"
    )
    monthly.to_csv(output_file)
    print(f"\nMonthly audit saved to: {output_file}")


if __name__ == "__main__":
    main()