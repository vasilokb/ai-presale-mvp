from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Presale(Base):
    __tablename__ = "presales"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    files = relationship("File", back_populates="presale", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="presale", cascade="all, delete-orphan")


class File(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    presale_id: Mapped[str] = mapped_column(String(36), ForeignKey("presales.id"), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    presale = relationship("Presale", back_populates="files")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    presale_id: Mapped[str] = mapped_column(String(36), ForeignKey("presales.id"), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    params_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    presale = relationship("Presale", back_populates="documents")
    results = relationship("Result", back_populates="document", cascade="all, delete-orphan")


class Result(Base):
    __tablename__ = "results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_model: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    raw_llm_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    document = relationship("Document", back_populates="results")
