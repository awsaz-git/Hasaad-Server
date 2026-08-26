# Hasaad — AI Backend

This is the AI/ML backend for **Hasaad**, a decision-support platform for Jordanian farmers. The main database and user-facing backend live in Supabase; this service exposes the predictive models as a REST API consumed by the mobile and web apps.

## What it does

Farmers provide a handful of inputs (crop, governorate, area in donums, planting and sale dates). The server automatically pulls live weather, national supply/demand figures from Supabase, and crop baseline statistics — then runs the appropriate ML model and returns a prediction in seconds.

### Prediction endpoints

| Endpoint | Model | Output |
|---|---|---|
| `POST /predict_profit` | Random Forest Regressor | Estimated profit in JOD |
| `POST /predict_yield` | Gradient Boosted Trees | Expected harvest in tons |
| `POST /predict_demand` | Logistic Regression | Demand level — High / Medium / Low |
| `POST /predict_market_price` | Gradient Boosted Trees | Expected market price in JOD/ton |
| `POST /predict_optimization` | GBT + SciPy SLSQP optimizer | Recommended area allocation per crop |

### Farm optimization

`/predict_optimization` takes a list of candidate crops and the total farm area, runs profit predictions for each crop, then solves a constrained optimization problem (SLSQP) to find the allocation that maximizes total expected profit while keeping each crop between 5% and 40% of the total area.

### Auto-enrichment

The farmer only provides minimal inputs. The server fills in the rest automatically:

- **Live weather** — temperature, humidity, rainfall, wind speed fetched from Open-Meteo using governorate coordinates
- **Supply & demand** — latest figures pulled from the `crop_supply` and `crop_demand` tables in Supabase
- **Historical baselines** — per-crop averages (market price, yield, fertilizer, water usage) computed from the training dataset at startup and used as fallbacks
- **Location mapping** — governorate is automatically mapped to farm location (Highlands / Southern / Jordan Valley) and region type (Urban / Suburban / Rural)
- **Unit conversion** — area in donums is converted to hectares internally (1 donum = 0.1 ha)

## Models

All models are trained with PySpark MLlib and stored as PipelineModel artifacts under `models/`.

| Directory | Algorithm | Target |
|---|---|---|
| `rf_profit_model` | Random Forest | Profit (JOD) |
| `gbt_actual_yield_model` | Gradient Boosted Trees | Actual yield (tons) |
| `gbt_market_price_model` | Gradient Boosted Trees | Market price (JOD/ton) |
| `logistic_regression_demand_level_model` | Logistic Regression | Demand level class |
| `gbt_profit_model` | Gradient Boosted Trees | Profit (used in optimization) |

Training notebooks are in `notebooks/`.

## Stack

- **Python 3.9** / **FastAPI** / **Uvicorn**
- **PySpark 3.5** — model inference via MLlib PipelineModel
- **Supabase** — live crop supply/demand data
- **Open-Meteo API** — real-time weather
- **SciPy** — farm area optimization (SLSQP)
- Deployed on **Render**

