"""Offline tests for Tushare membership frame mapping."""

from __future__ import annotations

from datetime import date

import pandas as pd

from tradingagents.market_data.contracts import MembershipMode
from tradingagents.market_data.providers.tushare import (
    industry_member_query_params,
    map_index_members_frame,
    map_industry_members_frame,
)


def test_map_industry_members_frame():
    frame = pd.DataFrame([
        {
            "l2_code": "801080",
            "ts_code": "600001.SH",
            "in_date": "20250101",
            "out_date": None,
        }
    ])
    rows = map_industry_members_frame(frame, "801080.SI", "tushare")
    assert len(rows) == 1
    assert rows[0].symbol == "600001"
    assert rows[0].membership_mode == MembershipMode.EFFECTIVE_INTERVAL
    assert rows[0].was_member_on(date(2025, 6, 1))


def test_map_index_members_frame_single_day():
    frame = pd.DataFrame([
        {
            "index_code": "000300.SH",
            "con_code": "600001.SH",
            "trade_date": "20250601",
            "weight": 0.01,
        }
    ])
    rows = map_index_members_frame(frame, "000300.SH", "tushare")
    assert rows[0].was_member_on(date(2025, 6, 1))
    assert not rows[0].was_member_on(date(2025, 6, 2))


def test_industry_member_query_params_use_sw_l2_code():
    params = industry_member_query_params("801080.SI")
    assert params == {"is_sw": "1", "l2_code": "801080"}
