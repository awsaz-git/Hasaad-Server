"""
Hasad API - ML Prediction Service (Refactored)

Features:
- Simplified frontend inputs (farmers provide minimal data)
- Auto-fetches crop data, supply, demand from Supabase
- Auto-fetches weather from Open-Meteo API
- Converts donums to hectares automatically (1 donum = 0.1 hectare)
- Graceful fallbacks to baseline values if DB queries fail
"""

import asyncio
import logging
import os
from dotenv import load_dotenv
load_dotenv()
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

# Hadoop home not needed on Linux/Docker — removed Windows-specific paths

import httpx
import pyspark.sql.functions as F
from fastapi import FastAPI, Form, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from pyspark.ml import PipelineModel
from pyspark.sql import SparkSession
from supabase import create_client

from src.optimizer import CropOptimizer

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------

# Supabase credentials (from .env)
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# Static Mappings: Governorate to Farm Location
GOVERNORATE_TO_FARM_LOCATION = {
    "Aqaba": "Southern",
    "Ma'an": "Southern",
    "Tafilah": "Southern",
    "Karak": "Highlands",
    "Madaba": "Highlands",
    "Amman": "Highlands",
    "Zarqa": "Highlands",
    "Balqa": "Highlands",
    "Jerash": "Highlands",
    "Ajloun": "Highlands",
    "Irbid": "Highlands",
    "Mafraq": "Jordan Valley",
}

# Static Mappings: Governorate to Region Type
GOVERNORATE_TO_REGION_TYPE = {
    "Amman": "Urban",
    "Zarqa": "Urban",
    "Irbid": "Urban",
    "Aqaba": "Urban",
    "Balqa": "Suburban",
    "Madaba": "Suburban",
    "Karak": "Rural",
    "Tafilah": "Rural",
    "Ma'an": "Rural",
    "Mafraq": "Rural",
    "Jerash": "Rural",
    "Ajloun": "Rural",
}

# Governorate Coordinates (for weather API)
GOVERNORATE_COORDS = {
    "Amman": (31.9539, 35.9106),
    "Zarqa": (32.0728, 36.0875),
    "Irbid": (32.5556, 35.8500),
    "Aqaba": (29.5267, 35.0060),
    "Mafraq": (32.3417, 36.2000),
    "Balqa": (32.0362, 35.7308),
    "Madaba": (31.7167, 35.7833),
    "Karak": (31.1833, 35.7000),
    "Tafilah": (30.8333, 35.6000),
    "Ma'an": (30.1967, 35.7344),
    "Jerash": (32.2833, 35.9000),
    "Ajloun": (32.3333, 35.7500),
}

# Market Location Options
MARKET_LOCATION_OPTIONS = ["Amman", "Zarqa", "Irbid", "Aqaba", "Other"]

# Conversion Constants
DONUM_TO_HECTARE = 0.1  # 1 donum = 0.1 hectare

# Demand Mapping (StringIndexer orders by frequency: most frequent = 0)
DEMAND_LABEL_MAP = {0.0: "High", 1.0: "Medium", 2.0: "Low"}

# Initialize optimizer
optimizer_service = CropOptimizer(min_share=0.05, max_share=0.40)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# force=True is critical — Spark's Log4j hijacks Python's root logger on init.
# Without force=True, this basicConfig call is silently ignored after Spark starts,
# causing all logger.info() calls to produce no output in Render logs.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    force=True,
)
logger = logging.getLogger("hasad-api")
# Also set the root logger level explicitly to ensure nothing is filtered
logging.getLogger().setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "template" / "html"
MODELS_DIR = BASE_DIR / "models"

# ---------------------------------------------------------------------------
# PySpark environment (Linux/Docker)
# ---------------------------------------------------------------------------
os.environ["PYSPARK_PYTHON"] = "python"
os.environ["PYSPARK_DRIVER_PYTHON"] = "python"

# ---------------------------------------------------------------------------
# Supabase Client (initialized in lifespan)
# ---------------------------------------------------------------------------
supabase_client = None


# ---------------------------------------------------------------------------
# Helper Functions: Weather & Database
# ---------------------------------------------------------------------------

async def fetch_weather(governorate: str) -> dict:
    """
    Fetch current weather data from Open-Meteo API.
    Returns: {temperature, humidity, rainfall, wind_speed}
    On failure, returns safe defaults and logs warning.
    """
    if governorate not in GOVERNORATE_COORDS:
        logger.warning(f"Governorate '{governorate}' not found in coordinates. Using defaults.")
        return {
            "temperature": 20.0,
            "humidity": 50.0,
            "rainfall": 0.0,
            "wind_speed": 0.0,
        }

    lat, lon = GOVERNORATE_COORDS[governorate]
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,rain,wind_speed_10m",
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            current = data.get("current", {})
            return {
                "temperature": current.get("temperature_2m", 20.0),
                "humidity": current.get("relative_humidity_2m", 50.0),
                "rainfall": current.get("rain", 0.0),
                "wind_speed": current.get("wind_speed_10m", 0.0),
            }
    except Exception as e:
        logger.warning(f"Weather API call failed for {governorate}: {e}. Using defaults.")
        return {
            "temperature": 20.0,
            "humidity": 50.0,
            "rainfall": 0.0,
            "wind_speed": 0.0,
        }


async def get_crop_id_by_name(crop_name: str) -> Optional[int]:
    """Fetch crop ID from Supabase by crop name."""
    try:
        response = supabase_client.table("crops").select("id").eq("name_en", crop_name).execute()
        if response.data:
            return response.data[0]["id"]
    except Exception as e:
        logger.warning(f"Failed to fetch crop ID for '{crop_name}': {e}")
    return None


async def get_crop_avg_yield(crop_name: str) -> float:
    """Fetch crop avg_yield_per_donum from Supabase."""
    try:
        response = supabase_client.table("crops").select("avg_yield_per_donum").eq("name_en", crop_name).execute()
        if response.data:
            return float(response.data[0].get("avg_yield_per_donum", 0.0))
    except Exception as e:
        logger.warning(f"Failed to fetch avg_yield for '{crop_name}': {e}")
    return 0.0


async def get_latest_supply(crop_id: int) -> float:
    """Fetch latest total_estimated_tons from crop_supply."""
    try:
        response = (
            supabase_client.table("crop_supply")
            .select("total_estimated_tons")
            .eq("crop_id", crop_id)
            .order("supply_date", desc=True)
            .limit(1)
            .execute()
        )
        if response.data:
            return float(response.data[0].get("total_estimated_tons", 0.0))
    except Exception as e:
        logger.warning(f"Failed to fetch supply for crop_id {crop_id}: {e}")
    return 0.0


async def get_latest_demand(crop_id: int) -> float:
    """Fetch latest demand_tons from crop_demand."""
    try:
        response = (
            supabase_client.table("crop_demand")
            .select("demand_tons")
            .eq("crop_id", crop_id)
            .order("demand_date", desc=True)
            .limit(1)
            .execute()
        )
        if response.data:
            return float(response.data[0].get("demand_tons", 0.0))
    except Exception as e:
        logger.warning(f"Failed to fetch demand for crop_id {crop_id}: {e}")
    return 0.0


async def get_market_price(crop_name: str, baselines: dict) -> float:
    """
    Fetch market price from baselines CSV data.
    Falls back to safe default if crop not found.
    """
    if crop_name in baselines:
        return baselines[crop_name].get("market_price", 100.0)
    return 100.0  # Safe default


def compute_demand_level_ord(supply: float, demand: float) -> int:
    """
    Compute demand_level_ord (ordinal) from supply/demand ratio.
    Logic:
    - supply > demand * 1.2: High supply = Low demand pressure → 0
    - supply < demand * 0.8: Low supply = High demand pressure → 2
    - else: Balanced → 1
    
    Note: Verify this matches your model's training label frequency order.
    """
    if demand == 0:
        return 0  # Default to high supply
    ratio = supply / demand
    if ratio > 1.2:
        return 0  # High supply / Low demand
    elif ratio < 0.8:
        return 2  # Low supply / High demand
    else:
        return 1  # Balanced


# ---------------------------------------------------------------------------
# Lifespan — initialize Supabase, load ML models, compute baselines
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global supabase_client
    
    logger.info("Initializing Supabase client...")
    try:
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Supabase client initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize Supabase: {e}")
        raise

    logger.info("Starting Spark session...")
    try:
        from pyspark import SparkConf
        # SparkConf is used instead of chained .config() calls because
        # spark.authenticate must be set before the JVM initializes —
        # builder-chained configs are applied too late in PySpark 3.5,
        # causing the AccumulatorServer to still enforce token auth.
        conf = SparkConf()
        conf.setAppName("hasad-api")
        conf.setMaster("local[2]")
        conf.set("spark.authenticate", "false")
        conf.set("spark.io.encryption.enabled", "false")
        conf.set("spark.ui.enabled", "false")
        conf.set("spark.ui.showConsoleProgress", "false")
        conf.set("spark.driver.memory", "1g")
        conf.set("spark.executor.memory", "1g")
        conf.set("spark.driver.maxResultSize", "512m")
        conf.set("spark.sql.shuffle.partitions", "4")
        conf.set("spark.default.parallelism", "4")
        conf.set("spark.network.timeout", "300s")
        conf.set("spark.driver.extraJavaOptions", "-Xss4m -Dspark.authenticate=false")
        conf.set("spark.python.worker.reuse", "true")
        conf.set("spark.executor.logs.rolling.enabled", "false")
        spark = SparkSession.builder.config(conf=conf).getOrCreate()
        spark.sparkContext.setLogLevel("ERROR")

        # Re-apply Python logging config after Spark init — Spark resets the
        # root logger handler, so we must restore it here.
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            force=True,
        )
        logging.getLogger().setLevel(logging.INFO)
        # Route uvicorn logs through the same handler
        logging.getLogger("uvicorn").setLevel(logging.INFO)
        logging.getLogger("uvicorn.access").setLevel(logging.INFO)

        logger.info("Spark session started. Python logging restored.")
        logger.info("Loading ML models...")
        app.state.spark = spark
        app.state.profit_model = PipelineModel.load(str(MODELS_DIR / "rf_profit_model"))
        app.state.yield_model = PipelineModel.load(str(MODELS_DIR / "gbt_actual_yield_model"))
        app.state.demand_model = PipelineModel.load(str(MODELS_DIR / "logistic_regression_demand_level_model"))
        app.state.market_price_model = PipelineModel.load(str(MODELS_DIR / "gbt_market_price_model"))

        logger.info("Calculating historical baselines from dataset...")
        dataset_path = str(BASE_DIR / "data" / "processed" / "mazra3ti_dataset.csv")
        df = spark.read.csv(dataset_path, header=True, inferSchema=True)

        # Calculate per-hectare baselines
        baselines_df = df.groupBy("crop_name").agg(
            F.avg("market_price").alias("avg_market_price"),
            F.avg(F.col("actual_yield") / F.col("area_size")).alias("avg_yield_per_ha"),
            F.avg(F.col("fertilizer_used") / F.col("area_size")).alias("avg_fert_per_ha"),
            F.avg(F.col("water_used") / F.col("area_size")).alias("avg_water_per_ha"),
            F.avg("supply_level").alias("avg_supply"),
        )

        baselines_dict = {
            row["crop_name"]: {
                "market_price": row["avg_market_price"] or 0.0,
                "actual_yield": row["avg_yield_per_ha"] or 0.0,
                "fertilizer_used": row["avg_fert_per_ha"] or 0.0,
                "water_used": row["avg_water_per_ha"] or 0.0,
                "supply_level": row["avg_supply"] or 0.0,
            }
            for row in baselines_df.collect()
        }
        app.state.crop_baselines = baselines_dict
        logger.info(f"Loaded baselines for {len(baselines_dict)} crops.")

        logger.info("Hasad API is ready.")
        yield

    except Exception as exc:
        logger.exception("Failed during startup: %s", exc)
        raise
    finally:
        logger.info("Shutting down Spark session...")
        app.state.spark.stop()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Hasad Prediction API",
    description="Profit, yield, demand & market price prediction for mazra3ti.",
    version="4.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware — disable caching so Render's CDN never serves stale responses
# ---------------------------------------------------------------------------
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Surrogate-Control"] = "no-store"
        response.headers["CDN-Cache-Control"] = "no-store"

        return response

app.add_middleware(NoCacheMiddleware)


# ---------------------------------------------------------------------------
# Enums (removed - using string values directly)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Simplified Input Schemas (frontend provides minimal data)
# ---------------------------------------------------------------------------

class ProfitInput(BaseModel):
    """Minimal input for profit prediction."""
    crop_name: str = Field(..., min_length=1, max_length=100)
    governorate: str
    area_donums: float = Field(..., gt=0, description="Area in donums")
    planting_date: date
    sale_date: date

    @field_validator("sale_date")
    @classmethod
    def sale_after_planting(cls, sale_date: date, info) -> date:
        planting_date = info.data.get("planting_date")
        if planting_date and sale_date <= planting_date:
            raise ValueError("sale_date must be after planting_date")
        return sale_date


class YieldInput(BaseModel):
    """Minimal input for yield prediction."""
    crop_name: str = Field(..., min_length=1, max_length=100)
    governorate: str
    area_donums: float = Field(..., gt=0, description="Area in donums")
    planting_date: date
    sale_date: date
    pest_indicator: bool = False

    @field_validator("sale_date")
    @classmethod
    def sale_after_planting(cls, sale_date: date, info) -> date:
        planting_date = info.data.get("planting_date")
        if planting_date and sale_date <= planting_date:
            raise ValueError("sale_date must be after planting_date")
        return sale_date


class DemandInput(BaseModel):
    """Minimal input for demand prediction."""
    crop_name: str = Field(..., min_length=1, max_length=100)
    governorate: str
    market_location: str  # One of: Amman, Zarqa, Irbid, Aqaba, Other
    sale_date: date


class MarketPriceInput(BaseModel):
    """Minimal input for market price prediction."""
    crop_name: str = Field(..., min_length=1, max_length=100)
    market_location: str  # One of: Amman, Zarqa, Irbid, Aqaba, Other
    sale_date: date


class OptimizationInput(BaseModel):
    """Minimal input for optimization prediction."""
    area_donums: float = Field(..., gt=0, description="Total farm area in donums")
    governorate: str
    planting_date: date
    sale_date: date
    crops_list: str = Field(..., description="Comma-separated crop names (e.g. 'Tomato,Wheat,Olive')")


# ---------------------------------------------------------------------------
# Response Schemas
# ---------------------------------------------------------------------------
class PredictionResponse(BaseModel):
    type: str
    value: str
    crop: str
    area_donums: float


class DemandResponse(BaseModel):
    type: str
    value: str  # "Low" / "Medium" / "High"
    crop: str


class MarketPriceResponse(BaseModel):
    type: str
    value: str
    crop: str


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------
def _add_date_features(df, *col_names):
    """Add date-derived features (year, month, day) to DataFrame."""
    for col_name in col_names:
        prefix = col_name.split("_")[0]
        parsed = F.to_date(col_name)
        df = (
            df.withColumn(f"{prefix}_year", F.year(parsed))
            .withColumn(f"{prefix}_month", F.month(parsed))
            .withColumn(f"{prefix}_day", F.dayofmonth(parsed))
        )
    return df


# ---------------------------------------------------------------------------
# Prediction functions (blocking — run in thread pool)
# ---------------------------------------------------------------------------
def _run_profit(
    spark: SparkSession,
    model: PipelineModel,
    crop_name: str,
    farm_location: str,
    area_ha: float,
    fertilizer_used: float,
    water_used: float,
    actual_yield: float,
    market_price: float,
    supply_level: float,
    humidity: float,
    rainfall: float,
    wind_speed: float,
    demand_level_ord: int,
    planting_date: str,
    sale_date: str,
) -> float:
    """Run profit prediction with all required fields."""
    data = [
        {
            "crop_name": crop_name,
            "farm_location": farm_location,
            "area_size": area_ha,
            "fertilizer_used": fertilizer_used,
            "water_used": water_used,
            "actual_yield": actual_yield,
            "market_price": market_price,
            "supply_level": supply_level,
            "humidity": humidity,
            "rainfall": rainfall,
            "wind_speed": wind_speed,
            "demand_level_ord": demand_level_ord,
            "planting_date": planting_date,
            "sale_date": sale_date,
        }
    ]
    df = _add_date_features(spark.createDataFrame(data), "planting_date", "sale_date")
    return model.transform(df).select("prediction").collect()[0][0]


def _run_yield(
    spark: SparkSession,
    model: PipelineModel,
    crop_name: str,
    farm_location: str,
    region_type: str,
    area_ha: float,
    fertilizer_used: float,
    water_used: float,
    temperature: float,
    humidity: float,
    rainfall: float,
    wind_speed: float,
    pest_indicator: int,
    planting_date: str,
    sale_date: str,
) -> float:
    """Run yield prediction with all required fields."""
    data = [
        {
            "crop_name": crop_name,
            "farm_location": farm_location,
            "region_type": region_type,
            "area_size": area_ha,
            "fertilizer_used": fertilizer_used,
            "water_used": water_used,
            "temperature": temperature,
            "humidity": humidity,
            "rainfall": rainfall,
            "wind_speed": wind_speed,
            "pest_indicator": pest_indicator,
            "planting_date": planting_date,
            "sale_date": sale_date,
        }
    ]
    df = _add_date_features(spark.createDataFrame(data), "planting_date", "sale_date")
    return model.transform(df).select("prediction").collect()[0][0]


def _run_demand(
    spark: SparkSession,
    model: PipelineModel,
    crop_name: str,
    market_location: str,
    sufficiency_status: str,
    sale_gap_days: int,
    supply_score: float,
    market_price: float,
    temperature: float,
    humidity: float,
    rainfall: float,
    wind_speed: float,
    sale_date: str,
) -> float:
    """Run demand prediction with all required fields."""
    data = [
        {
            "crop_name": crop_name,
            "market_location": market_location,
            "sufficiency_status": sufficiency_status,
            "sale_gap_days": float(sale_gap_days),
            "supply_score": supply_score,
            "market_price": market_price,
            "temperature": temperature,
            "humidity": humidity,
            "rainfall": rainfall,
            "wind_speed": wind_speed,
            "sale_date": sale_date,
        }
    ]
    df = _add_date_features(spark.createDataFrame(data), "sale_date")
    return model.transform(df).select("prediction").collect()[0][0]


def _run_market_price(
    spark: SparkSession,
    model: PipelineModel,
    crop_name: str,
    market_location: str,
    sufficiency_status: str,
    sale_gap_days: int,
    demand_score: float,
    supply_score: float,
    sale_date: str,
) -> float:
    """Run market price prediction with all required fields."""
    data = [
        {
            "crop_name": crop_name,
            "market_location": market_location,
            "sufficiency_status": sufficiency_status,
            "sale_gap_days": float(sale_gap_days),
            "demand_score": demand_score,
            "supply_score": supply_score,
            "sale_date": sale_date,
        }
    ]
    df = _add_date_features(spark.createDataFrame(data), "sale_date")
    return model.transform(df).select("prediction").collect()[0][0]


# Async wrappers
async def _predict(fn, *args) -> float:
    """Run blocking function in executor pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn, *args)


# ---------------------------------------------------------------------------
# Prediction routes (refactored with auto-fetching)
# ---------------------------------------------------------------------------

@app.post("/predict_profit", response_model=PredictionResponse, summary="Predict crop profit")
async def predict_profit(
    crop_name: str = Form(...),
    governorate: str = Form(...),
    area_donums: float = Form(...),
    planting_date: str = Form(...),
    sale_date: str = Form(...),
): 
    """
    Simplified profit prediction endpoint.
    Frontend provides minimal inputs; backend auto-fetches data.
    """
    logger.info("PREDICTION REQUEST RECEIVED - predict_profit")
    logger.info(f"RAW BODY: crop_name={crop_name}, governorate={governorate}")
    try:
        payload = ProfitInput(
            crop_name=crop_name,
            governorate=governorate,
            area_donums=area_donums,
            planting_date=planting_date,
            sale_date=sale_date,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    logger.info("Profit request | crop=%s governorate=%s area=%s donums", crop_name, governorate, area_donums)

    try:
        # Auto-derive farm_location from governorate
        if governorate not in GOVERNORATE_TO_FARM_LOCATION:
            raise ValueError(f"Unknown governorate: {governorate}")
        farm_location = GOVERNORATE_TO_FARM_LOCATION[governorate]

        # Convert donums to hectares
        area_ha = area_donums * DONUM_TO_HECTARE

        # Fetch weather
        weather = await fetch_weather(governorate)

        # Fetch crop data from Supabase
        crop_id = await get_crop_id_by_name(crop_name)
        if not crop_id:
            logger.warning(f"Crop '{crop_name}' not found in DB, using baselines")
        
        # Get supply and demand
        supply = await get_latest_supply(crop_id) if crop_id else 0.0
        demand = await get_latest_demand(crop_id) if crop_id else 0.0
        
        # Use baselines as fallback
        baselines = app.state.crop_baselines.get(crop_name, {})
        
        # Compute demand_level_ord
        demand_level_ord = compute_demand_level_ord(supply, demand)

        # Get market price from baselines
        market_price = await get_market_price(crop_name, app.state.crop_baselines)
        if supply == 0:
            supply = baselines.get("supply_level", 0.0)
        if market_price == 0:
            market_price = baselines.get("market_price", 100.0)
        
        # Get fertilizer and water from baselines (per hectare)
        fertilizer_used = baselines.get("fertilizer_used", 0.0) * area_ha
        water_used = baselines.get("water_used", 0.0) * area_ha

        # Predict actual_yield using the yield model
        region_type = GOVERNORATE_TO_REGION_TYPE[governorate]
        try:
            actual_yield = await _predict(
                _run_yield,
                app.state.spark,
                app.state.yield_model,
                crop_name,
                farm_location,
                region_type,
                area_ha,
                fertilizer_used,
                water_used,
                weather["temperature"],
                weather["humidity"],
                weather["rainfall"],
                weather["wind_speed"],
                0,  # No pest indicator by default
                str(payload.planting_date),
                str(payload.sale_date),
            )
            logger.info("Predicted yield for %s: %.2f", crop_name, actual_yield)
        except Exception as e:
            logger.warning(f"Yield prediction failed for profit calc, using baseline: {e}")
            actual_yield = baselines.get("actual_yield", 0.0) * area_ha

        # Run prediction
        pred = await _predict(
            _run_profit,
            app.state.spark,
            app.state.profit_model,
            crop_name,
            farm_location,
            area_ha,
            fertilizer_used,
            water_used,
            actual_yield,
            market_price,
            supply,
            weather["humidity"],
            weather["rainfall"],
            weather["wind_speed"],
            demand_level_ord,
            str(payload.planting_date),
            str(payload.sale_date),
        )

        logger.info("Profit result: $%.2f", pred)
        return PredictionResponse(
            type="Profit Prediction",
            value=f"${round(pred, 2)}",
            crop=crop_name,
            area_donums=area_donums,
        )

    except Exception as exc:
        logger.exception("Profit prediction failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Prediction failed. Check server logs.")


@app.post("/predict_yield", response_model=PredictionResponse, summary="Predict crop yield")
async def predict_yield(
    crop_name: str = Form(...),
    governorate: str = Form(...),
    area_donums: float = Form(...),
    planting_date: str = Form(...),
    sale_date: str = Form(...),
    pest_indicator: bool = Form(False),
):
    """
    Simplified yield prediction endpoint.
    Frontend provides minimal inputs; backend auto-fetches data.
    """
    try:
        payload = YieldInput(
            crop_name=crop_name,
            governorate=governorate,
            area_donums=area_donums,
            planting_date=planting_date,
            sale_date=sale_date,
            pest_indicator=pest_indicator,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    logger.info("Yield request | crop=%s governorate=%s area=%s donums", crop_name, governorate, area_donums)

    try:
        # Auto-derive farm_location and region_type from governorate
        if governorate not in GOVERNORATE_TO_FARM_LOCATION:
            raise ValueError(f"Unknown governorate: {governorate}")
        farm_location = GOVERNORATE_TO_FARM_LOCATION[governorate]
        region_type = GOVERNORATE_TO_REGION_TYPE[governorate]

        # Convert donums to hectares
        area_ha = area_donums * DONUM_TO_HECTARE

        # Fetch weather
        weather = await fetch_weather(governorate)

        # Use baselines for fertilizer and water
        baselines = app.state.crop_baselines.get(crop_name, {})
        fertilizer_used = baselines.get("fertilizer_used", 0.0) * area_ha
        water_used = baselines.get("water_used", 0.0) * area_ha

        # Run prediction
        pred = await _predict(
            _run_yield,
            app.state.spark,
            app.state.yield_model,
            crop_name,
            farm_location,
            region_type,
            area_ha,
            fertilizer_used,
            water_used,
            weather["temperature"],
            weather["humidity"],
            weather["rainfall"],
            weather["wind_speed"],
            int(pest_indicator),
            str(payload.planting_date),
            str(payload.sale_date),
        )

        logger.info("Yield result: %.2f Tons/Units", pred)
        return PredictionResponse(
            type="Yield Prediction",
            value=f"{round(pred, 2)} Tons/Units",
            crop=crop_name,
            area_donums=area_donums,
        )

    except Exception as exc:
        logger.exception("Yield prediction failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Prediction failed. Check server logs.")


@app.post("/predict_demand", response_model=DemandResponse, summary="Predict demand level")
async def predict_demand(
    crop_name: str = Form(...),
    governorate: str = Form(...),
    market_location: str = Form(...),
    sale_date: str = Form(...),
):
    """
    Simplified demand prediction endpoint.
    Frontend provides minimal inputs; backend auto-fetches data.
    """
    try:
        payload = DemandInput(
            crop_name=crop_name,
            governorate=governorate,
            market_location=market_location,
            sale_date=sale_date,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    logger.info("Demand request | crop=%s market=%s", crop_name, market_location)

    try:
        # Fetch crop data
        crop_id = await get_crop_id_by_name(crop_name)
        if not crop_id:
            logger.warning(f"Crop '{crop_name}' not found in DB, using baselines")

        # Get supply and demand
        supply = await get_latest_supply(crop_id) if crop_id else 0.0
        demand = await get_latest_demand(crop_id) if crop_id else 0.0

        # Determine sufficiency status
        if demand == 0:
            sufficiency_status = "Balanced"
        elif supply > demand * 1.2:
            sufficiency_status = "Surplus"
        elif supply < demand * 0.8:
            sufficiency_status = "Deficit"
        else:
            sufficiency_status = "Balanced"

        # Calculate sale_gap_days
        _sale_date = payload.sale_date if isinstance(payload.sale_date, date) else datetime.strptime(str(payload.sale_date), "%Y-%m-%d").date()
        sale_gap_days = (_sale_date - date.today()).days

        # Use baselines as fallback
        baselines = app.state.crop_baselines.get(crop_name, {})
        
        # Get market price and weather
        market_price = await get_market_price(crop_name, app.state.crop_baselines)
        weather = await fetch_weather(governorate)
        if supply == 0:
            supply = baselines.get("supply_level", 0.0)
        if market_price == 0:
            market_price = baselines.get("market_price", 100.0)

        # Run prediction
        pred = await _predict(
            _run_demand,
            app.state.spark,
            app.state.demand_model,
            crop_name,
            market_location,
            sufficiency_status,
            max(0, sale_gap_days),  # Ensure non-negative
            supply,
            market_price,
            weather["temperature"],
            weather["humidity"],
            weather["rainfall"],
            weather["wind_speed"],
            str(payload.sale_date),
        )

        # Map numeric label to string
        label = DEMAND_LABEL_MAP.get(pred, f"Unknown ({pred})")
        logger.info("Demand result: %s (raw=%.1f)", label, pred)
        return DemandResponse(type="Demand Level Prediction", value=label, crop=crop_name)

    except Exception as exc:
        logger.exception("Demand prediction failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Prediction failed. Check server logs.")


@app.post("/predict_market_price", response_model=MarketPriceResponse, summary="Predict market price")
async def predict_market_price(
    crop_name: str = Form(...),
    market_location: str = Form(...),
    sale_date: str = Form(...),
    governorate: str = Form(None),
):
    """
    Simplified market price prediction endpoint.
    Frontend provides minimal inputs; backend auto-fetches data.
    Governorate is optional, used only for sufficiency context.
    """
    try:
        payload = MarketPriceInput(
            crop_name=crop_name,
            market_location=market_location,
            sale_date=sale_date,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    logger.info("Market price request | crop=%s market=%s", crop_name, market_location)

    try:
        # Fetch crop data
        crop_id = await get_crop_id_by_name(crop_name)
        if not crop_id:
            logger.warning(f"Crop '{crop_name}' not found in DB, using baselines")

        # Get supply and demand
        supply = await get_latest_supply(crop_id) if crop_id else 0.0
        demand = await get_latest_demand(crop_id) if crop_id else 0.0

        # Determine sufficiency status
        if demand == 0:
            sufficiency_status = "Balanced"
        elif supply > demand * 1.2:
            sufficiency_status = "Surplus"
        elif supply < demand * 0.8:
            sufficiency_status = "Deficit"
        else:
            sufficiency_status = "Balanced"

        # Calculate sale_gap_days
        _sale_date = payload.sale_date if isinstance(payload.sale_date, date) else datetime.strptime(str(payload.sale_date), "%Y-%m-%d").date()
        sale_gap_days = (_sale_date - date.today()).days

        # Run prediction
        pred = await _predict(
            _run_market_price,
            app.state.spark,
            app.state.market_price_model,
            crop_name,
            market_location,
            sufficiency_status,
            max(0, sale_gap_days),  # Ensure non-negative
            demand,
            supply,
            str(payload.sale_date),
        )

        logger.info("Market price result: $%.2f", pred)
        return MarketPriceResponse(
            type="Market Price Prediction",
            value=f"${round(pred, 2)}",
            crop=crop_name,
        )

    except Exception as exc:
        logger.exception("Market price prediction failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Prediction failed. Check server logs.")


@app.post("/predict_optimization")
async def predict_optimization(
    area_donums: float = Form(...),
    governorate: str = Form(...),
    planting_date: str = Form(...),
    sale_date: str = Form(...),
    crops_list: str = Form(...),
):
    """
    Simplified optimization endpoint.
    Converts area_donums to hectares and derives farm_location from governorate.
    """
    try:
        payload = OptimizationInput(
            area_donums=area_donums,
            governorate=governorate,
            planting_date=planting_date,
            sale_date=sale_date,
            crops_list=crops_list,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    logger.info("Optimization request | governorate=%s area=%s donums crops=%s", governorate, area_donums, crops_list)

    try:
        # Validate governorate
        if governorate not in GOVERNORATE_TO_FARM_LOCATION:
            raise ValueError(f"Unknown governorate: {governorate}")
        farm_location = GOVERNORATE_TO_FARM_LOCATION[governorate]

        # Convert donums to hectares
        area_ha = area_donums * DONUM_TO_HECTARE

        crops = [c.strip() for c in crops_list.split(",")]
        spark = app.state.spark
        model = app.state.profit_model
        baselines = app.state.crop_baselines

        # Fetch weather once for all crops
        weather = await fetch_weather(governorate)

        # Prepare ALL rows at once
        rows_to_predict = []
        for crop in crops:
            crop_data = baselines.get(crop, {})
            
            # Fetch market price and supply for this crop
            market_price = await get_market_price(crop, app.state.crop_baselines)
            crop_id = await get_crop_id_by_name(crop)
            supply = await get_latest_supply(crop_id) if crop_id else 0.0
            demand = await get_latest_demand(crop_id) if crop_id else 0.0

            # Use baselines as fallback
            if not market_price:
                market_price = crop_data.get("market_price", 100.0)
            if supply == 0:
                supply = crop_data.get("supply_level", 0.0)

            # Compute demand_level_ord
            demand_level_ord = compute_demand_level_ord(supply, demand)

            rows_to_predict.append(
                {
                    "crop_name": crop,
                    "farm_location": farm_location,
                    "area_size": 1.0,  # Per hectare for optimization
                    "fertilizer_used": crop_data.get("fertilizer_used", 0.0),
                    "water_used": crop_data.get("water_used", 0.0),
                    "actual_yield": crop_data.get("actual_yield", 0.0),
                    "market_price": market_price,
                    "supply_level": supply,
                    "humidity": weather["humidity"],
                    "rainfall": weather["rainfall"],
                    "wind_speed": weather["wind_speed"],
                    "demand_level_ord": demand_level_ord,
                    "planting_date": str(payload.planting_date),
                    "sale_date": str(payload.sale_date),
                }
            )

        # Create ONE DataFrame and add date features
        batch_df = spark.createDataFrame(rows_to_predict)
        batch_df = _add_date_features(batch_df, "planting_date", "sale_date")

        # Transform ONCE
        predictions_df = model.transform(batch_df).select("crop_name", "prediction").collect()

        # Map results back to original crops order
        prediction_map = {row["crop_name"]: max(0, row["prediction"]) for row in predictions_df}
        predicted_profits = [prediction_map.get(crop, 0.0) for crop in crops]

        # Run the Optimizer
        result = optimizer_service.optimize(area_ha, crops, predicted_profits)

        logger.info("Optimization result: %s", result)
        return {
            "status": "success",
            "total_farm_area_donums": area_donums,
            "total_farm_area_hectares": area_ha,
            "crop_allocation": result["allocations"],
            "expected_total_profit": result.get("total_profit", 0),
        }

    except Exception as exc:
        logger.exception("Optimization prediction failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Prediction failed. Check server logs.")
# ---------------------------------------------------------------------------
# HTML routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def profit_form():
    return (TEMPLATE_DIR / "index.html").read_text()


@app.get("/yield", response_class=HTMLResponse, include_in_schema=False)
async def yield_form():
    return (TEMPLATE_DIR / "yield.html").read_text()


@app.get("/demand", response_class=HTMLResponse, include_in_schema=False)
async def demand_form():
    return (TEMPLATE_DIR / "demand.html").read_text()


@app.get("/market_price", response_class=HTMLResponse, include_in_schema=False)
async def market_price_form():
    return (TEMPLATE_DIR / "market_price.html").read_text()


@app.get("/optimize", response_class=HTMLResponse, include_in_schema=False)
async def optimize_form():
    return (TEMPLATE_DIR / "optimize.html").read_text()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", summary="Health check")
async def health():
    return {"status": "ok"}