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
    quantity: float = 30.0

class StatusUpdate(BaseModel):
    status: str

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

@app.post("/api/ai/recommend")
def ai_recommend(query: ManagerQuery, db: Session = Depends(get_db)):
    print(f"\n[AI] Received query: '{query.query}'", flush=True)

    inventory = query.inventory
    vendors = get_vendors(db)
    print(f"[AI] Active vendors in database: {len(vendors)}", flush=True)

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

    Analyze step-by-step:
    1. Match quantity needs vs spoilage risk.
    2. Consider distance and weather impact.
    3. Vendor rating and fulfillment history.
    4. Choose exactly 1 vendor from REGISTERED VENDORS.

    Respond with ONLY a valid JSON object matching these exact keys:
    {{
      "vendor": "exact vendor name from list",
      "reasoning": "concise explanation for selection",
      "quantity": 25.0,
      "delivery_time": "14:30"
    }}
    """

    try:
        print("[AI] Requesting Gemini (gemini-1.5-flash)...", flush=True)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )

        raw_text = response.text.strip()
        print(f"[AI] Raw Gemini output:\n{raw_text}", flush=True)

        # Remove markdown fences like ```json ... ``` if present
        cleaned_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.MULTILINE).strip()
        result = json.loads(cleaned_text)

        output = {
            "vendor": result.get("vendor", vendors[0]["name"]),
            "reasoning": result.get("reasoning", "Recommended based on demand and rating."),
            "quantity": float(result.get("quantity", vendors[0]["required_milk"])),
            "delivery_time": result.get("delivery_time", "11:00 AM"),
        }
        print(f"[AI] Recommendation complete: {output}", flush=True)
        return output

    except Exception as e:
        print(f"\n❌ [AI ERROR]: {type(e).__name__}: {str(e)}", file=sys.stderr, flush=True)

        # Fallback to smart heuristic if API fails during demo
        top_vendor = sorted(vendors, key=lambda v: v.get("rating", 0), reverse=True)[0]
        return {
            "vendor": top_vendor["name"],
            "reasoning": f"Heuristic fallback: Selected highest rated vendor ({top_vendor['rating']}★). (AI Error: {str(e)[:70]})",
            "quantity": top_vendor["required_milk"],
            "delivery_time": "10:30 AM"
        }

@app.post("/api/notify/{vendor_id}")
def send_notification(vendor_id: int, payload: NotifyRequest, db: Session = Depends(get_db)):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    vendor.status = "notified"
    vendor.assigned_quantity = payload.quantity
    vendor.notification_time = datetime.utcnow()
    db.commit()
    db.refresh(vendor)

    print(f"✅ Notified vendor '{vendor.name}' (ID: {vendor.id}) for {payload.quantity}L", flush=True)
    return {"success": True, "status": vendor.status, "vendor_id": vendor.id}

@app.post("/api/vendors/{vendor_id}/status")
def update_vendor_status(vendor_id: int, payload: StatusUpdate, db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Vendor not found")

    v.status = payload.status
    db.commit()
    print(f"🔄 Vendor '{v.name}' updated status to '{payload.status}'", flush=True)
    return {"success": True, "status": v.status}