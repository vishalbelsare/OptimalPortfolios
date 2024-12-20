"""
implementation of maximum diversification objective
"""
from __future__ import division

import numpy as np
import pandas as pd
import qis as qis
from scipy.optimize import minimize
from typing import Optional, Dict
from enum import Enum

from optimalportfolios.utils.portfolio_funcs import (calculate_portfolio_var, calculate_risk_contribution)
from optimalportfolios.utils.filter_nans import filter_covar_and_vectors_for_nans
from optimalportfolios.optimization.constraints import Constraints, long_only_constraint, total_weight_constraint
from optimalportfolios.utils.covar_matrix import (squeeze_covariance_matrix, estimate_rolling_lasso_covar, estimate_rolling_ewma_covar)


def rolling_equal_risk_contribution(prices: pd.DataFrame,
                                    constraints0: Constraints,
                                    time_period: qis.TimePeriod,  # when we start building portfolios
                                    risk_budget: pd.Series = None,
                                    returns_freq: str = 'W-WED',
                                    rebalancing_freq: str = 'QE',
                                    span: int = 52,  # 1y of weekly returns
                                    squeeze_factor: Optional[float] = None
                                    ) -> pd.DataFrame:
    """
    compute equal risk contribution
    risk_budget sets the risk bundets
    """
    # compute ewma covar with fill nans in covar using zeros
    pd_covars = estimate_rolling_ewma_covar(prices=prices,
                                            time_period=time_period,
                                            returns_freq=returns_freq,
                                            rebalancing_freq=rebalancing_freq,
                                            span=span)
    weights = {}
    weights_0 = None
    for date, pd_covar in pd_covars.items():
        weights_ = wrapper_equal_risk_contribution(pd_covar=pd_covar,
                                                   constraints0=constraints0,
                                                   weights_0=weights_0,
                                                   risk_budget=risk_budget,
                                                   squeeze_factor=squeeze_factor)
        weights_0 = weights_  # update for next rebalancing
        weights[date] = weights_
    weights = pd.DataFrame.from_dict(weights, orient='index')
    weights = weights.reindex(columns=prices.columns.to_list())
    return weights


def rolling_equal_risk_contribution_lasso_covar(benchmark_prices: pd.DataFrame,
                                                prices: pd.DataFrame,
                                                constraints0: Constraints,
                                                time_period: qis.TimePeriod,  # when we start building portfolios
                                                pd_covars: Dict[pd.Timestamp, pd.DataFrame] = None,
                                                risk_budget: pd.Series = None,
                                                returns_freq: str = 'W-WED',
                                                rebalancing_freq: str = 'QE',
                                                span: int = 52,  # 1y of weekly returns
                                                reg_lambda: float = 1e-8,
                                                squeeze_factor: Optional[float] = None
                                                ) -> pd.DataFrame:
    """
    use benchmarks to compute the benchmark covar matrix
    use lasso betas for computing
    compute equal risk contributgion
    risk_budget sets the risk budets
    """
    if pd_covars is None:
        pd_covars = estimate_rolling_lasso_covar(benchmark_prices=benchmark_prices,
                                                 prices=prices,
                                                 time_period=time_period,
                                                 returns_freq=returns_freq,
                                                 rebalancing_freq=rebalancing_freq,
                                                 span=span,
                                                 reg_lambda=reg_lambda,
                                                 squeeze_factor=squeeze_factor)
    weights = {}
    weights_0 = None
    for date, pd_covar in pd_covars.items():
        weights_ = wrapper_equal_risk_contribution(pd_covar=pd_covar,
                                                   constraints0=constraints0,
                                                   weights_0=weights_0,
                                                   risk_budget=risk_budget,
                                                   squeeze_factor=squeeze_factor)
        weights_0 = weights_  # update for next rebalancing
        weights[date] = weights_
    weights = pd.DataFrame.from_dict(weights, orient='index')
    weights = weights.reindex(columns=prices.columns.to_list())
    return weights


def wrapper_equal_risk_contribution(pd_covar: pd.DataFrame,
                                    constraints0: Constraints,
                                    weights_0: pd.Series = None,
                                    risk_budget: pd.Series = None,
                                    squeeze_factor: Optional[float] = None,
                                    verbouse: bool = False
                                    ) -> pd.Series:
    """
    create wrapper accounting for nans or zeros in covar matrix
    assets in columns/rows of covar must correspond to alphas.index
    """
    # filter out assets with zero variance or nans
    vectors = dict(min_weights=constraints0.min_weights, max_weights=constraints0.max_weights,
                   weights_0=weights_0, risk_budget=risk_budget)
    clean_covar, good_vectors = filter_covar_and_vectors_for_nans(pd_covar=pd_covar, vectors=vectors)

    if squeeze_factor is not None and squeeze_factor > 0.0:
        clean_covar = squeeze_covariance_matrix(clean_covar, squeeze_factor=squeeze_factor)

    total_to_good_ratio = len(pd_covar.columns) / len(clean_covar.columns)

    if risk_budget is not None:
        risk_budget = risk_budget.loc[clean_covar.columns].fillna(0.0)
        risk_budget *= total_to_good_ratio

    constraints = constraints0.update_with_valid_tickers(valid_tickers=clean_covar.columns.to_list(),
                                                         total_to_good_ratio=total_to_good_ratio,
                                                         weights_0=weights_0)

    weights0 = opt_equal_risk_contribution(covar=clean_covar.to_numpy(),
                                          constraints=constraints,
                                          risk_budget=risk_budget.to_numpy())
    weights = pd.Series(weights0, index=clean_covar.index)
    weights = weights.reindex(index=pd_covar.index).fillna(0.0)  # align with tickers

    if verbouse:
        asset_rc = calculate_risk_contribution(weights0, clean_covar.to_numpy())
        asset_rc_ratio = asset_rc / np.nansum(asset_rc)
        df = pd.concat([pd.Series(weights0, index=clean_covar.columns, name='weights'),
                        pd.Series(asset_rc, index=clean_covar.columns, name='asset_rc'),
                        risk_budget.rename('risk_budget'),
                        pd.Series(asset_rc_ratio, index=clean_covar.columns, name='asset_rc_ratio')
                        ], axis=1)
        print(df)

    return weights


def opt_equal_risk_contribution(covar: np.ndarray,
                                constraints: Constraints,
                                risk_budget: np.ndarray = None
                                ) -> np.ndarray:
    """
    can solve only for long onlz portfolios
    """
    n = covar.shape[0]
    x0 = np.ones(n) / n

    constraints_ = constraints.set_scipy_constraints(covar=covar)

    res = minimize(risk_budget_objective, x0, args=[covar, risk_budget], method='SLSQP', constraints=constraints_,
                   options={'ftol': 1e-18, 'maxiter': 200})

    optimal_weights = res.x

    if optimal_weights is None:
        # raise ValueError(f"not solved")
        print(f"not solved")
        if constraints.weights_0 is not None:
            optimal_weights = constraints.weights_0
            print(f"using weights_0")
        else:
            optimal_weights = np.zeros(n)
            print(f"using zeroweights")

    return optimal_weights


def risk_budget_objective(x, pars):
    covar, budget = pars[0], pars[1]
    asset_rc = calculate_risk_contribution(x, covar)
    sig_p = np.sqrt(calculate_portfolio_var(x, covar))
    if budget is not None:
        risk_target = np.where(np.isnan(budget), asset_rc, np.multiply(sig_p, budget))  # budget can be nan f
    else:
        risk_target = np.multiply(sig_p, np.ones_like(asset_rc) / asset_rc.shape[0])
    sse = np.nansum(np.square(asset_rc - risk_target))
    return sse


def solve_risk_parity_constr_vol(covar: np.ndarray,
                                 target_vol: float = None,
                                 disp: bool = False,
                                 print_log: bool = False
                                 ) -> np.ndarray:
    n = covar.shape[0]
    budget = np.ones(n) / n
    x0 = budget

    cons = [{'type': 'ineq', 'fun': long_only_constraint},
            {'type': 'eq', 'fun': total_weight_constraint},
            {'type': 'ineq', 'fun': portfolio_volatility_min, 'args': (covar, target_vol)},
            {'type': 'ineq', 'fun': portfolio_volatility_max, 'args': (covar, target_vol)}]

    res = minimize(risk_budget_objective_mod, x0, args=[covar, budget], method='SLSQP', constraints=cons,
                   options={'disp': disp, 'ftol': 1e-14})

    w_rb = res.x

    if print_log:
        print(f'(CON) sigma_p = {np.sqrt(calculate_portfolio_var(w_rb, covar))}, weights: {w_rb}, '
              f'risk contrib.s: {calculate_risk_contribution(w_rb, covar).T} '
              f'sum of weights: {sum(w_rb)}')
    return w_rb


def sum_of_log_weight_constraint(x, risk_budget):
    return np.log(x).dot(risk_budget)


def portfolio_volatility_min(x, covar, target_vol, freq_vol, af: float = 12.0):
    vol_dt = np.sqrt(af)
    return vol_dt * np.sqrt(calculate_portfolio_var(x, covar)) - (target_vol - 0.001)


def portfolio_volatility_max(x, covar, target_vol, freq_vol, af: float = 12.0):
    vol_dt = np.sqrt(af)
    return - (vol_dt * np.sqrt(calculate_portfolio_var(x, covar)) - (target_vol + 0.001))


def risk_budget_objective_mod(x, pars):
    covar, budget = pars[0], pars[1]
    sig_p = np.sqrt(calculate_portfolio_var(x, covar))  # portfolio sigma
    risk_target = np.asmatrix(np.multiply(sig_p, budget))
    asset_RC = calculate_risk_contribution(x, covar)
    sse = np.sum(np.square(asset_RC[:-1] - risk_target.T[:-1] - np.mean(asset_RC[:-1] - risk_target.T[:-1])))
    return sse


def solve_risk_parity_alt(covar: np.ndarray,
                          budget: np.ndarray = None,
                          disp: bool = False,
                          print_log: bool = False
                          ) -> np.ndarray:
    """
    alternative risk parity
    """
    n = covar.shape[0]
    if budget is None:
        budget = np.ones(n) / n
    x0 = budget
    cons = [{'type': 'ineq', 'fun': long_only_constraint},
            {'type': 'eq', 'fun': total_weight_constraint},
            {'type': 'ineq', 'fun': sum_of_log_weight_constraint, 'args': (budget,)}]

    res = minimize(calculate_portfolio_var, x0, args=covar, method='SLSQP', constraints=cons,
                   options={'disp': disp, 'ftol': 1e-14})

    w_rb = res.x

    if print_log:
        print(f'(ALT) sigma_p = {np.sqrt(calculate_portfolio_var(w_rb, covar))}, weights: {w_rb}, '
              f'risk contrib.s: {calculate_risk_contribution(w_rb, covar).T} '
              f'sum of weights: {sum(w_rb)}')
    return w_rb


class UnitTests(Enum):
    RISK_PARITY = 1


def run_unit_test(unit_test: UnitTests):

    if unit_test == UnitTests.RISK_PARITY:
        risk_budget = np.array([0.50, 0.0, 0.5])
        covar = np.array([[0.2 ** 2, 0.5*0.15*0.2, 0.0],
                          [0.5*0.15*0.2, 0.15 ** 2, 0.0],
                          [0.0, 0.0, 0.1**2]])
        covar = np.array([[0.2 ** 2, 0.5*0.15*0.2, -0.01],
                          [0.5*0.15*0.2, 0.15 ** 2, -0.005],
                          [-0.01, -0.005, 0.1**2]])
        print('covar')
        print(covar)
        vol = np.sqrt(np.diag(covar))
        norm = np.outer(1.0 / vol, 1.0 / vol)
        print('corr')
        print(covar*norm)

        w_rb = opt_equal_risk_contribution(covar=covar,
                                           constraints=Constraints(is_long_only=True),
                                           risk_budget=risk_budget)

        print(f"risk_budget={risk_budget}")
        print(f"weights={w_rb}")
        asset_rc = calculate_risk_contribution(w_rb, covar)
        print(f"asset_rc={asset_rc/np.nansum(asset_rc)}")

        """
        w_rb1 = solve_risk_parity_alt(covar=covar)
        print(f"w_rb1={w_rb1}")
        asset_rc = calculate_risk_contribution(w_rb1, covar, risk_budget=risk_budget)
        print(f"asset_rc={asset_rc}")
        """

if __name__ == '__main__':

    unit_test = UnitTests.RISK_PARITY

    is_run_all_tests = False
    if is_run_all_tests:
        for unit_test in UnitTests:
            run_unit_test(unit_test=unit_test)
    else:
        run_unit_test(unit_test=unit_test)