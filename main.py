import json
import os
import sys
import re
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

# --- 1. DIRECTORY PATHS ---
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "app" / "static"
TEMPLATE_DIR = BASE_DIR / "app" / "templates"

# --- 2. FASTAPI APP INITIALIZATION ---
app = FastAPI()

# --- 3. MIDDLEWARE ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 4. STATIC FILES MOUNT ---
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# --- 5. PYDANTIC MODELS ---
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
    status: str = "accepted"  # "accepted" or "rejected"
    quantity: float = 0.0

class AutoHandleRequest(BaseModel):
    inventory: dict
    weather: str = "Rainy, 22°C"

# --- 6. ROUTES ---

@app.get("/")
async def home():
    index_file = TEMPLATE_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_file)

@app.get("/vendor")
async def vendor_page():
    vendor_file = TEMPLATE_DIR / "vendor.html"
    if not vendor_file.exists():
        raise HTTPException(status_code=404, detail="vendor.html not found")
    return FileResponse(vendor_file)

@app.get("/api/inventory")
def get_inventory(db: Session = Depends(get_db)):
    inv = db.query(Inventory).filter(Inventory.id == "current").first()
    if inv:
        return {
            "milk_0days": inv.milk_0days,
            "milk_1day": inv.milk_1day,
            "milk_2days": inv.milk_2days,
            "office": inv.office,
        }
    return {
        "milk_0days": 30.0,
        "milk_1day": 10.0,
        "milk_2days": 5.0,
        "office": "Noida",
    }

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

# MANUAL ACCEPT / REJECT FROM BMC DASHBOARD
@app.post("/api/notify/{vendor_id}")
def send_notification(vendor_id: int, payload: NotifyRequest, db: Session = Depends(get_db)):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    vendor.status = payload.status  # "accepted" or "rejected"
    vendor.assigned_quantity = payload.quantity if payload.status == "accepted" else 0.0
    vendor.notification_time = datetime.utcnow()
    db.commit()
    db.refresh(vendor)

    print(f"✅ BMC Manual Decision -> Vendor '{vendor.name}' (ID: {vendor.id}): {vendor.status.upper()} ({vendor.assigned_quantity}L)", flush=True)
    return {"success": True, "status": vendor.status, "vendor_id": vendor.id}

# AUTOMATICALLY HANDLE ALL PENDING ORDERS BY AI
@app.post("/api/ai/handle-orders")
def ai_handle_all_orders(payload: AutoHandleRequest, db: Session = Depends(get_db)):
    pending_vendors = db.query(Vendor).filter(Vendor.status == "pending").all()
    if not pending_vendors:
        return {
            "summary": "No pending vendor orders to process!",
            "decisions": []
        }

    inv = payload.inventory
    total_available = 30.0 + float(inv.get("milk_1day", 10)) + float(inv.get("milk_2days", 5))

    vendors_list = [
        {
            "id": v.id,
            "name": v.name,
            "location": v.location,
            "required_milk": v.required_milk,
            "rating": v.rating
        }
        for v in pending_vendors
    ]

    prompt = f"""
    You are an automated BMC Milk Allocation AI.
    TOTAL AVAILABLE MILK INVENTORY: {total_available}L (30L urgent 0-day, {inv.get('milk_1day', 10)}L 1-day, {inv.get('milk_2days', 5)}L 2-day)
    BMC OFFICE: Noida | WEATHER: {payload.weather}

    PENDING VENDOR REQUESTS:
    {json.dumps(vendors_list, indent=2)}

    TASK:
    Automatically decide which vendor orders to "accepted" and which to "rejected" based on:
    1. Total available milk ({total_available}L). Do not allocate more milk than available.
    2. Prioritize higher rating, proximity to Noida, and matching quantity to prevent spoilage.
    3. Every vendor in PENDING VENDOR REQUESTS must receive a decision ("accepted" or "rejected").

    Respond ONLY with a valid JSON object matching this exact format:
    {{
      "summary": "Overall explanation of how milk was allocated and why",
      "decisions": [
        {{
          "id": 1,
          "name": "vendor name",
          "status": "accepted",
          "assigned_quantity": 25.0,
          "reason": "High rating and matches urgent inventory"
        }}
      ]
    }}
    """

    try:
        print("[AI AUTO-HANDLE] Calling gemini-2.5-flash to process all pending orders...", flush=True)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw_text = response.text.strip()
        print(f"[AI AUTO-HANDLE] Raw output:\n{raw_text}", flush=True)

        cleaned_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.MULTILINE).strip()
        result = json.loads(cleaned_text)
        decisions = result.get("decisions", [])

        # Apply AI decisions directly to the database
        for dec in decisions:
            v = db.query(Vendor).filter(Vendor.id == dec.get("id")).first()
            if v:
                status_val = str(dec.get("status", "rejected")).lower()
                if status_val not in ["accepted", "rejected"]:
                    status_val = "rejected"
                v.status = status_val
                v.assigned_quantity = float(dec.get("assigned_quantity", v.required_milk)) if status_val == "accepted" else 0.0
                v.notification_time = datetime.utcnow()

        db.commit()
        return {
            "summary": result.get("summary", "Processed all pending orders."),
            "decisions": decisions
        }

    except Exception as e:
        print(f"\n❌ [AI AUTO-HANDLE ERROR]: {type(e).__name__}: {str(e)}", file=sys.stderr, flush=True)
        # Smart fallback if AI call fails: sort by rating and allocate until stock runs out
        remaining = total_available
        fallback_decisions = []
        for v in sorted(pending_vendors, key=lambda x: x.rating, reverse=True):
            if remaining >= v.required_milk:
                v.status = "accepted"
                v.assigned_quantity = v.required_milk
                remaining -= v.required_milk
                reason = "Accepted (High rating & sufficient stock)"
            elif remaining > 0:
                v.status = "accepted"
                v.assigned_quantity = remaining
                remaining = 0
                reason = "Partially accepted based on remaining stock"
            else:
                v.status = "rejected"
                v.assigned_quantity = 0.0
                reason = "Rejected (Insufficient inventory)"
            v.notification_time = datetime.utcnow()
            fallback_decisions.append({
                "id": v.id,
                "name": v.name,
                "status": v.status,
                "assigned_quantity": v.assigned_quantity,
                "reason": reason
            })
        db.commit()
        return {
            "summary": f"Processed {len(pending_vendors)} orders using priority rules.",
            "decisions": fallback_decisions
        }

# SINGLE QUERY AI CHAT ADVISOR
@app.post("/api/ai/recommend")
def ai_recommend(query: ManagerQuery, db: Session = Depends(get_db)):
    inventory = query.inventory
    vendors = get_vendors(db)

    if not vendors:
        return {
            "vendor": "No Vendors Available",
            "reasoning": "Please register at least one vendor first in the vendor dashboard.",
            "quantity": 0,
            "delivery_time": "N/A"
        }

    prompt = f"""
    You are a milk supply chain optimization assistant.
    INVENTORY: 30L(0d), {inventory.get('milk_1day', 0)}L(1d), {inventory.get('milk_2days', 0)}L(2d)
    OFFICE: Noida | WEATHER: {query.weather}

    REGISTERED VENDORS:
    {json.dumps(vendors, indent=2)}

    MANAGER QUERY: "{query.query}"

    Respond with ONLY a valid JSON object matching these exact keys:
    {{
      "vendor": "exact vendor name from list",
      "reasoning": "concise explanation for selection",
      "quantity": 25.0,
      "delivery_time": "14:30"
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

        return {
            "vendor": result.get("vendor", vendors[0]["name"]),
            "reasoning": result.get("reasoning", "Recommended based on demand and rating."),
            "quantity": float(result.get("quantity", vendors[0]["required_milk"])),
            "delivery_time": result.get("delivery_time", "11:00 AM"),
        }
    except Exception as e:
        top_vendor = sorted(vendors, key=lambda v: v.get("rating", 0), reverse=True)[0]
        return {
            "vendor": top_vendor["name"],
            "reasoning": f"Selected highest rated vendor ({top_vendor['rating']}★).",
            "quantity": top_vendor["required_milk"],
            "delivery_time": "10:30 AM"
        }