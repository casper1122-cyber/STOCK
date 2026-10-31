import numpy as np
import pandas as pd
import pytest

from stocklab import storage


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKLAB_HOME", str(tmp_path / "lab"))
    monkeypatch.setenv("STOCKLAB_NO_WORKER", "1")
    monkeypatch.delenv("STOCKLAB_API_TOKEN", raising=False)
    storage.init()


@pytest.fixture
def meta():
    return {"id": 1, "symbol": "TEST", "name": "test", "asset_type": "stock", "price_basis": "raw", "source": "TEST"}


def bars(prices):
    prices = np.array(prices, dtype=float)
    return pd.DataFrame({"date": pd.bdate_range("2020-01-01", periods=len(prices)).strftime("%Y-%m-%d"),
                         "open": prices, "high": prices, "low": prices, "close": prices,
                         "volume": np.full(len(prices), 10000.)})
