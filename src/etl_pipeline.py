import pandas as pd
import numpy as np
import os
from dataclasses import dataclass
from typing import Dict, List

# ==========================================
# 1. CONFIGURATION
# ==========================================
@dataclass
class AppConfig:
    LOCATION_TO_REGION: Dict[str, str] = None

    def __post_init__(self):
        if self.LOCATION_TO_REGION is None:
            self.LOCATION_TO_REGION = {
                'amman': 'up-land', 'balqa': 'up-land', 'zarqa': 'up-land',
                'madaba': 'up-land', 'irbid': 'up-land', 'mafraq': 'up-land',
                'jarash': 'up-land', 'ajloun': 'up-land', 'karak': 'up-land',
                'tafiela': 'up-land', "ma'an": 'up-land',
                'aqaba': 'al-aghouar',
                'barn north': 'al-aghouar',
                'deir alla': 'al-aghouar',
                'barn south': 'al-aghouar',
                'ghor safi': 'al-aghouar'
            }

# ==========================================
# 2. INGESTOR (SMART FIX)
# ==========================================
class GovernmentDataIngestor:

    def __init__(self, delimiter=';'):
        self.delimiter = delimiter

    def _find_start_row(self, file_path):
        """
        Scans the file to find the first line that looks like data 
        (contains at least 8 delimiters).
        """
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                for i, line in enumerate(f):
                    if line.count(self.delimiter) >= 8:
                        return i
        except UnicodeDecodeError:
            # Fallback for latin-1 if utf-8 fails
            with open(file_path, 'r', encoding='latin-1') as f:
                for i, line in enumerate(f):
                    if line.count(self.delimiter) >= 8:
                        return i
        return 0  # Default to 0 if not found (will likely fail later, but we tried)

    def load_production_data(self, file_path: str) -> pd.DataFrame:
        print(f"   ...Reading Production: {file_path}")
        
        # 1. Auto-detect where the data starts
        start_row = self._find_start_row(file_path)
        print(f"      -> Detected data starting at row {start_row}")

        # 2. Read CSV skipping the bad lines
        try:
            df = pd.read_csv(
                file_path, 
                delimiter=self.delimiter, 
                header=None, 
                skiprows=start_row,
                encoding='utf-8-sig'
            )
        except UnicodeDecodeError:
            df = pd.read_csv(
                file_path, 
                delimiter=self.delimiter, 
                header=None, 
                skiprows=start_row,
                encoding='latin-1'
            )

        # Remove potential empty 10th column
        if df.shape[1] > 9:
            df = df.iloc[:, :9]

        if df.shape[1] != 9:
            raise ValueError(f"Expected 9 columns, found {df.shape[1]}. Check file format.")

        df.columns = [
            'kind', 'season', 'crop', 'location',
            '2020', '2021', '2022', '2023', '2024'
        ]

        # Clean strings
        df['kind'] = df['kind'].astype(str).str.strip().str.lower()
        df['season'] = df['season'].astype(str).str.strip().str.lower()
        df['crop'] = df['crop'].astype(str).str.strip().str.lower()
        df['location'] = df['location'].astype(str).str.strip().str.lower()

        # Robust mapping
        def clean_kind_name(val):
            if 'area' in val: return 'area'
            if 'production' in val: return 'production'
            return val
        
        df['kind'] = df['kind'].apply(clean_kind_name)

        # Melt
        df_long = df.melt(
            id_vars=['kind', 'season', 'crop', 'location'],
            var_name='year',
            value_name='value'
        )

        # Clean numeric
        df_long['value'] = pd.to_numeric(df_long['value'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        df_long['year'] = df_long['year'].astype(int)

        # Pivot
        df_final = df_long.pivot_table(
            index=['crop', 'location', 'season', 'year'],
            columns='kind',
            values='value',
            aggfunc='sum'
        ).reset_index()

        df_final = df_final.rename(columns={
            'area': 'area_size',
            'production': 'total_yield'
        })

        # Validation
        required = ['area_size', 'total_yield']
        missing = [c for c in required if c not in df_final.columns]
        if missing:
            raise ValueError(f"Pivot failed. Missing columns: {missing}. Found kinds: {df['kind'].unique()}")

        return df_final


    def load_price_data(self, file_path: str) -> pd.DataFrame:
        print(f"   ...Reading Prices: {file_path}")

        # 1. Auto-detect start row for Price file too
        start_row = self._find_start_row(file_path)
        print(f"      -> Detected data starting at row {start_row}")

        try:
            df = pd.read_csv(
                file_path, 
                delimiter=self.delimiter, 
                header=None, 
                skiprows=start_row,
                encoding='utf-8-sig'
            )
        except UnicodeDecodeError:
            df = pd.read_csv(
                file_path, 
                delimiter=self.delimiter, 
                header=None, 
                skiprows=start_row,
                encoding='latin-1'
            )

        if df.shape[1] > 9:
            df = df.iloc[:, :9]

        df.columns = [
            'metric', 'region_level', 'crop', 'month',
            '2020', '2021', '2022', '2023', '2024'
        ]

        # Filter for Price rows
        df_price = df[df['metric'].astype(str).str.contains('Price', case=False, na=False)].copy()

        if df_price.empty:
            print("⚠️ WARNING: No 'Price' rows found. Using entire dataset (assuming Packs/Quantity is desired?)...")
            df_price = df.copy() # Fallback if you want to see whatever data is there

        df_price['crop'] = df_price['crop'].astype(str).str.strip().str.lower()
        df_price['region_level'] = df_price['region_level'].astype(str).str.strip().str.lower()
        df_price['month'] = df_price['month'].astype(str).str.strip()

        df_long = df_price.melt(
            id_vars=['metric', 'region_level', 'crop', 'month'],
            var_name='year',
            value_name='price'
        )
        
        df_long['price'] = pd.to_numeric(df_long['price'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        df_long['year'] = df_long['year'].astype(int)

        return df_long[['region_level', 'crop', 'month', 'year', 'price']]

# ==========================================
# 3. PROCESSOR
# ==========================================
class Mazra3tiProcessor:

    def __init__(self, config: AppConfig):
        self.config = config

    def process(self, df_prod: pd.DataFrame, df_price: pd.DataFrame) -> pd.DataFrame:

        # Map region
        df_prod['price_region'] = df_prod['location'].map(
            self.config.LOCATION_TO_REGION
        ).fillna('kingdom')

        # Expand to monthly
        months = [
            'January', 'February', 'March', 'April',
            'May', 'June', 'July', 'August',
            'September', 'October', 'November', 'December'
        ]

        # Replicate rows 12 times (once for each month)
        df_prod_expanded = df_prod.loc[df_prod.index.repeat(12)].copy()
        df_prod_expanded['month'] = months * len(df_prod)
        df_prod_expanded['monthly_yield_est'] = df_prod_expanded['total_yield'] / 12

        # Merge region-level price
        # Note: Ensure month names match exactly (Case sensitive usually)
        df_merged = pd.merge(
            df_prod_expanded,
            df_price,
            left_on=['price_region', 'crop', 'month', 'year'],
            right_on=['region_level', 'crop', 'month', 'year'],
            how='left'
        )

        # Fallback to kingdom price
        kingdom_prices = df_price[df_price['region_level'] == 'kingdom']

        df_merged = pd.merge(
            df_merged,
            kingdom_prices[['crop', 'month', 'year', 'price']],
            on=['crop', 'month', 'year'],
            how='left',
            suffixes=('', '_kingdom')
        )

        df_merged['final_price'] = df_merged['price'].fillna(
            df_merged['price_kingdom']
        )
        
        # Fill remaining NaN prices with 0 to avoid errors in calculation
        df_merged['final_price'] = df_merged['final_price'].fillna(0)

        return df_merged

# ==========================================
# 4. SCHEMA FORMATTER
# ==========================================
class SchemaEnforcer:

    def format_output(self, df: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame()

        out['record_id'] = range(1, len(df) + 1)
        out['crop_name'] = df['crop']
        out['year'] = df['year']
        out['month'] = df['month']
        out['farm_location'] = df['location']
        out['region_type'] = df['price_region']
        out['area_size'] = df['area_size']
        out['actual_yield_tons'] = df['total_yield']
        out['quantity_sold_kg'] = df['monthly_yield_est'] * 1000
        out['unit_price_jod'] = df['final_price'] / 1000  # Assuming price is per ton, converting to kg? Adjust logic if price is per kg.
        out['total_sales_jod'] = out['quantity_sold_kg'] * out['unit_price_jod']

        cols_to_clean = [
            'area_size', 'actual_yield_tons', 
            'quantity_sold_kg', 'unit_price_jod', 
            'total_sales_jod'#, 'market_price'
        ]
        
        # Replace strictly 0 and 0.0 with np.nan
        out[cols_to_clean] = out[cols_to_clean].replace({0: np.nan, 0.0: np.nan})

        return out


# ==========================================
# 5. ORCHESTRATOR
# ==========================================
class ETLPipeline:

    def __init__(self, prod_file, price_file, output_file=None):
        self.prod_file = prod_file
        self.price_file = price_file
        self.output_file = output_file

        self.config = AppConfig()
        # Initializing Ingestor with Semicolon delimiter
        self.ingestor = GovernmentDataIngestor(delimiter=';')
        self.processor = Mazra3tiProcessor(self.config)
        self.schema = SchemaEnforcer()

    def run(self):
        print("1️ Loading production data...")
        df_prod = self.ingestor.load_production_data(self.prod_file)

        print("2️ Loading price data...")
        df_price = self.ingestor.load_price_data(self.price_file)

        print("3️ Processing & merging...")
        df_merged = self.processor.process(df_prod, df_price)

        print("4️ Formatting final dataset...")
        final_df = self.schema.format_output(df_merged)

        if self.output_file:
            os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
            final_df.to_csv(self.output_file, index=False)
            print(f"✅ Saved to {self.output_file}")

        return final_df

# ==========================================
# RUN
# ==========================================
if __name__ == "__main__":
    # Update paths to your specific files
    pipeline = ETLPipeline(
        prod_file=r'D:\Codes & Programs\mazra3ati\data\raw\AGR_AREAPRO (2).csv',
        price_file=r'D:\Codes & Programs\mazra3ati\data\raw\AGR_PRICE1 (2).csv',
        output_file=r'D:\Codes & Programs\mazra3ati\data\processed\cleaned_data.csv'
    )

    try:
        df = pipeline.run()
        print("\nTop 5 Rows:")
        print(df.head())
    except Exception as e:
        print(f"\n *Pipeline failed: {e}")