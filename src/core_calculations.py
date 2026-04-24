import pandas as pd
import numpy as np

class CoreCalculations:
    def __init__(self, data_path=None, config=None):
        """
        config: dict for flexible behavior (future-proofing)
        """
        self.data_path = data_path
        self.config = config or {}
        self.df = None

    def load_sample(self, n_rows=100_000):
        if not self.data_path:
            raise ValueError("No data_path provided")

        self.df = pd.read_csv(self.data_path, nrows=n_rows)
        return self.df

    def set_dataframe(self, df):
        self.df = df.copy()

    def get_dataframe(self):
        return self.df.copy()

    def _validate_columns(self, required_cols):
        missing = [col for col in required_cols if col not in self.df.columns]
        if missing:
            raise ValueError(f"Missing columns: {missing}")

    def calculate_revenue(self):
        self._validate_columns(["quantity_sold", "unit_price"])

        self.df["computed_revenue"] = self.df["quantity_sold"] * self.df["unit_price"]

        if "total_sales" in self.df.columns:
            self.df["revenue"] = self.df["total_sales"].fillna(self.df["computed_revenue"])
        else:
            self.df["revenue"] = self.df["computed_revenue"]

    def calculate_costs(self):
        self._validate_columns(["total_expenses"])
        self.df["cost"] = self.df["total_expenses"]

        # Optional: include transport cost if configured
        if self.config.get("include_transport", True) and "transport_cost" in self.df.columns:
            self.df["cost"] += self.df["transport_cost"]

    def calculate_profit(self):
        self._validate_columns(["revenue", "cost"])
        self.df["profit_calc"] = self.df["revenue"] - self.df["cost"]

        if "profit" in self.df.columns:
            self.df["profit_diff"] = self.df["profit"] - self.df["profit_calc"]

    def calculate_profit_margin(self):
        self._validate_columns(["profit_calc", "revenue"])
        self.df["profit_margin_pct"] = (
            self.df["profit_calc"] / self.df["revenue"].replace(0, pd.NA)
        ) * 100

    def calculate_efficiency(self):
        if "water_used" in self.df.columns:
            self._validate_columns(["actual_yield"])
            self.df["water_efficiency"] = (
                self.df["actual_yield"] / self.df["water_used"].replace(0, pd.NA)
            )

        if "fertilizer_used" in self.df.columns:
            self._validate_columns(["actual_yield"])
            self.df["fertilizer_efficiency"] = (
                self.df["actual_yield"] / self.df["fertilizer_used"].replace(0, pd.NA)
            )

    def run_pipeline(self):
        """
        Executes standard calculations sequentially instead of using method chaining.
        """
        if self.df is None:
            raise ValueError("No dataframe loaded. Call load_sample() or set_dataframe() first.")
            
        self.calculate_revenue()
        self.calculate_costs()
        self.calculate_profit()
        self.calculate_profit_margin()
        self.calculate_efficiency()
        
        return self.df

    def summary_by_crop(self):
        self._validate_columns(["crop_name", "revenue", "cost", "profit_calc", "quantity_sold"])
        
        grouped = self.df.groupby("crop_name").agg({
            "revenue": "sum",
            "cost": "sum",
            "profit_calc": "sum",
            "quantity_sold": "sum"
        }).reset_index()

        grouped["profit_margin_pct"] = (
            grouped["profit_calc"] / grouped["revenue"].replace(0, pd.NA)
        ) * 100

        return grouped