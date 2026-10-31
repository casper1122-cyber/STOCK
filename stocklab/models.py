from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FIELDS = {"close", "open", "volume", "sma5", "sma10", "sma20", "sma60", "sma120", "rsi14", "volume_ratio20", "return1", "return5", "volatility20"}


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    op: Literal["gt", "ge", "lt", "le", "cross_above", "cross_below"]
    value: float | str

    @model_validator(mode="after")
    def valid_fields(self):
        import math
        if self.field not in FIELDS or (isinstance(self.value, str) and self.value not in FIELDS):
            raise ValueError("不支援的指標欄位")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("條件數值必須是有限數字")
        return self


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry: list[Rule] = Field(default_factory=lambda: [Rule(field="sma20", op="gt", value="sma60")], min_length=1, max_length=12)
    exit: list[Rule] = Field(default_factory=lambda: [Rule(field="sma20", op="lt", value="sma60")], max_length=12)
    entry_mode: Literal["all", "any"] = "all"
    exit_mode: Literal["all", "any"] = "any"
    max_hold: int | None = Field(default=None, ge=1, le=2520)
    stop_loss: float | None = Field(default=None, gt=0, lt=1)


class Costs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_cash: float = Field(default=1_000_000, ge=1000, le=1e12)
    commission: float = Field(default=0.001425, ge=0, le=0.05)
    min_commission: float = Field(default=20, ge=0, le=10000)
    sell_tax: float | None = Field(default=None, ge=0, le=0.05)
    slippage: float = Field(default=0.0005, ge=0, le=0.05)
    lot_size: int = Field(default=1, ge=1, le=1000)


class WalkForwardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    train_bars: int = Field(default=252, ge=126, le=2520)
    validation_bars: int = Field(default=63, ge=30, le=504)
    test_bars: int = Field(default=63, ge=30, le=504)
    max_folds: int = Field(default=60, ge=2, le=100)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["backtest", "research", "correlation", "daily", "walkforward"]
    dataset_ids: list[int] = Field(min_length=1, max_length=100)
    strategy: Strategy = Field(default_factory=Strategy)
    costs: Costs = Field(default_factory=Costs)
    action_ids: dict[int, int] = Field(default_factory=dict)
    walk_forward: WalkForwardConfig = Field(default_factory=WalkForwardConfig)
    audit_ids: dict[int, int] = Field(default_factory=dict)
    require_audit: bool = False

    @model_validator(mode="after")
    def actions_match_selection(self):
        if not set(self.action_ids).issubset(self.dataset_ids):
            raise ValueError("公司行動只能對應已選取的資料")
        if self.kind in ("daily", "correlation") and self.action_ids:
            raise ValueError("每日更新及關聯工作尚不支援公司行動版本；請使用指定版本回測或滾動驗證")
        if not set(self.audit_ids).issubset(self.dataset_ids):
            raise ValueError("資料稽核只能對應已選取的資料")
        if self.kind in ("daily","correlation") and (self.audit_ids or self.require_audit):
            raise ValueError("每日更新及關聯工作尚不支援此稽核綁定；更新價格後需重新檢查")
        return self


class ActionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    ex_date: date
    kind: Literal["dividend", "split"]
    announced_date: date
    cash_per_share: float = Field(default=0, ge=0, le=1e7)
    pay_date: date | None = None
    split_ratio: float = Field(default=1, gt=0, le=1000)

    @model_validator(mode="after")
    def consistent(self):
        if self.announced_date > self.ex_date:
            raise ValueError("公告日不得晚於除權息／拆股日，避免使用事後才知道的事件")
        if self.kind == "dividend":
            if self.cash_per_share <= 0 or self.pay_date is None or self.pay_date < self.ex_date or self.split_ratio != 1:
                raise ValueError("現金股利需正數金額、有效付款日，且不得混入拆股比例")
        elif self.cash_per_share or self.pay_date is not None or self.split_ratio == 1:
            raise ValueError("拆股需非 1 比例，不得混入現金股利或付款日")
        return self


class ActionsImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: int = Field(gt=0)
    source: str = Field(min_length=1, max_length=200)
    csv_text: str = Field(max_length=2_000_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    dataset_ids: list[int] = Field(default_factory=list, max_length=20)
    action_ids: dict[int, int] = Field(default_factory=dict)
    costs: Costs = Field(default_factory=Costs)
    walk_forward: WalkForwardConfig = Field(default_factory=WalkForwardConfig)
    audit_ids: dict[int, int] = Field(default_factory=dict)
    require_audit: bool = False


class CalendarImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["TWSE","TPEX","TAIFEX"]
    source: str = Field(min_length=1,max_length=200)
    csv_text: str = Field(max_length=3_000_000)


class AuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: int = Field(gt=0)
    calendar_id: int | None = Field(default=None,gt=0)
    action_id: int | None = Field(default=None,gt=0)


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(pattern=r"^[A-Za-z0-9_^.=-]{1,30}$")
    name: str = Field(default="", max_length=80)
    asset_type: Literal["stock", "etf", "index", "future"] = "stock"
    price_basis: Literal["raw", "adjusted"] = "raw"
    csv_text: str = Field(max_length=8_000_000)


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(pattern=r"^[A-Za-z0-9]{1,12}$")
    name: str = Field(default="", max_length=80)
    asset_type: Literal["stock", "etf"] = "stock"
    start: str = "2018-01-01"
    end: str | None = None


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    hour: int = Field(default=18, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    dataset_ids: list[int] = Field(default_factory=list, max_length=100)
    costs: Costs = Field(default_factory=Costs)
    backup: bool = True

    @model_validator(mode="after")
    def nonempty_enabled(self):
        if self.enabled and not self.dataset_ids:
            raise ValueError("啟用排程前請選擇資料")
        return self
