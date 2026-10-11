import json
import os
from datetime import datetime
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

# Import Vertex AI Gemini client and Cloud SQL setup
from client.vertex_client import client
from db import get_db, Vendor, Inventory

load_dotenv()

app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Request Models
class VendorRequest(BaseModel):
    name: str
    location: str
    required_milk: float
    rating: float = 4.0

class ManagerQuery(BaseModel):
    query: str
    inventory: dict
    weather: str = ""

@app.get("/")
async def home():
    return FileResponse("app/templates/index.html")

# --- BMC MANAGER DASHBOARD ---

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

# --- VENDOR REGISTRATION ---

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

# --- AI VENDOR RECOMMENDATION (Google Gemini AI via Vertex) ---

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
        # Call Gemini using the official Google GenAI SDK
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.3,
            },
        )
        result = json.loads(response.text)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini AI processing error: {str(e)}")

# --- SEND NOTIFICATION ---

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