# Hasad API - Frontend Integration Guide

**API Version:** 4.1.0 (Refactored)

This guide provides everything a frontend developer needs to integrate with the Hasad Prediction API. All examples use **form-data** (not JSON) for submissions.

---

## Overview

The Hasad API has been refactored to minimize frontend input burden. Farmers now provide only **essential information**, and the backend automatically:
- Fetches weather data from Open-Meteo API
- Queries crop data from Supabase (supply, demand, prices)
- Derives location-based attributes from governorate
- Converts area measurements (donums ↔ hectares)
- Falls back gracefully to historical averages if data is missing

**Key Change:** Areas are entered in **donums** by farmers, automatically converted to hectares internally.

---

## Base URL

```
http://localhost:8000
```

(Adjust host/port for production deployment)

---

## Response Format

All prediction endpoints return JSON with the following structure:

### Success Response (200 OK)
```json
{
  "type": "Prediction Type",
  "value": "Formatted prediction value",
  "crop": "Crop name",
  "area_donums": 5.0
}
```

### Error Response (422 or 500)
```json
{
  "detail": "Error message explaining what went wrong"
}
```

---

## Endpoint: POST /predict_profit

**Description:** Predict the expected profit for a crop given farm conditions.

### Frontend Must Send

| Field | Type | Required | Example | Notes |
|-------|------|----------|---------|-------|
| `crop_name` | string | ✓ | "Tomato" | Exact crop name (case-sensitive) |
| `governorate` | string | ✓ | "Amman" | One of 12 Jordanian governorates |
| `area_donums` | float | ✓ | 5.0 | Farm area in donums (> 0) |
| `planting_date` | date | ✓ | "2024-03-15" | YYYY-MM-DD format |
| `sale_date` | date | ✓ | "2024-06-20" | YYYY-MM-DD format, must be after planting_date |

### Backend Handles Automatically

| Field | Source | Purpose |
|-------|--------|---------|
| `farm_location` | Governorate mapping | Region classification (Highland, Jordan Valley, Southern) |
| `humidity` | Open-Meteo API | Current weather condition |
| `rainfall` | Open-Meteo API | Current weather condition |
| `wind_speed` | Open-Meteo API | Current weather condition |
| `market_price` | Hardcoded defaults (pending DB) | Latest market price for crop |
| `supply_level` | Supabase `crop_supply` table | Latest supply estimate |
| `demand_level_ord` | Computed from supply/demand ratio | Ordinal demand pressure |
| `actual_yield` | Crop baseline * area | Expected yield per area |
| `fertilizer_used` | Crop baseline * area | Per-hectare average |
| `water_used` | Crop baseline * area | Per-hectare average |

### Example Request (form-data)
```
POST /predict_profit HTTP/1.1
Content-Type: application/x-www-form-urlencoded

crop_name=Tomato
&governorate=Amman
&area_donums=5.0
&planting_date=2024-03-15
&sale_date=2024-06-20
```

### Example Response
```json
{
  "type": "Profit Prediction",
  "value": "$450.75",
  "crop": "Tomato",
  "area_donums": 5.0
}
```

### Validation Rules
- `sale_date` must be after `planting_date`
- `area_donums` must be > 0
- `governorate` must be one of: Amman, Zarqa, Irbid, Aqaba, Mafraq, Balqa, Madaba, Karak, Tafilah, Ma'an, Jerash, Ajloun
- `crop_name` should match names in the Supabase `crops` table

---

## Endpoint: POST /predict_yield

**Description:** Predict crop yield (in tons/units) based on farm conditions.

### Frontend Must Send

| Field | Type | Required | Example | Notes |
|-------|------|----------|---------|-------|
| `crop_name` | string | ✓ | "Wheat" | Exact crop name |
| `governorate` | string | ✓ | "Irbid" | One of 12 Jordanian governorates |
| `area_donums` | float | ✓ | 10.0 | area in donums (> 0) |
| `planting_date` | date | ✓ | "2024-01-20" | YYYY-MM-DD format |
| `sale_date` | date | ✓ | "2024-06-15" | YYYY-MM-DD format |
| `pest_indicator` | boolean | ✗ | false | Presence of pests (default: false) |

### Backend Handles Automatically

| Field | Source | Purpose |
|-------|--------|---------|
| `farm_location` | Governorate mapping | Region classification |
| `region_type` | Governorate mapping | Urban/Rural/Suburban classification |
| `temperature` | Open-Meteo API | Current weather |
| `humidity` | Open-Meteo API | Current weather |
| `rainfall` | Open-Meteo API | Current weather |
| `wind_speed` | Open-Meteo API | Current weather |
| `fertilizer_used` | Crop baseline * area | Per-hectare average |
| `water_used` | Crop baseline * area | Per-hectare average |

### Example Request (form-data)
```
POST /predict_yield HTTP/1.1
Content-Type: application/x-www-form-urlencoded

crop_name=Wheat
&governorate=Irbid
&area_donums=10.0
&planting_date=2024-01-20
&sale_date=2024-06-15
&pest_indicator=false
```

### Example Response
```json
{
  "type": "Yield Prediction",
  "value": "28.5 Tons/Units",
  "crop": "Wheat",
  "area_donums": 10.0
}
```

### Validation Rules
- `sale_date` must be after `planting_date`
- `area_donums` must be > 0
- `pest_indicator` should be "true" or "false" (or 1/0)

---

## Endpoint: POST /predict_demand

**Description:** Predict demand level (High/Medium/Low) for a crop in a specific market.

### Frontend Must Send

| Field | Type | Required | Example | Notes |
|-------|------|----------|---------|-------|
| `crop_name` | string | ✓ | "Olive" | Exact crop name |
| `governorate` | string | ✓ | "Zarqa" | Used for weather context |
| `market_location` | string | ✓ | "Amman" | One of: Amman, Zarqa, Irbid, Aqaba, Other |
| `sale_date` | date | ✓ | "2024-07-01" | YYYY-MM-DD format |

### Backend Handles Automatically

| Field | Source | Purpose |
|-------|--------|---------|
| `sale_gap_days` | Calculated from sale_date - today | Days until sale |
| `supply_score` | Supabase `crop_supply` table | Current estimated supply (tons) |
| `demand` | Supabase `crop_demand` table | Current estimated demand (tons) |
| `sufficiency_status` | Computed from supply/demand | Surplus / Balanced / Deficit |
| `market_price` | Hardcoded defaults (pending DB) | Current market price |
| `temperature` | Open-Meteo API | Weather for governorate |
| `humidity` | Open-Meteo API | Weather for governorate |
| `rainfall` | Open-Meteo API | Weather for governorate |
| `wind_speed` | Open-Meteo API | Weather for governorate |

### Example Request (form-data)
```
POST /predict_demand HTTP/1.1
Content-Type: application/x-www-form-urlencoded

crop_name=Olive
&governorate=Zarqa
&market_location=Amman
&sale_date=2024-07-01
```

### Example Response
```json
{
  "type": "Demand Level Prediction",
  "value": "High",
  "crop": "Olive"
}
```

### Validation Rules
- `market_location` must be one of: Amman, Zarqa, Irbid, Aqaba, Other
- `sale_date` should be in the future (or near-future)

---

## Endpoint: POST /predict_market_price

**Description:** Predict the market price per ton for a crop.

### Frontend Must Send

| Field | Type | Required | Example | Notes |
|-------|------|----------|---------|-------|
| `crop_name` | string | ✓ | "Cucumber" | Exact crop name |
| `market_location` | string | ✓ | "Irbid" | One of: Amman, Zarqa, Irbid, Aqaba, Other |
| `sale_date` | date | ✓ | "2024-08-10" | YYYY-MM-DD format |
| `governorate` | string | ✗ | "Irbid" | Optional; used for sufficiency context |

### Backend Handles Automatically

| Field | Source | Purpose |
|-------|--------|---------|
| `sale_gap_days` | Calculated from sale_date - today | Days until sale |
| `demand_score` | Supabase `crop_demand` table | Current estimated demand (tons) |
| `supply_score` | Supabase `crop_supply` table | Current estimated supply (tons) |
| `sufficiency_status` | Computed from supply/demand | Surplus / Balanced / Deficit |

### Example Request (form-data)
```
POST /predict_market_price HTTP/1.1
Content-Type: application/x-www-form-urlencoded

crop_name=Cucumber
&market_location=Irbid
&sale_date=2024-08-10
```

### Example Response
```json
{
  "type": "Market Price Prediction",
  "value": "$125.50",
  "crop": "Cucumber"
}
```

### Validation Rules
- `market_location` must be one of: Amman, Zarqa, Irbid, Aqaba, Other

---

## Endpoint: POST /predict_optimization

**Description:** Recommend optimal crop allocation across farm area to maximize total profit.

### Frontend Must Send

| Field | Type | Required | Example | Notes |
|-------|------|----------|---------|-------|
| `area_donums` | float | ✓ | 50.0 | Total farm area in donums (> 0) |
| `governorate` | string | ✓ | "Madaba" | Farm location (one of 12 governorates) |
| `planting_date` | date | ✓ | "2024-02-01" | YYYY-MM-DD format |
| `sale_date` | date | ✓ | "2024-08-15" | YYYY-MM-DD format |
| `crops_list` | string | ✓ | "Tomato,Wheat,Olive" | Comma-separated crop names |

### Backend Handles Automatically

| Field | Source | Purpose |
|-------|--------|---------|
| `farm_location` | Governorate mapping | Region for all crops |
| Per-crop market data | Supabase + hardcoded defaults | Price, supply, demand for each crop |
| Per-crop weather | Open-Meteo API | Weather for the governorate |
| Per-crop baselines | Historical data | Fertilizer, water, yield averages |

### Example Request (form-data)
```
POST /predict_optimization HTTP/1.1
Content-Type: application/x-www-form-urlencoded

area_donums=50.0
&governorate=Madaba
&planting_date=2024-02-01
&sale_date=2024-08-15
&crops_list=Tomato,Wheat,Olive
```

### Example Response
```json
{
  "status": "success",
  "total_farm_area_donums": 50.0,
  "total_farm_area_hectares": 5.0,
  "crop_allocation": {
    "Tomato": { "area_hectares": 2.0, "area_donums": 20.0 },
    "Wheat": { "area_hectares": 2.0, "area_donums": 20.0 },
    "Olive": { "area_hectares": 1.0, "area_donums": 10.0 }
  },
  "expected_total_profit": 2850.50
}
```

### Validation Rules
- `area_donums` must be > 0
- `sale_date` must be after `planting_date`
- `crops_list` must contain at least 1 crop, comma-separated

---

## Supported Governorates

When the backend requires a `governorate` parameter, use **exactly** one of these values:

- Amman
- Zarqa
- Irbid
- Aqaba
- Mafraq
- Balqa
- Madaba
- Karak
- Tafilah
- Ma'an
- Jerash
- Ajloun

---

## Date Format

All date fields must be in **ISO 8601 format**: `YYYY-MM-DD`

Examples:
- ✓ "2024-03-15"
- ✗ "15/03/2024"
- ✗ "15-Mar-2024"

---

## Error Handling

### Expected Error Status Codes

| Status | Reason | Action |
|--------|--------|--------|
| 200 | Success | Process response normally |
| 422 | Validation error (bad field type or value) | Check field values and types |
| 500 | Server error (DB, model, or weather API failure) | Retry after a delay; check server logs |

### Graceful Degradation

If the backend cannot fetch data from Supabase or Open-Meteo API, it will:
1. Log a warning
2. **Use safe fallback values** (historical averages from CSV)
3. **Continue with the prediction** (never fail the entire request)

Farmers may see slightly less accurate predictions, but the API will not crash.

---

## Common Issues & Troubleshooting

### Issue: `"Unknown governorate: XYZ"`
**Cause:** Governorate name spelling or case mismatch.  
**Solution:** Verify the exact spelling from the [Supported Governorates](#supported-governorates) list.

### Issue: Crop name not found, using baselines
**Cause:** `crop_name` does not exist in Supabase `crops` table.  
**Solution:** Verify crop name matches the database exactly (case-sensitive).

### Issue: `sale_date must be after planting_date`
**Cause:** Sale date is equal to or before the planting date.  
**Solution:** Ensure `sale_date` is in the future relative to `planting_date`.

### Issue: `area_donums must be > 0`
**Cause:** Area is zero or negative.  
**Solution:** Enter a positive farm area.

### Issue: Weather data unavailable, using defaults
**Cause:** Open-Meteo API is temporarily unavailable.  
**Solution:** The API will use safe default weather values (temp=20°C, humidity=50%, etc.). Results remain valid.

---

## Area Conversion Reference

Frontend users enter area in **donums**. The API automatically converts:

$$\text{hectares} = \text{donums} \times 0.1$$

Example:
- 5 donums = 0.5 hectares
- 50 donums = 5 hectares
- 100 donums = 10 hectares

---

## Testing with cURL

### Test Profit Prediction
```bash
curl -X POST http://localhost:8000/predict_profit \
  -F "crop_name=Tomato" \
  -F "governorate=Amman" \
  -F "area_donums=5.0" \
  -F "planting_date=2024-03-15" \
  -F "sale_date=2024-06-20"
```

### Test Yield Prediction
```bash
curl -X POST http://localhost:8000/predict_yield \
  -F "crop_name=Wheat" \
  -F "governorate=Irbid" \
  -F "area_donums=10.0" \
  -F "planting_date=2024-01-20" \
  -F "sale_date=2024-06-15"
```

### Test Demand Prediction
```bash
curl -X POST http://localhost:8000/predict_demand \
  -F "crop_name=Olive" \
  -F "governorate=Zarqa" \
  -F "market_location=Amman" \
  -F "sale_date=2024-07-01"
```

### Test Market Price Prediction
```bash
curl -X POST http://localhost:8000/predict_market_price \
  -F "crop_name=Cucumber" \
  -F "market_location=Irbid" \
  -F "sale_date=2024-08-10"
```

### Test Optimization
```bash
curl -X POST http://localhost:8000/predict_optimization \
  -F "area_donums=50.0" \
  -F "governorate=Madaba" \
  -F "planting_date=2024-02-01" \
  -F "sale_date=2024-08-15" \
  -F "crops_list=Tomato,Wheat,Olive"
```

---

## Health Check

To verify the API is running:

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{ "status": "ok" }
```

---

## Support & Contact

For issues or questions about the API:
1. Check the [Troubleshooting](#common-issues--troubleshooting) section
2. Review server logs (check the terminal running the API)
3. Verify all required fields are present and correctly formatted
4. Ensure the Supabase database and Open-Meteo API are accessible

---

## Appendix: Governorate to Farm Location Mapping

| Governorate | Farm Location |
|-------------|---------------|
| Aqaba | Southern |
| Ma'an | Southern |
| Tafilah | Southern |
| Karak | Highlands |
| Madaba | Highlands |
| Amman | Highlands |
| Zarqa | Highlands |
| Balqa | Highlands |
| Jerash | Highlands |
| Ajloun | Highlands |
| Irbid | Highlands |
| Mafraq | Jordan Valley |

---

## Appendix: Governorate to Region Type Mapping

| Governorate | Region Type |
|-------------|-------------|
| Amman | Urban |
| Zarqa | Urban |
| Irbid | Urban |
| Aqaba | Urban |
| Balqa | Suburban |
| Madaba | Suburban |
| Karak | Rural |
| Tafilah | Rural |
| Ma'an | Rural |
| Mafraq | Rural |
| Jerash | Rural |
| Ajloun | Rural |

