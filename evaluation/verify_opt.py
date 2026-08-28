"""Verification of optimization correctness."""

import sys
from pathlib import Path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import pandas as pd
from backend.tools.analytics import (
    load_transaction_data,
    get_revenue_trend,
    get_conversion_by_device,
    get_category_performance,
)

df = load_transaction_data()

# 1. Test get_revenue_trend W and M
trend_w = get_revenue_trend(df, frequency="W")
trend_m = get_revenue_trend(df, frequency="M")
print(f"Weekly Trend entries: {len(trend_w)}")
print(f"Weekly sample: {trend_w[0]}")
print(f"Monthly Trend entries: {len(trend_m)}")
print(f"Monthly sample: {trend_m[0]}")

# 2. Test get_conversion_by_device
devices = get_conversion_by_device(df)
print(f"Devices: {list(devices.keys())}")
print(f"Sample device: {devices['Desktop']}")

# 3. Test get_category_performance
categories = get_category_performance(df)
print(f"Categories: {list(categories.keys())}")
print(f"Sample category: {categories['Fashion']}")
