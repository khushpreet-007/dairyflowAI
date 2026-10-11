from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import firestore
from pydantic import BaseModel
import openai
import os
from dotenv import load_dotenv
import requests
import json
from datetime import datetime, timedelta

load_dotenv()

# Firestore & OpenAI
db = firestore.Client()
openai.api_key = os.getenv("OPENAI_API_KEY")
app = FastAPI()

# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Models
class VendorRequest(BaseModel):
    name: str
    location: str
    required_milk: float
    rating: float = 4.0

class ManagerQuery(BaseModel):
    query: str
    inventory: dict
    weather: str = ""

# COLLECTIONS
VENDORS_COLLECTION = "vendors"
INVENTORY_COLLECTION = "inventory"

@app.get("/")
async def home():
    return FileResponse("app/templates/index.html")

# BMC MANAGER DASHBOARD
@app.get("/api/inventory")
async def get_inventory():
    doc = db.collection(INVENTORY_COLLECTION).document("current").get()
    return doc.to_dict() if doc.exists else {
        "milk_0days": 30,
        "milk_1day": 10, 
        "milk_2days": 5,
        "office": "Noida"
    }

@app.get("/api/vendors")
async def get_vendors():
    vendors = db.collection(VENDORS_COLLECTION).stream()
    return [{"id": v.id, **v.to_dict()} for v in vendors]

# VENDOR REGISTRATION (Vendor App hits this)
@app.post("/api/vendors/register")
async def register_vendor(vendor: VendorRequest):
    vendor_data = vendor.dict()
    vendor_data["timestamp"] = firestore.SERVER_TIMESTAMP
    vendor_data["status"] = "pending"
    
    doc_ref = db.collection(VENDORS_COLLECTION).add(vendor_data)
    return {"success": True, "vendor_id": doc_ref[1].id}

# AI VENDOR RECOMMENDATION (Gen AI Magic ✨)
@app.post("/api/ai/recommend")
async def ai_recommend(query: ManagerQuery):
    inventory = query.inventory
    vendors = [v for v in await get_vendors()]
    
    prompt = f"""
    INVENTORY: 30L(0d), {inventory['milk_1day']}L(1d), {inventory['milk_2days']}L(2d)
    OFFICE: Noida | WEATHER: {query.weather}
    
    VENDORS:
    {json.dumps(vendors, indent=2)}
    
    MANAGER ASKED: "{query.query}"
    
    ANALYZE step-by-step:
    1. Match quantity needs vs spoilage risk
    2. Consider distance + weather impact  
    3. Vendor rating + history
    4. Recommend EXACTLY 1 vendor + delivery time
    
    RESPOND in JSON: {{"reasoning": "your explanation", "vendor": "name", "quantity": X, "delivery_time": "HH:MM"}}
    """
    
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    
    result = json.loads(response.choices[0].message.content)
    return result

# SEND NOTIFICATION (Update vendor status)
@app.post("/api/notify/{vendor_id}")
async def send_notification(vendor_id: str, quantity: float):
    db.collection(VENDORS_COLLECTION).document(vendor_id).update({
        "status": "notified",
        "assigned_quantity": quantity,
        "notification_time": firestore.SERVER_TIMESTAMP
    })
    return {"success": True}