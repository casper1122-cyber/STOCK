import numpy as np
import pytest

from stocklab.engine import backtest, correlations, features, research
from stocklab.models import Costs, Rule, Strategy
from tests.conftest import bars


def always():
    return Strategy(entry=[Rule(field="close", op="gt", value=0)], exit=[])


def free(**kwargs):
    return Costs(initial_cash=1000, commission=0, min_commission=0, sell_tax=0, slippage=0, **kwargs)


def test_next_open_and_exact_cash_ledger(meta):
    f = bars([10, 20, 30])
    r = backtest(f, meta, always(), free())
    trade = r["trades"][0]
    assert trade["signal_date"] == f.iloc[0]["date"]
    assert trade["entry_date"] == f.iloc[1]["date"]
    assert trade["entry_price"] == 20
    assert trade["quantity"] == 50
    assert trade["pnl"] == 500
    assert r["metrics"]["final_equity"] == 1500


def test_fee_tax_slippage_and_buy_hold_use_identical_costs(meta):
    costs = Costs(initial_cash=1000, commission=.01, min_commission=0, sell_tax=.02, slippage=.01)
    r = backtest(bars([10, 10, 10]), meta, always(), costs)
    t = r["trades"][0]
    quantity = int(1000 / (10.1 * 1.01))
    expected = 1000 - quantity * 10.1 * 1.01 + quantity * 9.9 * .97
    assert t["quantity"] == quantity
    assert r["metrics"]["final_equity"] == pytest.approx(expected)
    assert t["pnl"] == pytest.approx(expected - 1000)
    assert t["costs"] == pytest.approx(1000 - expected)
    assert r["metrics"] == r["benchmark"]["metrics"]


def test_minimum_fee_and_lot_budget(meta):
    c = Costs(initial_cash=1000, commission=0, min_commission=20, sell_tax=0, slippage=0, lot_size=10)
    r = backtest(bars([10, 10, 10]), meta, always(), c)
    assert r["trades"][0]["quantity"] == 90
    assert r["metrics"]["final_equity"] == 960


def test_gap_stop_uses_actual_next_open_not_stop_price(meta):
    f = bars([100, 100, 90, 70, 75])
    s = always().model_copy(update={"stop_loss": .05})
    r = backtest(f, meta, s, free())
    t = r["trades"][0]
    assert t["exit_date"] == f.iloc[3]["date"]
    assert t["exit_price"] == 70
    assert t["exit_reason"] == "previous_close_stop"
    assert t["pnl"] == -300


def test_max_hold_and_no_same_open_reentry(meta):
    s = always().model_copy(update={"max_hold": 1})
    r = backtest(bars([10] * 6), meta, s, free())
    assert [t["entry_index"] for t in r["trades"]] == [1, 3, 5]


def test_initial_drawdown_included(meta):
    r = backtest(bars([100, 100, 50]), meta, always(), free())
    assert r["metrics"]["max_drawdown"] == -.5


def test_no_trade_has_no_invented_win_rate(meta):
    s = Strategy(entry=[Rule(field="close", op="lt", value=0)], exit=[])
    r = backtest(bars([10, 11, 12]), meta, s, free())
    assert r["metrics"]["trades"] == 0
    assert r["metrics"]["win_rate"] is None
    assert r["metrics"]["sharpe"] is None


def test_feature_prefix_invariant_to_future_changes():
    f = bars(np.linspace(10, 50, 200))
    changed = f.copy()
    changed.loc[150:, ["open", "high", "low", "close"]] *= 10
    left, right = features(f), features(changed)
    np.testing.assert_allclose(left.iloc[:150].select_dtypes('number'), right.iloc[:150].select_dtypes('number'), equal_nan=True)


def test_wilder_rsi_flat_up_down():
    for data, expected in (([10] * 40, 50), (range(1, 41), 100), (range(40, 0, -1), 0)):
        f = features(bars(data))
        assert f["rsi14"].iloc[:14].isna().all()
        assert f["rsi14"].iloc[14] == expected


@pytest.mark.parametrize("kind", ["future", "index"])
def test_non_cash_instruments_rejected(meta, kind):
    with pytest.raises(ValueError, match="僅供關聯"):
        backtest(bars([10, 11, 12]), {**meta, "asset_type": kind}, always(), free())


def test_volume_rules_require_volume(meta):
    f = bars([10] * 50)
    f["volume"] = 0
    s = Strategy(entry=[Rule(field="volume_ratio20", op="gt", value=1)], exit=[])
    with pytest.raises(ValueError, match="沒有可用成交量"):
        backtest(f, meta, s, free())


def test_research_does_not_choose_using_test_data(meta):
    rng = np.random.default_rng(21)
    f = bars(100 * np.exp(np.cumsum(rng.normal(0, .02, 1000))))
    first = research(f, meta, free())
    altered = f.copy()
    boundary = 120 + int((len(f) - 120) * .8)
    altered.loc[boundary:, ["open", "high", "low", "close"]] *= np.linspace(1, 3, len(f) - boundary)[:, None]
    second = research(altered, meta, free())
    assert first["selected_strategy"] == second["selected_strategy"]
    assert first["shortlist"] == second["shortlist"]
    assert first["test"]["metrics"] != second["test"]["metrics"]
    assert first["split"]["train"][1] < first["split"]["validation"][0] < first["split"]["test"][0]


def test_research_short_history_is_not_silent_success(meta):
    with pytest.raises(ValueError, match="400"):
        research(bars([10] * 200), meta)


def test_correlations_do_not_fill_missing_dates(meta):
    rng = np.random.default_rng(1)
    f = bars(np.exp(np.cumsum(rng.normal(0, .01, 100))))
    g = f.drop(index=[30, 50]).reset_index(drop=True)
    result = correlations([(meta, f), ({**meta, "id": 2}, g)])
    zero = result["pairs"][0]["lags"][5]
    assert zero["correlation"] == pytest.approx(1)
    assert zero["observations"] == 95  # 99 returns minus 2 missing days and 2 unequal intervals


def test_constant_price_does_not_invent_correlation(meta):
    f = bars([10] * 100)
    r = correlations([(meta, f), ({**meta, 'id':2}, f)])
    assert all(lag['correlation'] is None for lag in r['pairs'][0]['lags'])
