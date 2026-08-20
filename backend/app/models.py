from sqlalchemy import (
    Column, Integer, String, Text, Numeric, DateTime, ForeignKey, func
)
from sqlalchemy.orm import relationship
from .database import Base


class Auction(Base):
    __tablename__ = "auctions"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    source_url = Column(String)
    closing_date = Column(DateTime)

    lots = relationship("Lot", back_populates="auction")


class Lot(Base):
    __tablename__ = "lots"

    id = Column(Integer, primary_key=True)
    lot_id = Column(String, unique=True, nullable=False, index=True)  # hibid lot id
    auction_id = Column(Integer, ForeignKey("auctions.id"))
    title = Column(String, nullable=False)
    category = Column(String, index=True)
    description = Column(Text)
    current_bid = Column(Numeric)
    next_bid = Column(Numeric)
    lot_link = Column(String)
    thumbnail_url = Column(String)
    created_at = Column(DateTime, server_default=func.now())

    auction = relationship("Auction", back_populates="lots")
    enrichment = relationship("Enrichment", back_populates="lot", uselist=False)


class Enrichment(Base):
    __tablename__ = "enrichment"

    id = Column(Integer, primary_key=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), unique=True, nullable=False)

    status = Column(String, default="pending", index=True)  # pending | success | failed
    bolo_brand = Column(String)
    bolo_category = Column(String)
    bolo_tier = Column(String)
    target_buy_price = Column(Numeric)
    confidence = Column(String)  # strong | weak
    notes = Column(Text)
    error_message = Column(Text)
    last_attempted_at = Column(DateTime)

    lot = relationship("Lot", back_populates="enrichment")
