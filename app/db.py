# app/db.py (or db.py)
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Vendor(Base):
    __tablename__ = "vendors"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, nullable=False)
    location = Column(String, nullable=False) # Noida, Delhi, or Gurugram
    required_milk = Column(Float, nullable=False)
    rating = Column(Float, default=4.0)
    status = Column(String, default="pending") # pending, accepted, rejected
    assigned_quantity = Column(Float, nullable=True)
    truck_name = Column(String, nullable=True) # e.g., "Truck-1 (DL-1M-101)"
    eta = Column(String, nullable=True)        # e.g., "~45 mins (25 km)"
    created_at = Column(DateTime, default=datetime.utcnow)
    notification_time = Column(DateTime, nullable=True)

class Truck(Base):
    __tablename__ = "trucks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, nullable=False)          # e.g., "Truck-1 (DL-1M-101)"
    capacity = Column(Float, default=30.0)         # Max 30L per route
    current_load = Column(Float, default=0.0)      # Current loaded milk
    assigned_route = Column(String, nullable=True) # "Noida", "Delhi", "Gurugram", or None
    status = Column(String, default="available")   # "available" or "in_transit"

class Inventory(Base):
    __tablename__ = "inventory"

    id = Column(String, primary_key=True, default="current")
    milk_0days = Column(Float, default=30.0)
    milk_1day = Column(Float, default=10.0)
    milk_2days = Column(Float, default=5.0)
    office = Column(String, default="Noida")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()