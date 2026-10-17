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

from app.client.vertex_client import client

try:
    from app.db import get_db, Vendor, Inventory, Truck
except ImportError:
    from db import get_db, Vendor, Inventory, Truck

load_dotenv()

OPENWEATHER_KEY = os.getenv("OPENWEATHER_API_KEY", "")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "app" / "static"
TEMPLATE_DIR = BASE_DIR / "app" / "templates"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# --- FIXED ROUTE LOGISTICS MATRIX (FROM NOIDA BMC HUB) ---
ROUTE_DATA = {
    "Noida": {"distance_km": 8, "eta": "20 mins", "lat": 28.5355, "lon": 77.3910},
    "Delhi": {"distance_km": 25, "eta": "45 mins", "lat": 28.6139, "lon": 77.2090},
    "Gurugram": {"distance_km": 50, "eta": "90 mins", "lat": 28.4595, "lon": 77.0266},
}

def get_route_info(location_name: str):
    for key, val in ROUTE_DATA.items():
        if key.lower() in (location_name or "").lower():
            return key, val
    return "Noida", ROUTE_DATA["Noida"]

# --- MODELS ---
class VendorRequest(BaseModel):
    name: str
    location: str
    required_milk: float
    rating: float = 4.5

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

# --- HELPERS: INVENTORY & UNIVERSAL FLEET ---
def get_or_create_inventory(db: Session) -> Inventory:
    inv = db.query(Inventory).filter(Inventory.id == "current").first()
    if not inv:
        inv = Inventory(id="current", milk_0days=30.0, milk_1day=10.0, milk_2days=5.0, office="Noida")
        db.add(inv)
        db.commit()
        db.refresh(inv)
    return inv

def get_or_create_trucks(db: Session):
    trucks = db.query(Truck).all()
    if not trucks:
        # Neutral Refrigerated Units - ANY truck can serve ANY city!
        default_fleet = [
            Truck(name="Reefer-Unit-01", capacity=30.0, current_load=0.0, assigned_route=None, status="available"),
            Truck(name="Reefer-Unit-02", capacity=30.0, current_load=0.0, assigned_route=None, status="available"),
            Truck(name="Reefer-Unit-03", capacity=30.0, current_load=0.0, assigned_route=None, status="available"),
        ]
        db.add_all(default_fleet)
        db.commit()
        trucks = db.query(Truck).all()
    return trucks

def allocate_fleet_for_order(db: Session, route_name: str, desired_qty: float):
    """
    Universal Fleet Allocator:
    - Any available truck can go to ANY destination.
    - First tries to fit on a truck already heading to `route_name` IF it can fit the whole order (or if no empty trucks exist).
    - If an order is 30L and Truck-1 already has 15L on that route, it assigns a fresh 30L truck (or splits across trucks) so NO milk is left behind!
    """
    get_or_create_trucks(db)
    rem_to_load = float(desired_qty)
    assigned_trucks = []
    total_loaded = 0.0

    # 1. Check if a truck on the same route can fit the ENTIRE order right now (clean batching)
    exact_fit_truck = db.query(Truck).filter(
        Truck.assigned_route == route_name,
        (Truck.capacity - Truck.current_load) >= rem_to_load
    ).first()

    if exact_fit_truck:
        exact_fit_truck.current_load = round(exact_fit_truck.current_load + rem_to_load, 2)
        exact_fit_truck.status = "in_transit"
        db.commit()
        return exact_fit_truck.name, rem_to_load

    # 2. Otherwise, use any truck on the same route with spare space + overflow to any available unassigned trucks
    same_route_trucks = db.query(Truck).filter(
        Truck.assigned_route == route_name,
        Truck.current_load < Truck.capacity
    ).all()

    free_trucks = db.query(Truck).filter(
        Truck.assigned_route.is_(None),
        Truck.status == "available"
    ).all()

    # If the order fits completely in a single free truck (e.g. 30L order), give it a dedicated free truck first!
    if free_trucks and rem_to_load > 15.0:
        candidate_trucks = free_trucks + same_route_trucks
    else:
        candidate_trucks = same_route_trucks + free_trucks

    for t in candidate_trucks:
        if rem_to_load <= 0:
            break
        spare = round(t.capacity - t.current_load, 2)
        if spare <= 0:
            continue
        load_now = min(rem_to_load, spare)
        t.assigned_route = route_name
        t.current_load = round(t.current_load + load_now, 2)
        t.status = "in_transit"
        rem_to_load = round(rem_to_load - load_now, 2)
        total_loaded = round(total_loaded + load_now, 2)
        assigned_trucks.append(t.name)

    db.commit()
    if not assigned_trucks:
        return None, 0.0
    return " + ".join(assigned_trucks), total_loaded

def deduct_inventory_fefo(db: Session, quantity_to_deduct: float):
    inv = get_or_create_inventory(db)
    rem = float(quantity_to_deduct)

    used_0 = min(inv.milk_0days, rem)
    inv.milk_0days = round(inv.milk_0days - used_0, 2)
    rem = round(rem - used_0, 2)

    used_1 = 0.0
    if rem > 0:
        used_1 = min(inv.milk_1day, rem)
        inv.milk_1day = round(inv.milk_1day - used_1, 2)
        rem = round(rem - used_1, 2)

    used_2 = 0.0
    if rem > 0:
        used_2 = min(inv.milk_2days, rem)
        inv.milk_2days = round(inv.milk_2days - used_2, 2)
        rem = round(rem - used_2, 2)

    db.commit()
    db.refresh(inv)
    return f"FEFO: {used_0}L(0d), {used_1}L(1d), {used_2}L(2d)"

# --- ROUTES ---

@app.get("/")
async def home():
    return FileResponse(TEMPLATE_DIR / "index.html")

@app.get("/vendor")
async def vendor_page():
    return FileResponse(TEMPLATE_DIR / "vendor.html")

@app.get("/api/weather")
def fetch_live_weather(location: str = "Noida"):
    route_key, info = get_route_info(location)
    lat, lon = info["lat"], info["lon"]

    if OPENWEATHER_KEY:
        try:
            owm_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&units=metric&appid={OPENWEATHER_KEY}"
            res = requests.get(owm_url, timeout=3)
            if res.status_code == 200:
                data = res.json()
                temp = round(data["main"]["temp"], 1)
                desc = data["weather"][0]["main"]
                risk = "HIGH THERMAL RISK" if temp >= 30 else ("MODERATE" if temp >= 24 else "OPTIMAL")
                return {"location": route_key, "summary": f"{desc}, {temp}°C · {risk}", "temp": temp}
        except Exception:
            pass

    return {"location": route_key, "summary": "28.5°C, Clear · MODERATE RISK", "temp": 28.5}

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

@app.get("/api/trucks")
def get_trucks(db: Session = Depends(get_db)):
    trucks = get_or_create_trucks(db)
    return [
        {
            "id": t.id,
            "name": t.name,
            "capacity": t.capacity,
            "current_load": t.current_load,
            "assigned_route": t.assigned_route or "Standby (Any Route)",
            "status": t.status,
            "eta": ROUTE_DATA[t.assigned_route]["eta"] if t.assigned_route in ROUTE_DATA else "Ready"
        }
        for t in trucks
    ]

@app.post("/api/trucks/{truck_id}/return")
def mark_truck_returned(truck_id: int, db: Session = Depends(get_db)):
    t = db.query(Truck).filter(Truck.id == truck_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Truck not found")
    t.current_load = 0.0
    t.assigned_route = None
    t.status = "available"
    db.commit()
    return {"success": True}

@app.post("/api/inventory/disrupt")
def simulate_disruption(db: Session = Depends(get_db)):
    inv = get_or_create_inventory(db)
    inv.milk_1day = max(0.0, inv.milk_1day - 7.0)
    db.commit()
    return {"success": True, "milk_1day": inv.milk_1day}

@app.post("/api/inventory/reset")
def reset_demo(db: Session = Depends(get_db)):
    inv = get_or_create_inventory(db)
    inv.milk_0days = 30.0
    inv.milk_1day = 10.0
    inv.milk_2days = 5.0
    for t in get_or_create_trucks(db):
        t.current_load = 0.0
        t.assigned_route = None
        t.status = "available"
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
            "distance_km": ROUTE_DATA.get(v.location, ROUTE_DATA["Noida"])["distance_km"],
            "required_milk": v.required_milk,
            "rating": v.rating,
            "status": v.status,
            "assigned_quantity": v.assigned_quantity,
            "truck_name": v.truck_name,
            "eta": v.eta,
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
        "distance_km": ROUTE_DATA.get(v.location, ROUTE_DATA["Noida"])["distance_km"],
        "required_milk": v.required_milk,
        "rating": v.rating,
        "status": v.status,
        "assigned_quantity": v.assigned_quantity,
        "truck_name": v.truck_name,
        "eta": v.eta,
        "notification_time": str(v.notification_time) if v.notification_time else None
    }

@app.post("/api/vendors/register")
def register_vendor(vendor: VendorRequest, db: Session = Depends(get_db)):
    route_key, _ = get_route_info(vendor.location)
    new_vendor = Vendor(
        name=vendor.name,
        location=route_key,
        required_milk=vendor.required_milk,
        rating=vendor.rating,
        status="pending",
    )
    db.add(new_vendor)
    db.commit()
    db.refresh(new_vendor)
    return {"success": True, "vendor_id": new_vendor.id}

# MANUAL ACCEPT / REJECT
@app.post("/api/notify/{vendor_id}")
def send_notification(vendor_id: int, payload: NotifyRequest, db: Session = Depends(get_db)):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    inv = get_or_create_inventory(db)
    total_available = round(inv.milk_0days + inv.milk_1day + inv.milk_2days, 2)

    if payload.status == "accepted":
        if total_available <= 0:
            raise HTTPException(status_code=400, detail="Chiller Out of Stock!")

        desired_qty = min(float(payload.quantity), total_available)
        route_key, route_info = get_route_info(vendor.location)

        truck_names, loaded_qty = allocate_fleet_for_order(db, route_key, desired_qty)
        if not truck_names or loaded_qty <= 0:
            raise HTTPException(
                status_code=400,
                detail="All Reefer Trucks are at full capacity! Click 'Recall Truck' on a unit first."
            )

        vendor.status = "accepted"
        vendor.assigned_quantity = loaded_qty
        vendor.truck_name = truck_names
        vendor.eta = f"{route_info['eta']} ({route_info['distance_km']} km)"
        vendor.notification_time = datetime.utcnow()

        deduct_inventory_fefo(db, loaded_qty)
    else:
        vendor.status = "rejected"
        vendor.assigned_quantity = 0.0
        vendor.truck_name = None
        vendor.eta = None
        vendor.notification_time = datetime.utcnow()

    db.commit()
    db.refresh(vendor)
    return {
        "success": True,
        "status": vendor.status,
        "assigned_quantity": vendor.assigned_quantity,
        "truck_name": vendor.truck_name,
        "eta": vendor.eta
    }

# ONE-CLICK AI AUTO-ALLOCATION (EXACT INVENTORY & FLEET ENGINE + GEMINI EXECUTIVE SUMMARY)
@app.post("/api/ai/handle-orders")
def ai_handle_all_orders(payload: AutoHandleRequest, db: Session = Depends(get_db)):
    pending_vendors = db.query(Vendor).filter(Vendor.status == "pending").all()
    if not pending_vendors:
        return {"summary": "All vendor orders have already been processed.", "decisions": []}

    inv = get_or_create_inventory(db)
    initial_stock = round(inv.milk_0days + inv.milk_1day + inv.milk_2days, 2)

    # Sort pending orders by: 1. Shortest distance from Noida Hub, 2. Highest rating
    sorted_vendors = sorted(
        pending_vendors,
        key=lambda v: (ROUTE_DATA.get(v.location, ROUTE_DATA["Noida"])["distance_km"], -v.rating)
    )

    final_decisions = []
    for v in sorted_vendors:
        inv = get_or_create_inventory(db)
        remaining_stock = round(inv.milk_0days + inv.milk_1day + inv.milk_2days, 2)
        r_key, r_info = get_route_info(v.location)

        if remaining_stock <= 0:
            v.status = "rejected"
            v.assigned_quantity = 0.0
            v.notification_time = datetime.utcnow()
            final_decisions.append({
                "id": v.id,
                "name": v.name,
                "route": r_key,
                "status": "rejected",
                "assigned_quantity": 0.0,
                "reason": "Rejected: Chiller inventory completely exhausted (0L remaining)"
            })
            continue

        qty_to_fulfill = min(v.required_milk, remaining_stock)
        truck_names, loaded_qty = allocate_fleet_for_order(db, r_key, qty_to_fulfill)

        if truck_names and loaded_qty > 0:
            fefo_log = deduct_inventory_fefo(db, loaded_qty)
            v.status = "accepted"
            v.assigned_quantity = loaded_qty
            v.truck_name = truck_names
            v.eta = f"{r_info['eta']} ({r_info['distance_km']} km)"
            v.notification_time = datetime.utcnow()

            partial_note = " (Partial fulfillment due to stock/fleet cap)" if loaded_qty < v.required_milk else ""
            final_decisions.append({
                "id": v.id,
                "name": v.name,
                "route": r_key,
                "status": "accepted",
                "assigned_quantity": loaded_qty,
                "truck": truck_names,
                "eta": v.eta,
                "reason": f"Route: {r_key} ({r_info['distance_km']}km, ETA {r_info['eta']}){partial_note} · Dispatched on {truck_names} [{fefo_log}]"
            })
        else:
            v.status = "rejected"
            v.assigned_quantity = 0.0
            v.notification_time = datetime.utcnow()
            final_decisions.append({
                "id": v.id,
                "name": v.name,
                "route": r_key,
                "status": "rejected",
                "assigned_quantity": 0.0,
                "reason": "Rejected: All 3 Refrigerated Fleet Units (90L total cap) are currently in transit"
            })

    db.commit()

    # Ask Gemini to write a concise executive summary of the exact mathematical dispatch
    summary_text = f"FEFO Dispatch executed across {len(final_decisions)} orders ({initial_stock}L initial chiller stock)."
    try:
        prompt = f"""
        Summarize this dairy cold-chain dispatch in 2 crisp, professional sentences for an industrial operations manager.
        Initial Stock: {initial_stock}L | Weather: {payload.weather}
        Exact Dispatch Results: {json.dumps(final_decisions)}
        """
        res = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        if res.text:
            summary_text = res.text.strip()
    except Exception:
        pass

    return {
        "summary": summary_text,
        "decisions": final_decisions
    }

@app.post("/api/ai/recommend")
def ai_recommend(query: ManagerQuery, db: Session = Depends(get_db)):
    inv = get_or_create_inventory(db)
    vendors = get_vendors(db)
    trucks = get_trucks(db)
    if not vendors:
        return {"vendor": "No Vendors Available", "reasoning": "Register a vendor first.", "quantity": 0, "delivery_time": "N/A"}

    prompt = f"""
    You are an Industrial Dairy Supply Chain AI at the Noida BMC Hub.
    DISTANCES FROM NOIDA HUB: Noida = 8km (20 mins), Delhi = 25km (45 mins), Gurugram = 50km (90 mins).
    INVENTORY: {inv.milk_0days}L (0d), {inv.milk_1day}L (1d), {inv.milk_2days}L (2d)
    UNIVERSAL FLEET (Any truck can serve any city, 30L max each): {json.dumps(trucks)}
    VENDORS: {json.dumps(vendors)}
    MANAGER QUERY: "{query.query}"

    Respond ONLY with valid JSON:
    {{
      "vendor": "exact vendor name",
      "reasoning": "mention exact km distance, route batching, and FEFO stock",
      "quantity": 20.0,
      "delivery_time": "20 mins (8 km)"
    }}
    """
    try:
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.text.strip(), flags=re.MULTILINE).strip()
        result = json.loads(cleaned)
        return {
            "vendor": result.get("vendor", vendors[0]["name"]),
            "reasoning": result.get("reasoning", "Optimal route & FEFO match."),
            "quantity": float(result.get("quantity", vendors[0]["required_milk"])),
            "delivery_time": result.get("delivery_time", "20 mins"),
        }
    except Exception:
        top = vendors[0]
        return {"vendor": top["name"], "reasoning": f"Located in {top['location']} ({top['distance_km']} km).", "quantity": top["required_milk"], "delivery_time": "20 mins"}