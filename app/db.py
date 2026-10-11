import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

load_dotenv()

# Example Cloud SQL connection string:
# postgresql://<DB_USER>:<DB_PASS>@<CLOUD_SQL_IP_OR_PROXY>/<DB_NAME>
# For local fallback during development, SQLite is used if DATABASE_URL is not provided
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
    location = Column(String, nullable=False)
    required_milk = Column(Float, nullable=False)
    rating = Column(Float, default=4.0)
    status = Column(String, default="pending")
    assigned_quantity = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    notification_time = Column(DateTime, nullable=True)

class Inventory(Base):
    __tablename__ = "inventory"

    id = Column(String, primary_key=True, default="current")
    milk_0days = Column(Float, default=30.0)
    milk_1day = Column(Float, default=10.0)
    milk_2days = Column(Float, default=5.0)
    office = Column(String, default="Noida")

# Create tables
Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()