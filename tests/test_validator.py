import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) # Add project root
import types
import pandas as pd
import numpy as np
from data_validator import DataValidator

# Create bad data
dates = pd.date_range("2023-01-01", periods=100)
bad_data = pd.DataFrame({
    'Open': [100]*100,
    'High': [105]*100,
    'Low': [95]*100,
    'Close': [100]*100,
    'Volume': [1000000]*100
}, index=dates)

# Insert Spike
bad_data.loc[dates[50], 'Close'] = 1000 # 10x spike

print("Testing DataValidator with bad data (Spike)...")
res = DataValidator.validate_history(bad_data, "BAD")
print(f"Valid: {res.is_valid}")
print(f"Warnings: {res.warnings}")

# Insert Negative Price
bad_data.loc[dates[51], 'Close'] = -50
print("\nTesting DataValidator with bad data (Negative Price)...")
res2 = DataValidator.validate_history(bad_data, "NEG")
print(f"Valid: {res2.is_valid}")
print(f"Errors: {res2.errors}")
