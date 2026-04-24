import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Optional
import os
# ==========================================
# 1. CONFIGURATION & MAPPINGS
# ==========================================
@dataclass
class AppConfig:
    """Centralized configuration for mappings and constants."""
    
    # Mapping specific locations to broader regions for price matching
    LOCATION_TO_REGION: Dict[str, str] = None
    
    # Mapping seasons to specific months for data explosion
    SEASON_TO_MONTHS: Dict[str, List[str]] = None
    
    def __post_init__(self):
        if self.LOCATION_TO_REGION is None:
            self.LOCATION_TO_REGION = {
                'Amman': 'Up-Land', 'Balqa': 'Up-Land', 'Zarqa': 'Up-Land', 
                'Madaba': 'Up-Land', 'Irbid': 'Up-Land', 'Mafraq': 'Up-Land', 
                'Jarash': 'Up-Land', 'Ajloun': 'Up-Land', 'Karak': 'Up-Land', 
                'Tafiela': 'Up-Land', "Ma'an": 'Up-Land', 'Aqaba': 'Al-Aghouar',
                'Barn North': 'Al-Aghouar', 'Deir Alla': 'Al-Aghouar', 
                'Barn South': 'Al-Aghouar', 'Ghor Safi': 'Al-Aghouar'
            }
        
        if self.SEASON_TO_MONTHS is None:
            self.SEASON_TO_MONTHS = {
                'Summer': ['May', 'June', 'July', 'August', 'September', 'October'],
                'Winter': ['November', 'December', 'January', 'February', 'March', 'April']
            }

# ==========================================
# 2. DATA INGESTOR (The Loader)
# ==========================================
class GovernmentDataIngestor:
    """Responsible for reading raw, messy files and returning clean base DataFrames."""
    
    def __init__(self, delimiter=';', skip_rows=2):
        self.delimiter = delimiter
        self.skip_rows = skip_rows

    def load_production_data(self, file_path: str) -> pd.DataFrame:
        """Loads and pivots the Area/Production CSV."""
        try:
            df = pd.read_csv(file_path, delimiter=self.delimiter, skiprows=self.skip_rows)
            # Rename raw columns
            df.columns = ['Metric', 'Season', 'Crop', 'Location', 'Value_2024']
            
            # Pivot to get Area and Production side-by-side
            df_pivoted = df.pivot_table(
                index=['Season', 'Crop', 'Location'], 
                columns='Metric', 
                values='Value_2024', 
                aggfunc='first'
            ).reset_index()
            
            # Clean column names
            df_pivoted.rename(columns={'Area': 'area_size', 'Production': 'total_yield'}, inplace=True)
            return df_pivoted
            
        except Exception as e:
            raise ValueError(f"Error loading production data: {e}")

    def load_price_data(self, file_path: str) -> pd.DataFrame:
        """Loads and filters the Price CSV."""
        try:
            df = pd.read_csv(file_path, delimiter=self.delimiter, skiprows=self.skip_rows)
            df.columns = ['Metric', 'Region_Level', 'Crop', 'Month', 'Price_2024']
            
            # We only care about Farm-Gate Price for this dataset
            return df[df['Metric'] == 'Farm-Gate Price'].copy()
            
        except Exception as e:
            raise ValueError(f"Error loading price data: {e}")

# ==========================================
# 3. DATA PROCESSOR (The Brains)
# ==========================================
class Mazra3tiProcessor:
    """Handles logic: Exploding seasons to months, merging datasets, calculations."""
    
    def __init__(self, config: AppConfig):
        self.config = config

    def process(self, df_prod: pd.DataFrame, df_prices: pd.DataFrame) -> pd.DataFrame:
        
        # 1. Explode Seasons into Months
        df_prod['Month'] = df_prod['Season'].map(self.config.SEASON_TO_MONTHS)
        df_exploded = df_prod.explode('Month')
        
        # 2. Distribute totals (Yield / 6 months)
        df_exploded['monthly_yield_est'] = df_exploded['total_yield'] / 6
        
        # 3. Map Locations to Price Regions
        df_exploded['Price_Region'] = df_exploded['Location'].map(
            self.config.LOCATION_TO_REGION
        ).fillna('Kingdom')
        
        # 4. Merge with Prices
        # Merge strategy: Try to match specific Region first
        df_merged = pd.merge(
            df_exploded,
            df_prices[['Region_Level', 'Crop', 'Month', 'Price_2024']],
            left_on=['Price_Region', 'Crop', 'Month'],
            right_on=['Region_Level', 'Crop', 'Month'],
            how='left'
        )
        
        # 5. Fallback Merge (Kingdom Average) for missing prices
        kingdom_prices = df_prices[df_prices['Region_Level'] == 'Kingdom']
        df_merged = pd.merge(
            df_merged,
            kingdom_prices[['Crop', 'Month', 'Price_2024']],
            on=['Crop', 'Month'],
            how='left',
            suffixes=('', '_kingdom')
        )
        
        # Coalesce prices (Use region price, if null use kingdom price)
        df_merged['final_price'] = df_merged['Price_2024'].fillna(df_merged['Price_2024_kingdom'])
        
        return df_merged

# ==========================================
# 4. SCHEMA ENFORCER (The Output Formatter)
# ==========================================
class SchemaEnforcer:
    """Ensures the final dataframe matches the Mazra3ti Doc structure."""
    
    TARGET_COLUMNS = [
        'record_id', 'crop_name', 'season', 'month', 'planting_date', 'harvest_date',
        'farm_location', 'market_location', 'region_type',
        'area_size', 'quantity_sold', 'unit_price', 'total_sales',
        'expense_type', 'expense_amount', 'total_expenses', 'profit',
        'fertilizer_used', 'water_used', 'actual_yield', 'expected_yield',
        'demand_level', 'customer_type', 'market_price'
    ]

    def format_output(self, raw_merged_df: pd.DataFrame) -> pd.DataFrame:
        df_out = pd.DataFrame()
        
        # --- Direct Mappings ---
        df_out['crop_name'] = raw_merged_df['Crop']
        df_out['season'] = raw_merged_df['Season']
        df_out['month'] = raw_merged_df['Month']
        df_out['farm_location'] = raw_merged_df['Location']
        df_out['market_location'] = raw_merged_df['Location'] # Assumption
        df_out['region_type'] = raw_merged_df['Price_Region']
        df_out['area_size'] = raw_merged_df['area_size']
        df_out['actual_yield'] = raw_merged_df['total_yield'] # Total Season Yield
        
        # --- Unit Conversions (Assumptions: Tons -> Kg, Fils -> JOD) ---
        # Prod data usually Tons, Price usually Fils
        df_out['quantity_sold'] = raw_merged_df['monthly_yield_est'] * 1000 
        df_out['unit_price'] = raw_merged_df['final_price'] / 1000 
        
        # --- Calculations ---
        df_out['total_sales'] = df_out['quantity_sold'] * df_out['unit_price']
        df_out['market_price'] = df_out['unit_price'] * 1.15 # Simulate markup
        
        # --- Filling Missing Schema Columns with NaN ---
        for col in self.TARGET_COLUMNS:
            if col not in df_out.columns:
                df_out[col] = np.nan
                
        # --- Final ID Generation ---
        df_out['record_id'] = range(1, len(df_out) + 1)
        
        # Reorder columns to match target list
        return df_out[self.TARGET_COLUMNS]

# ==========================================
# 5. ORCHESTRATOR (The Main Loop)
# ==========================================
class ETLPipeline:
    def __init__(self, prod_file: str, price_file: str, output_file: str):
        self.prod_file = prod_file
        self.price_file = price_file
        self.output_file = output_file
        self.config = AppConfig()
        self.ingestor = GovernmentDataIngestor()
        self.processor = Mazra3tiProcessor(self.config)
        self.schema = SchemaEnforcer()

    def run(self):
        print("1. Ingesting Data...")
        df_prod = self.ingestor.load_production_data(self.prod_file)
        df_price = self.ingestor.load_price_data(self.price_file)
        
        print("2. Processing & Merging...")
        df_merged = self.processor.process(df_prod, df_price)
        
        print("3. Formatting to Schema...")
        final_df = self.schema.format_output(df_merged)
        
        if self.output_file:
            final_df.to_csv(self.output_file, index=False)
            print(f"4. Done! Saved to {self.output_file}")
        
        return final_df

# ==========================================
# USAGE
# ==========================================
if __name__ == "__main__":
    # Replace these with your actual file paths
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    PROCESSED_DIR = os.path.join(BASE_DIR, 'data', 'processed')
    pipeline = ETLPipeline(
        prod_file=r'D:\Codes & Programs\mazra3ati\data\raw\AGR_AREAPRO (1).csv', 
        price_file=r'D:\Codes & Programs\mazra3ati\data\raw\AGR_PRICE1 (1).csv',
        output_file=PROCESSED_DIR+"/cleaned_data.csv"
    )
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    clean_data = pipeline.run()
    print(clean_data.head())