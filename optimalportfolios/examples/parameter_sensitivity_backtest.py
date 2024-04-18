"""
backtest parameter sensitivity of one method
"""
# imports
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf
from typing import Tuple
from enum import Enum
import qis as qis

# package
from optimalportfolios import PortfolioObjective, backtest_rolling_optimal_portfolio
import optimalportfolios.local_path as local_path


def fetch_universe_data() -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """
    fetch universe data for the portfolio construction:
    1. dividend and split adjusted end of day prices: price data may start / end at different dates
    2. benchmark prices which is used for portfolio reporting and benchmarking
    3. universe group data for portfolio reporting and risk attribution for large universes
    this function is using yfinance to fetch the price data
    """
    universe_data = dict(SPY='Equities',
                         QQQ='Equities',
                         EEM='Equities',
                         TLT='Bonds',
                         IEF='Bonds',
                         LQD='Credit',
                         HYG='HighYield',
                         GLD='Gold')
    tickers = list(universe_data.keys())
    group_data = pd.Series(universe_data)
    prices = yf.download(tickers, start=None, end=None, ignore_tz=True)['Adj Close']
    prices = prices[tickers]  # arrange as given
    prices = prices.asfreq('B', method='ffill')  # refill at B frequency
    benchmark_prices = prices[['SPY', 'TLT']]
    return prices, benchmark_prices, group_data


def run_max_diversification_sensitivity_to_span(prices: pd.DataFrame,
                                                benchmark_prices: pd.DataFrame,
                                                group_data: pd.Series,
                                                time_period: qis.TimePeriod  # for reporting
                                                ) -> plt.Figure:
    """
    test maximum diversification optimiser to span parameter
    span is number period for ewm filter
    span = 20 for daily data implies last 20 (trading) days contribute 50% of weight for covariance estimation
    we test sensitivity from fast (small span) to slow (large span)
    """
    # use daily returns
    returns_freq = 'W-WED'  # returns freq
    # span defined on number periods using returns_freq
    # for weekly returns assume 5 weeeks per month
    spans = {'1m': 5, '3m': 13, '6m': 26, '1y': 52, '2y': 104}

    # set global params for portfolios
    min_weights = {x: 0.0 for x in prices.columns}  # all weights >= 0
    max_weights = {x: 0.5 for x in prices.columns}  # all weights <= 0.5

    # now create a list of portfolios
    portfolio_datas = []
    for ticker, span in spans.items():
        portfolio_data = backtest_rolling_optimal_portfolio(prices=prices,
                                                            portfolio_objective=PortfolioObjective.MAX_DIVERSIFICATION,
                                                            min_weights=min_weights,
                                                            max_weights=max_weights,
                                                            rebalancing_freq='QE',  # portfolio rebalancing
                                                            returns_freq=returns_freq,
                                                            span=span,
                                                            ticker=f"span-{ticker}",  # portfolio id
                                                            rebalancing_costs=0.0010  # 10bp for rebalancin
                                                            )
        portfolio_datas.append(portfolio_data)

    # run cross portfolio report
    multi_portfolio_data = qis.MultiPortfolioData(portfolio_datas=portfolio_datas, benchmark_prices=benchmark_prices)
    fig = qis.generate_multi_portfolio_factsheet(multi_portfolio_data=multi_portfolio_data,
                                                 time_period=time_period,
                                                 **qis.fetch_default_report_kwargs(time_period=time_period))
    return fig


class UnitTests(Enum):
    MAX_DIVERSIFICATION_SPAN = 1


def run_unit_test(unit_test: UnitTests):

    if unit_test == UnitTests.MAX_DIVERSIFICATION_SPAN:
        prices, benchmark_prices, group_data = fetch_universe_data()
        prices = prices.loc['2003':, :]
        time_period = qis.TimePeriod(start='01Jan2005', end=prices.index[-1])  # backtest reporting
        fig = run_max_diversification_sensitivity_to_span(prices=prices,
                                                          benchmark_prices=benchmark_prices,
                                                          group_data=group_data,
                                                          time_period=time_period)

        # save png and pdf
        qis.save_fig(fig=fig, file_name=f"max_diversification_span", local_path=f"figures/")
        qis.save_figs_to_pdf(figs=[fig],
                             file_name=f"max_diversification_span",
                             orientation='landscape',
                             local_path=local_path.get_output_path())
    plt.show()


if __name__ == '__main__':

    unit_test = UnitTests.MAX_DIVERSIFICATION_SPAN

    is_run_all_tests = False
    if is_run_all_tests:
        for unit_test in UnitTests:
            run_unit_test(unit_test=unit_test)
    else:
        run_unit_test(unit_test=unit_test)
