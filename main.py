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
    "Noida": {"distance_km": 8, "eta": "~20 mins", "lat": 28.5355, "lon": 77.3910},
    "Delhi": {"distance_km": 25, "eta": "~45 mins", "lat": 28.6139, "lon": 77.2090},
    "Gurugram": {"distance_km": 50, "eta": "~90 mins", "lat": 28.4595, "lon": 77.0266},
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

# --- HELPERS: INVENTORY & FLEET ---
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
        default_fleet = [
            Truck(name="Truck-1 (UP16-A-101)", capacity=30.0, current_load=0.0, assigned_route=None, status="available"),
            Truck(name="Truck-2 (DL1M-B-202)", capacity=30.0, current_load=0.0, assigned_route=None, status="available"),
            Truck(name="Truck-3 (HR26-C-303)", capacity=30.0, current_load=0.0, assigned_route=None, status="available"),
        ]
        db.add_all(default_fleet)
        db.commit()
        trucks = db.query(Truck).all()
    return trucks

def allocate_truck_for_order(db: Session, route_name: str, requested_qty: float):
    """
    Smart Route Batching:
    1. First looks for a truck ALREADY going to the SAME route ('Noida', 'Delhi', or 'Gurugram') that has spare capacity.
    2. Otherwise picks an empty 'available' truck and assigns it to this route.
    3. Caps allocation to the truck's remaining capacity (max 30L per truck).
    """
    get_or_create_trucks(db)

    # Step 1: Check if an existing truck on the SAME route has room (Route Batching!)
    same_route_truck = db.query(Truck).filter(
        Truck.assigned_route == route_name,
        Truck.current_load < Truck.capacity
    ).first()

    if same_route_truck:
        spare_cap = round(same_route_truck.capacity - same_route_truck.current_load, 2)
        loaded_qty = min(requested_qty, spare_cap)
        same_route_truck.current_load = round(same_route_truck.current_load + loaded_qty, 2)
        same_route_truck.status = "in_transit"
        db.commit()
        return same_route_truck, loaded_qty

    # Step 2: Find a fresh unassigned truck
    free_truck = db.query(Truck).filter(
        Truck.assigned_route.is_(None),
        Truck.status == "available"
    ).first()

    if free_truck:
        loaded_qty = min(requested_qty, free_truck.capacity)
        free_truck.assigned_route = route_name
        free_truck.current_load = round(loaded_qty, 2)
        free_truck.status = "in_transit"
        db.commit()
        return free_truck, loaded_qty

    return None, 0.0

def deduct_inventory_fefo(db: Session, quantity_to_deduct: float):
    inv = get_or_create_inventory(db)
    rem = float(quantity_to_deduct)

    used_0 = min(inv.milk_0days, rem)
    inv.milk_0days = round(inv.milk_0days - used_0, 2)
    rem -= used_0

    if rem > 0:
        used_1 = min(inv.milk_1day, rem)
        inv.milk_1day = round(inv.milk_1day - used_1, 2)
        rem -= used_1

    if rem > 0:
        used_2 = min(inv.milk_2days, rem)
        inv.milk_2days = round(inv.milk_2days - used_2, 2)
        rem -= used_2

    db.commit()
    db.refresh(inv)
    return inv

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
                risk = "HIGH SPOILAGE RISK" if temp >= 30 else ("MODERATE RISK" if temp >= 24 else "OPTIMAL")
                return {"location": route_key, "summary": f"{desc}, {temp}°C [{risk}]", "temp": temp}
        except Exception:
            pass

    return {"location": route_key, "summary": "28.5°C, Clear [MODERATE THERMAL RISK]", "temp": 28.5}

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
            "assigned_route": t.assigned_route or "Unassigned",
            "status": t.status,
            "eta": ROUTE_DATA[t.assigned_route]["eta"] if t.assigned_route in ROUTE_DATA else "--"
        }
        for t in trucks
    ]

@app.post("/api/trucks/{truck_id}/return")
def mark_truck_returned(truck_id: int, db: Session = Depends(get_db)):
    """Frees up a truck when it returns to BMC after completing its route deliveries."""
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
    """Resets Chiller Inventory AND recalls all Trucks for clean Hackathon Demos."""
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

# MANUAL ACCEPT / REJECT WITH AUTOMATIC TRUCK BATCHING
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

        # Allocate or batch onto a Truck (max 30L per truck)
        truck, loaded_qty = allocate_truck_for_order(db, route_key, desired_qty)
        if not truck or loaded_qty <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"No truck available for route '{route_key}'! Mark an in-transit truck as 'Returned' first."
            )

        vendor.status = "accepted"
        vendor.assigned_quantity = loaded_qty
        vendor.truck_name = truck.name
        vendor.eta = f"{route_info['eta']} ({route_info['distance_km']} km via {route_key} Route)"
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

# ONE-CLICK AI AUTO-ALLOCATION (STOCK + ROUTE BATCHING + 30L TRUCK CAPACITY)
@app.post("/api/ai/handle-orders")
def ai_handle_all_orders(payload: AutoHandleRequest, db: Session = Depends(get_db)):
    pending_vendors = db.query(Vendor).filter(Vendor.status == "pending").all()
    if not pending_vendors:
        return {"summary": "No pending vendor orders to process!", "decisions": []}

    inv = get_or_create_inventory(db)
    total_available = round(inv.milk_0days + inv.milk_1day + inv.milk_2days, 2)
    trucks = get_or_create_trucks(db)

    vendors_list = []
    for v in pending_vendors:
        r_key, r_info = get_route_info(v.location)
        vendors_list.append({
            "id": v.id,
            "name": v.name,
            "route": r_key,
            "distance_from_noida_hub_km": r_info["distance_km"],
            "estimated_travel_time": r_info["eta"],
            "required_milk": v.required_milk,
            "rating": v.rating
        })

    fleet_status = [
        {"name": t.name, "route": t.assigned_route, "remaining_capacity_L": t.capacity - t.current_load, "status": t.status}
        for t in trucks
    ]

    prompt = f"""
    You are an Industrial Dairy Supply Chain & Fleet Dispatch AI at the Noida BMC Hub.
    CHILLER INVENTORY: Total = {total_available}L (Urgent 0-day: {inv.milk_0days}L, 1-day: {inv.milk_1day}L, 2-day: {inv.milk_2days}L)
    FLEET STATUS (Each Truck max 30L capacity, orders on the SAME route share a truck):
    {json.dumps(fleet_status, indent=2)}

    PENDING VENDOR ORDERS:
    {json.dumps(vendors_list, indent=2)}

    DISPATCH RULES:
    1. DO NOT allocate more milk than Total Available ({total_available}L) or available Truck Capacity.
    2. Prioritize:
       - Closer routes first (Noida = 8km, Delhi = 25km, Gurugram = 50km) to minimize thermal spoilage.
       - Route Batching: Group vendors in the same city onto the same 30L truck.
       - Higher vendor rating.

    Respond ONLY with a valid JSON object:
    {{
      "summary": "Brief explanation of FEFO inventory usage and how orders were batched onto trucks by route",
      "decisions": [
        {{
          "id": 1,
          "name": "vendor name",
          "status": "accepted",
          "assigned_quantity": 20.0,
          "reason": "Closest route (Noida, 8km) + batched onto Truck-1"
        }}
      ]
    }}
    """

    try:
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.text.strip(), flags=re.MULTILINE).strip()
        result = json.loads(cleaned)
        ai_decisions = result.get("decisions", [])
    except Exception as e:
        print(f"AI fallback triggered: {e}", flush=True)
        # Sort by shortest distance first, then highest rating
        sorted_v = sorted(
            pending_vendors,
            key=lambda x: (ROUTE_DATA.get(x.location, ROUTE_DATA["Noida"])["distance_km"], -x.rating)
        )
        ai_decisions = [
            {"id": v.id, "name": v.name, "status": "accepted", "assigned_quantity": v.required_milk,
             "reason": f"Optimized for {v.location} route ({ROUTE_DATA.get(v.location, ROUTE_DATA['Noida'])['distance_km']} km)"}
            for v in sorted_v
        ]
        result = {"summary": "Allocated orders by shortest route distance and truck capacity."}

    remaining_stock = total_available
    final_decisions = []

    for dec in ai_decisions:
        v = db.query(Vendor).filter(Vendor.id == dec.get("id")).first()
        if not v:
            continue

        status_val = str(dec.get("status", "rejected")).lower()
        req_qty = min(float(dec.get("assigned_quantity", v.required_milk)), v.required_milk)
        r_key, r_info = get_route_info(v.location)

        if status_val == "accepted" and remaining_stock > 0:
            qty_to_try = min(req_qty, remaining_stock)
            truck, loaded_qty = allocate_truck_for_order(db, r_key, qty_to_try)

            if truck and loaded_qty > 0:
                v.status = "accepted"
                v.assigned_quantity = loaded_qty
                v.truck_name = truck.name
                v.eta = f"{r_info['eta']} ({r_info['distance_km']} km)"
                v.notification_time = datetime.utcnow()

                deduct_inventory_fefo(db, loaded_qty)
                remaining_stock = round(remaining_stock - loaded_qty, 2)

                final_decisions.append({
                    "id": v.id,
                    "name": v.name,
                    "status": "accepted",
                    "assigned_quantity": loaded_qty,
                    "truck": truck.name,
                    "eta": v.eta,
                    "reason": f"{dec.get('reason', 'Route matched')} | Assigned {truck.name} ({r_info['distance_km']}km, ETA {r_info['eta']})"
                })
            else:
                v.status = "rejected"
                v.assigned_quantity = 0.0
                v.notification_time = datetime.utcnow()
                final_decisions.append({
                    "id": v.id, "name": v.name, "status": "rejected", "assigned_quantity": 0.0,
                    "reason": f"Rejected: No truck capacity left for {r_key} route (30L max/truck)"
                })
        else:
            v.status = "rejected"
            v.assigned_quantity = 0.0
            v.notification_time = datetime.utcnow()
            final_decisions.append({
                "id": v.id, "name": v.name, "status": "rejected", "assigned_quantity": 0.0,
                "reason": dec.get("reason", "Insufficient inventory / lower route priority")
            })

    db.commit()
    return {
        "summary": result.get("summary", "Processed orders with FEFO stock & 30L truck route batching."),
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
    DISTANCES FROM NOIDA HUB: Noida = 8km (~20 mins), Delhi = 25km (~45 mins), Gurugram = 50km (~90 mins).
    INVENTORY: {inv.milk_0days}L (0d), {inv.milk_1day}L (1d), {inv.milk_2days}L (2d)
    TRUCK FLEET (30L max each): {json.dumps(trucks)}
    VENDORS: {json.dumps(vendors)}
    MANAGER QUERY: "{query.query}"

    Respond ONLY with valid JSON:
    {{
      "vendor": "exact vendor name",
      "reasoning": "mention exact km distance, route batching, and rating",
      "quantity": 20.0,
      "delivery_time": "~20 mins (8 km)"
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
            "delivery_time": result.get("delivery_time", "~20 mins"),
        }
    except Exception:
        top = vendors[0]
        return {"vendor": top["name"], "reasoning": f"Located in {top['location']} ({top['distance_km']} km).", "quantity": top["required_milk"], "delivery_time": "~30 mins"}