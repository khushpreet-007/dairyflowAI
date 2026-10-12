import json
import os
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Correct imports matching your 'app/' folder structure
from app.client.vertex_client import client
# If db.py is inside app/, use: from app.db import ...
# If db.py is in the root, change this to: from db import ...
try:
    from app.db import get_db, Vendor, Inventory
except ImportError:
    from db import get_db, Vendor, Inventory

load_dotenv()

# --- 1. DEFINE PATHS FIRST ---
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "app" / "static"
TEMPLATE_DIR = BASE_DIR / "app" / "templates"

# --- 2. CREATE THE FASTAPI APP INSTANCE ---
app = FastAPI()

# --- 3. ADD MIDDLEWARE ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 4. MOUNT STATIC FILES (Now 'app' is defined!) ---
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# --- 5. MODELS ---
class VendorRequest(BaseModel):
    name: str
    location: str
    required_milk: float
    rating: float = 4.0

class ManagerQuery(BaseModel):
    query: str
    inventory: dict
    weather: str = ""

# --- 6. ROUTES ---
@app.get("/")
async def home():
    index_file = TEMPLATE_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_file)

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
    inventory = query.inventory
    vendors = get_vendors(db)

    prompt = f"""
    INVENTORY: 30L(0d), {inventory.get('milk_1day', 0)}L(1d), {inventory.get('milk_2days', 0)}L(2d)
    OFFICE: Noida | WEATHER: {query.weather}

    VENDORS:
    {json.dumps(vendors, indent=2)}

    MANAGER ASKED: "{query.query}"

    ANALYZE step-by-step:
    1. Match quantity needs vs spoilage risk
    2. Consider distance + weather impact
    3. Vendor rating + history
    4. Recommend EXACTLY 1 vendor + delivery time

    Respond with a valid JSON object matching this schema:
    {{
      "reasoning": "your explanation",
      "vendor": "name",
      "quantity": 0.0,
      "delivery_time": "HH:MM"
    }}
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.3,
            },
        )
        return json.loads(response.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini AI processing error: {str(e)}")

@app.post("/api/notify/{vendor_id}")
def send_notification(vendor_id: int, quantity: float, db: Session = Depends(get_db)):
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    vendor.status = "notified"
    vendor.assigned_quantity = quantity
    vendor.notification_time = datetime.utcnow()
    db.commit()

    return {"success": True}

# 1. Vendor Page Route
@app.get("/vendor")
async def vendor_page():
    vendor_file = TEMPLATE_DIR / "vendor.html"
    if not vendor_file.exists():
        raise HTTPException(status_code=404, detail="vendor.html not found")
    return FileResponse(vendor_file)

# 2. Get Single Vendor Details
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

# 3. Model for status change
class StatusUpdate(BaseModel):
    status: str

# 4. Vendor Accepts or Rejects Notification
@app.post("/api/vendors/{vendor_id}/status")
def update_vendor_status(vendor_id: int, payload: StatusUpdate, db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Vendor not found")
    v.status = payload.status
    db.commit()
    return {"success": True, "status": v.status}