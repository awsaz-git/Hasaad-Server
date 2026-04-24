import pytest
import pandas as pd
import numpy as np
# Assuming your class is in a file called core_calculations.py
from .core_calculations import CoreCalculations 

@pytest.fixture
def sample_df():
    """Provides a standard dataframe for testing."""
    return pd.DataFrame({
        "crop_name": ["Wheat", "Corn", "Wheat"],
        "quantity_sold": [100, 200, 150],
        "unit_price": [5.0, 4.0, 5.0],
        "total_expenses": [300.0, 500.0, 400.0],
        "transport_cost": [50.0, 100.0, 50.0],
        "actual_yield": [1000, 2500, 1600],
        "water_used": [500, 1000, 800]
    })

@pytest.fixture
def calc_instance(sample_df):
    """Provides an initialized CoreCalculations instance with data."""
    calc = CoreCalculations(config={"include_transport": True})
    calc.set_dataframe(sample_df)
    return calc

def test_validate_columns_raises_error(calc_instance):
    """Ensures a ValueError is raised if required columns are missing."""
    with pytest.raises(ValueError, match="Missing columns"):
        calc_instance._validate_columns(["non_existent_column"])

def test_calculate_revenue(calc_instance):
    """Tests if revenue is computed correctly (qty * price)."""
    calc_instance.calculate_revenue()
    df = calc_instance.get_dataframe()
    
    expected_revenue = [500.0, 800.0, 750.0]
    assert df["revenue"].tolist() == expected_revenue

def test_calculate_costs_with_transport(calc_instance):
    """Tests if costs include transport when config allows it."""
    calc_instance.calculate_costs()
    df = calc_instance.get_dataframe()
    
    # 300 + 50, 500 + 100, 400 + 50
    expected_costs = [350.0, 600.0, 450.0] 
    assert df["cost"].tolist() == expected_costs

def test_calculate_costs_without_transport(sample_df):
    """Tests if transport costs are ignored when config is False."""
    calc = CoreCalculations(config={"include_transport": False})
    calc.set_dataframe(sample_df)
    calc.calculate_costs()
    df = calc.get_dataframe()
    
    # Should just equal total_expenses
    expected_costs = [300.0, 500.0, 400.0]
    assert df["cost"].tolist() == expected_costs

def test_profit_and_margin(calc_instance):
    """Tests profit and margin percentages."""
    calc_instance.calculate_revenue()
    calc_instance.calculate_costs()
    calc_instance.calculate_profit()
    calc_instance.calculate_profit_margin()
    
    df = calc_instance.get_dataframe()
    
    # Row 0: Rev 500, Cost 350 -> Profit 150 -> Margin (150/500)*100 = 30.0%
    assert df["profit_calc"].iloc[0] == 150.0
    assert df["profit_margin_pct"].iloc[0] == 30.0

def test_run_pipeline(calc_instance):
    """Ensures the pipeline runs sequentially without throwing errors."""
    df_result = calc_instance.run_pipeline()
    
    # Check that all expected columns were created
    expected_new_cols = [
        "revenue", "cost", "profit_calc", 
        "profit_margin_pct", "water_efficiency"
    ]
    for col in expected_new_cols:
        assert col in df_result.columns

def test_summary_by_crop(calc_instance):
    """Tests the aggregation logic."""
    calc_instance.run_pipeline()
    summary = calc_instance.summary_by_crop()
    
    # Check that Wheat rows (0 and 2) were summed correctly
    wheat_summary = summary[summary["crop_name"] == "Wheat"].iloc[0]
    
    # Rev: 500 + 750 = 1250, Cost: 350 + 450 = 800, Profit: 150 + 300 = 450
    assert wheat_summary["revenue"] == 1250.0
    assert wheat_summary["profit_calc"] == 450.0
    assert wheat_summary["quantity_sold"] == 250