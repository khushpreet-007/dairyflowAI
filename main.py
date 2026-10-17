import json
import os
import sys
import re
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Import Vertex AI Gemini client
from app.client.vertex_client import client

# Import database session and models
try:
    from app.db import get_db, Vendor, Inventory
except ImportError:
    from db import get_db, Vendor, Inventory

load_dotenv()

OPENWEATHER_KEY = os.getenv("OPENWEATHER_API_KEY", "")

# --- 1. DIRECTORY PATHS ---
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "app" / "static"
TEMPLATE_DIR = BASE_DIR / "app" / "templates"

# --- 2. FASTAPI APP INITIALIZATION ---
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# --- 3. PYDANTIC MODELS ---
class VendorRequest(BaseModel):
    name: str
    location: str
    required_milk: float
    rating: float = 4.0

class ManagerQuery(BaseModel):
    query: str
    inventory: dict
    weather: str = ""

class NotifyRequest(BaseModel):
    status: str = "accepted"
    quantity: float = 0.0

class AutoHandleRequest(BaseModel):
    inventory: dict
    weather: str = ""

# --- HELPER: ENSURE INVENTORY EXISTS & DEDUCT STOCK (FEFO) ---
def get_or_create_inventory(db: Session) -> Inventory:
    inv = db.query(Inventory).filter(Inventory.id == "current").first()
    if not inv:
        inv = Inventory(
            id="current",
            milk_0days=30.0,
            milk_1day=10.0,
            milk_2days=5.0,
            office="Noida"
        )
        db.add(inv)
        db.commit()
        db.refresh(inv)
    return inv

def deduct_inventory_fefo(db: Session, quantity_to_deduct: float):
    """
    FEFO (First-Expired, First-Out):
    Deducts from 0-day milk first, then 1-day, then 2-day milk.
    """
    inv = get_or_create_inventory(db)
    rem = float(quantity_to_deduct)

    # 1. Deduct from 0-day urgent stock first
    used_0 = min(inv.milk_0days, rem)
    inv.milk_0days = round(inv.milk_0days - used_0, 2)
    rem -= used_0

    # 2. Deduct from 1-day stock next
    if rem > 0:
        used_1 = min(inv.milk_1day, rem)
        inv.milk_1day = round(inv.milk_1day - used_1, 2)
        rem -= used_1

    # 3. Deduct from 2-day stock last
    if rem > 0:
        used_2 = min(inv.milk_2days, rem)
        inv.milk_2days = round(inv.milk_2days - used_2, 2)
        rem -= used_2

    db.commit()
    db.refresh(inv)
    return inv

# --- HELPER: CONVERT LOCATION NAME TO LAT/LON & GET WEATHER ---
def get_coordinates(location_name: str):
    """Converts a sector/city name (e.g. 'Sector 62, Noida') into (lat, lon)."""
    # Default coordinates for Noida BMC Hub
    default_coords = (28.5355, 77.3910)
    if not location_name:
        return default_coords

    # Clean sector strings like "Sector 62, Noida" -> search city or full string
    if OPENWEATHER_KEY:
        try:
            geo_url = f"http://api.openweathermap.org/geo/1.0/direct?q={location_name},IN&limit=1&appid={OPENWEATHER_KEY}"
            res = requests.get(geo_url, timeout=3)
            if res.status_code == 200 and len(res.json()) > 0:
                data = res.json()[0]
                return (data["lat"], data["lon"])
        except Exception as e:
            print(f"Geocoding fallback triggered: {e}", flush=True)

    # Smart fallback dictionary for common NCR / Hackathon test locations
    loc_lower = location_name.lower()
    if "greater noida" in loc_lower:
        return (28.4744, 77.5040)
    elif "delhi" in loc_lower:
        return (28.6139, 77.2090)
    elif "gurgaon" in loc_lower or "gurugram" in loc_lower:
        return (28.4595, 77.0266)
    elif "ghaziabad" in loc_lower:
        return (28.6692, 77.4538)
    return default_coords

# --- 4. ROUTES ---

@app.get("/")
async def home():
    index_file = TEMPLATE_DIR / "index.html"
    return FileResponse(index_file)

@app.get("/vendor")
async def vendor_page():
    vendor_file = TEMPLATE_DIR / "vendor.html"
    return FileResponse(vendor_file)

@app.get("/api/weather")
def fetch_live_weather(location: str = "Noida"):
    lat, lon = get_coordinates(location)
    
    # 1. Try OpenWeatherMap Current Weather API using lat & lon
    if OPENWEATHER_KEY:
        try:
            owm_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&units=metric&appid={OPENWEATHER_KEY}"
            res = requests.get(owm_url, timeout=3)
            if res.status_code == 200:
                data = res.json()
                temp = round(data["main"]["temp"], 1)
                desc = data["weather"][0]["main"]
                humidity = data["main"]["humidity"]
                risk = "HIGH SPOILAGE RISK" if temp >= 30 else ("MODERATE" if temp >= 24 else "OPTIMAL COLD-CHAIN")
                return {
                    "location": location,
                    "lat": lat,
                    "lon": lon,
                    "summary": f"{desc}, {temp}°C (Humidity: {humidity}%) [{risk}]",
                    "temp": temp
                }
        except Exception as e:
            print(f"OpenWeather API error: {e}", flush=True)

        # 2. Fallback: Check if key is a WAQI token (from your snippet)
        try:
            waqi_url = f"https://api.waqi.info/feed/geo:{lat};{lon}/?token={OPENWEATHER_KEY}"
            res = requests.get(waqi_url, timeout=3)
            if res.status_code == 200 and res.json().get("status") == "ok":
                aqi_data = res.json()["data"]
                aqi = aqi_data.get("aqi", 0)
                temp = aqi_data.get("iaqi", {}).get("t", {}).get("v", 26)
                return {
                    "location": location,
                    "lat": lat,
                    "lon": lon,
                    "summary": f"Temp: {temp}°C, AQI: {aqi} (Lat: {round(lat,2)}, Lon: {round(lon,2)})",
                    "temp": temp
                }
        except Exception:
            pass

    # Default live-like response if API key is missing/invalid
    return {
        "location": location,
        "lat": lat,
        "lon": lon,
        "summary": f"28.5°C, Clear Sky (Lat: {round(lat,2)}, Lon: {round(lon,2)}) [MODERATE THERMAL RISK]",
        "temp": 28.5
    }

@app.get("/api/inventory")
def get_inventory(db: Session = Depends(get_db)):
    inv = get_or_create_inventory(db)
    return {
        "milk_0days": inv.milk_0days,
        "milk_1day": inv.milk_1day,
        "milk_2days": inv.milk_2days,
        "total_milk": round(inv.milk_0days + inv.milk_1day + inv.milk_2days, 2),
        "office": inv.office,
    }

@app.post("/api/inventory/disrupt")
def simulate_disruption(db: Session = Depends(get_db)):
    """Persists the yield drop / cooling unit disruption in DB."""
    inv = get_or_create_inventory(db)
    inv.milk_1day = max(0.0, inv.milk_1day - 7.0)
    db.commit()
    db.refresh(inv)
    return {"success": True, "milk_1day": inv.milk_1day}

@app.post("/api/inventory/reset")
def reset_demo(db: Session = Depends(get_db)):
    """Handy button for hackathon judges demo to reset stock to full!"""
    inv = get_or_create_inventory(db)
    inv.milk_0days = 30.0
    inv.milk_1day = 10.0
    inv.milk_2days = 5.0
    db.commit()
    return {"success": True}

@app.get("/api/vendors")
def get_vendors(db: Session = Depends(get_db)):
    vendors = db.query(Vendor).all()
    return [
        {
            "id": v.id,
            "name": v.name,
            "location": v.location,
            "required_milk": v.required_milk,
            "rating": v.rating,
            "status": v.status,
            "assigned_quantity": v.assigned_quantity,
            "notification_time": str(v.notification_time) if v.notification_time else None
        }
        for v in vendors
    ]

@app.get("/api/vendors/{vendor_id}")
def get_single_vendor(vendor_id: int, db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return {
        "id": v.id,
        "name": v.name,
        "location": v.location,
        "required_milk": v.required_milk,
        "rating": v.rating,
        "status": v.status,
        "assigned_quantity": v.assigned_quantity,
        "notification_time": str(v.notification_time) if v.notification_time else None
    }

@app.post("/api/vendors/register")
def register_vendor(vendor: VendorRequest, db: Session = Depends(get_db)):
    new_vendor = Vendor(
        name=vendor.name,
        location=vendor.location,
        required_milk=vendor.required_milk,
        rating=vendor.rating,
        status="pending",
    )
    db.add(new_vendor)
    db.commit()
    db.refresh(new_vendor)
    return {"success": True, "vendor_id": new_vendor.id}

# MANUAL ACCEPT / REJECT FROM BMC DASHBOARD (NOW DECREASES INVENTORY!)
@app.post("/api/notify/{vendor_id}")
def send_notification(vendor_id: int, payload: NotifyRequest, db: Session = Depends(get_db)):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    inv = get_or_create_inventory(db)
    total_available = inv.milk_0days + inv.milk_1day + inv.milk_2days

    if payload.status == "accepted":
        if total_available <= 0:
            raise HTTPException(status_code=400, detail="Out of stock! Cannot accept order.")
        # Cap allocation to available stock if requested > available
        allocated = min(float(payload.quantity), total_available)
        vendor.status = "accepted"
        vendor.assigned_quantity = allocated
        vendor.notification_time = datetime.utcnow()
        # Deduct from database inventory using FEFO
        deduct_inventory_fefo(db, allocated)
    else:
        vendor.status = "rejected"
        vendor.assigned_quantity = 0.0
        vendor.notification_time = datetime.utcnow()

    db.commit()
    db.refresh(vendor)
    return {"success": True, "status": vendor.status, "assigned_quantity": vendor.assigned_quantity}

# AUTOMATICALLY HANDLE ALL PENDING ORDERS BY AI (WITH INVENTORY DEDUCTION)
@app.post("/api/ai/handle-orders")
def ai_handle_all_orders(payload: AutoHandleRequest, db: Session = Depends(get_db)):
    pending_vendors = db.query(Vendor).filter(Vendor.status == "pending").all()
    if not pending_vendors:
        return {"summary": "No pending vendor orders to process!", "decisions": []}

    inv = get_or_create_inventory(db)
    total_available = round(inv.milk_0days + inv.milk_1day + inv.milk_2days, 2)

    vendors_list = []
    for v in pending_vendors:
        lat, lon = get_coordinates(v.location)
        vendors_list.append({
            "id": v.id,
            "name": v.name,
            "location": v.location,
            "coordinates": f"{round(lat,3)}, {round(lon,3)}",
            "required_milk": v.required_milk,
            "rating": v.rating
        })

    prompt = f"""
    You are an Intelligent Dairy Manufacturing & Supply Chain AI (FEFO Cold-Chain Optimizer).
    CURRENT BMC INVENTORY: Total = {total_available}L 
    - Urgent 0-day shelf-life: {inv.milk_0days}L (MUST DISPATCH FIRST TO PREVENT SPOILAGE)
    - 1-day shelf-life: {inv.milk_1day}L
    - 2-day shelf-life: {inv.milk_2days}L
    LIVE WEATHER & THERMAL CONDITIONS: {payload.weather}

    PENDING VENDOR ORDERS:
    {json.dumps(vendors_list, indent=2)}

    RULES:
    1. You CANNOT allocate more than the Total Available Milk ({total_available}L) across all accepted vendors combined.
    2. If totalAvailable is 0, reject all orders.
    3. Prioritize vendors with high ratings, closer coordinates to Noida (28.53, 77.39) especially in warm weather, and quantities that clear the urgent {inv.milk_0days}L stock.
    4. Partial fulfillment is allowed if stock is running low.

    Respond ONLY with a valid JSON object matching this exact schema:
    {{
      "summary": "Industrial efficiency summary explaining FEFO stock depletion and thermal routing",
      "decisions": [
        {{
          "id": 1,
          "name": "vendor name",
          "status": "accepted",
          "assigned_quantity": 25.0,
          "reason": "High rating, clears 0-day urgent batch"
        }}
      ]
    }}
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw_text = response.text.strip()
        cleaned_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.MULTILINE).strip()
        result = json.loads(cleaned_text)
        decisions = result.get("decisions", [])

        remaining_stock = total_available
        final_decisions = []

        for dec in decisions:
            v = db.query(Vendor).filter(Vendor.id == dec.get("id")).first()
            if not v:
                continue

            status_val = str(dec.get("status", "rejected")).lower()
            req_qty = float(dec.get("assigned_quantity", v.required_milk))

            if status_val == "accepted" and remaining_stock > 0:
                actual_qty = min(req_qty, remaining_stock)
                v.status = "accepted"
                v.assigned_quantity = actual_qty
                v.notification_time = datetime.utcnow()
                deduct_inventory_fefo(db, actual_qty)
                remaining_stock = round(remaining_stock - actual_qty, 2)
                dec["assigned_quantity"] = actual_qty
                final_decisions.append(dec)
            else:
                v.status = "rejected"
                v.assigned_quantity = 0.0
                v.notification_time = datetime.utcnow()
                dec["status"] = "rejected"
                dec["assigned_quantity"] = 0.0
                if remaining_stock <= 0 and status_val == "accepted":
                    dec["reason"] = "Rejected automatically: Inventory depleted"
                final_decisions.append(dec)

        db.commit()
        return {
            "summary": result.get("summary", "FEFO optimization complete."),
            "decisions": final_decisions
        }

    except Exception as e:
        print(f"AI Auto-Handle Fallback: {e}", flush=True)
        remaining = total_available
        fallback_decisions = []
        for v in sorted(pending_vendors, key=lambda x: x.rating, reverse=True):
            if remaining > 0:
                alloc = min(v.required_milk, remaining)
                v.status = "accepted"
                v.assigned_quantity = alloc
                deduct_inventory_fefo(db, alloc)
                remaining = round(remaining - alloc, 2)
                reason = "Accepted via FEFO priority rule"
            else:
                v.status = "rejected"
                v.assigned_quantity = 0.0
                reason = "Rejected (Inventory exhausted)"
            v.notification_time = datetime.utcnow()
            fallback_decisions.append({
                "id": v.id, "name": v.name, "status": v.status,
                "assigned_quantity": v.assigned_quantity, "reason": reason
            })
        db.commit()
        return {"summary": "Processed all orders using FEFO priority rules.", "decisions": fallback_decisions}

@app.post("/api/ai/recommend")
def ai_recommend(query: ManagerQuery, db: Session = Depends(get_db)):
    inv = get_or_create_inventory(db)
    vendors = get_vendors(db)
    if not vendors:
        return {"vendor": "No Vendors Available", "reasoning": "Register a vendor first.", "quantity": 0, "delivery_time": "N/A"}

    prompt = f"""
    You are an Industrial Dairy Supply Chain AI.
    CURRENT INVENTORY: {inv.milk_0days}L (0d urgent), {inv.milk_1day}L (1d), {inv.milk_2days}L (2d)
    WEATHER & AMBIENT TELEMETRY: {query.weather}
    VENDORS: {json.dumps(vendors, indent=2)}
    MANAGER QUERY: "{query.query}"

    Respond ONLY with a valid JSON object:
    {{
      "vendor": "exact vendor name",
      "reasoning": "explanation referencing FEFO spoilage & weather",
      "quantity": 25.0,
      "delivery_time": "14:30"
    }}
    """
    try:
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.text.strip(), flags=re.MULTILINE).strip()
        result = json.loads(cleaned)
        return {
            "vendor": result.get("vendor", vendors[0]["name"]),
            "reasoning": result.get("reasoning", "Optimal FEFO match."),
            "quantity": float(result.get("quantity", vendors[0]["required_milk"])),
            "delivery_time": result.get("delivery_time", "11:00 AM"),
        }
    except Exception:
        top = sorted(vendors, key=lambda v: v.get("rating", 0), reverse=True)[0]
        return {"vendor": top["name"], "reasoning": "Highest rated vendor.", "quantity": top["required_milk"], "delivery_time": "10:30 AM"}