"""
SQLAlchemy database models for Schematic Extraction MVP.
9 tables as specified in the plan.
"""
from datetime import datetime
from typing import Optional, List
import json

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, Text, 
    DateTime, ForeignKey, Index, UniqueConstraint, Enum as SQLEnum
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.types import TypeDecorator, TEXT

from config import Config

# Custom JSON type for SQLite
class JSONType(TypeDecorator):
    """Store JSON as TEXT in SQLite."""
    impl = TEXT
    cache_ok = True
    
    def process_bind_param(self, value, dialect):
        if value is not None:
            return json.dumps(value)
        return None
    
    def process_result_value(self, value, dialect):
        if value is not None:
            return json.loads(value)
        return None


# Enums
class ExtractionStatus:
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class ValidationStatus:
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"


class ValidationType:
    PAGE = "page"
    FULL_FILE = "full_file"
    COMPONENT = "component"
    CONNECTION = "connection"
    WIRE_LABEL = "wire_label"


# Base
Base = declarative_base()


# 1. Machines table
class Machine(Base):
    """Store machine/line identifiers."""
    __tablename__ = "machines"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    schematic_files = relationship("SchematicFile", back_populates="machine", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Machine(id={self.id}, name='{self.name}')>"


# 2. Schematic Files table
class SchematicFile(Base):
    """Track uploaded PDFs."""
    __tablename__ = "schematic_files"
    
    id = Column(Integer, primary_key=True)
    machine_id = Column(Integer, ForeignKey("machines.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    filepath = Column(String(500), nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)  # SHA-256
    context_pages = Column(JSONType)  # {"reading_instructions_page": 2, "legend_page": 3}
    gemini_file_uri = Column(String(500), nullable=True)
    extraction_status = Column(String(20), default=ExtractionStatus.PENDING)
    extraction_started_at = Column(DateTime, nullable=True)
    extraction_completed_at = Column(DateTime, nullable=True)
    total_pages_processed = Column(Integer, default=0)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    machine = relationship("Machine", back_populates="schematic_files")
    pages = relationship("SchematicPage", back_populates="schematic_file", cascade="all, delete-orphan")
    components = relationship("Component", back_populates="schematic_file", cascade="all, delete-orphan")
    connections = relationship("Connection", back_populates="schematic_file", cascade="all, delete-orphan")
    wire_labels = relationship("WireLabel", back_populates="schematic_file", cascade="all, delete-orphan")
    continuations = relationship("Continuation", back_populates="schematic_file", cascade="all, delete-orphan")
    extraction_errors = relationship("ExtractionError", back_populates="schematic_file", cascade="all, delete-orphan")
    validation_results = relationship("ValidationResult", back_populates="schematic_file", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<SchematicFile(id={self.id}, filename='{self.filename}', status='{self.extraction_status}')>"


# 3. Schematic Pages table (page mapping)
class SchematicPage(Base):
    """Store page mapping between PDF pages and schematic page numbers."""
    __tablename__ = "schematic_pages"
    
    id = Column(Integer, primary_key=True)
    schematic_file_id = Column(Integer, ForeignKey("schematic_files.id"), nullable=False)
    pdf_page_index = Column(Integer, nullable=False)  # 0-based
    schematic_page_number = Column(Integer, nullable=True)  # From title block, e.g., 1, 2, 3
    width = Column(Float, nullable=True)  # Page width in points
    height = Column(Float, nullable=True)  # Page height in points
    detection_confidence = Column(Float, default=1.0)
    is_processed = Column(Boolean, default=False)
    detected_at = Column(DateTime, default=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index("idx_schematic_pages_file_pdf", "schematic_file_id", "pdf_page_index"),
        Index("idx_schematic_pages_file_schematic", "schematic_file_id", "schematic_page_number"),
    )
    
    # Relationships
    schematic_file = relationship("SchematicFile", back_populates="pages")
    
    def __repr__(self):
        return f"<SchematicPage(pdf={self.pdf_page_index}, schematic={self.schematic_page_number})>"


# 4. Components table
class Component(Base):
    """Extracted components from schematic."""
    __tablename__ = "components"
    
    id = Column(Integer, primary_key=True)
    schematic_file_id = Column(Integer, ForeignKey("schematic_files.id"), nullable=False)
    symbol = Column(String(100), nullable=True)  # Component symbol type
    name = Column(String(255), nullable=True)  # Component name
    mark = Column(String(50), nullable=False, index=True)  # Component mark, e.g., "SOL-1"
    type = Column(String(100), nullable=True)  # Component type
    pdf_page_index = Column(Integer, nullable=False, index=True)  # 0-based
    schematic_page_number = Column(Integer, nullable=True, index=True)  # From title block
    x = Column(Float, nullable=True)
    y = Column(Float, nullable=True)
    width = Column(Float, nullable=True)
    height = Column(Float, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Unique constraint
    __table_args__ = (
        UniqueConstraint("schematic_file_id", "mark", "pdf_page_index", name="uq_component_mark_page"),
        Index("idx_components_file_page", "schematic_file_id", "pdf_page_index"),
    )
    
    # Relationships
    schematic_file = relationship("SchematicFile", back_populates="components")
    connections_from = relationship("Connection", foreign_keys="Connection.from_component_id", back_populates="from_component")
    connections_to = relationship("Connection", foreign_keys="Connection.to_component_id", back_populates="to_component")
    
    def __repr__(self):
        return f"<Component(id={self.id}, mark='{self.mark}', page={self.schematic_page_number})>"
    
    def to_dict(self):
        return {
            "id": self.id,
            "symbol": self.symbol,
            "name": self.name,
            "mark": self.mark,
            "type": self.type,
            "pdf_page_index": self.pdf_page_index,
            "schematic_page_number": self.schematic_page_number,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "description": self.description,
        }


# 5. Connections table
class Connection(Base):
    """Wire connections between components."""
    __tablename__ = "connections"
    
    id = Column(Integer, primary_key=True)
    schematic_file_id = Column(Integer, ForeignKey("schematic_files.id"), nullable=False)
    from_component_id = Column(Integer, ForeignKey("components.id"), nullable=True)
    to_component_id = Column(Integer, ForeignKey("components.id"), nullable=True)
    from_component_mark = Column(String(50), nullable=True)  # Original mark from extraction
    to_component_mark = Column(String(50), nullable=True)  # Original mark from extraction
    wire_label = Column(String(50), nullable=True, index=True)
    terminal_from = Column(String(20), nullable=True)
    terminal_to = Column(String(20), nullable=True)
    pdf_page_index = Column(Integer, nullable=False)
    schematic_page_number = Column(Integer, nullable=True)
    path_coordinates = Column(JSONType)  # [[x1, y1], [x2, y2], ...]
    is_external = Column(Boolean, default=False)  # Points to unprocessed page
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index("idx_connections_file_page", "schematic_file_id", "pdf_page_index"),
        Index("idx_connections_wire", "schematic_file_id", "wire_label"),
    )
    
    # Relationships
    schematic_file = relationship("SchematicFile", back_populates="connections")
    from_component = relationship("Component", foreign_keys=[from_component_id], back_populates="connections_from")
    to_component = relationship("Component", foreign_keys=[to_component_id], back_populates="connections_to")
    
    def __repr__(self):
        return f"<Connection(id={self.id}, wire='{self.wire_label}', from='{self.from_component_mark}', to='{self.to_component_mark}')>"
    
    def to_dict(self):
        return {
            "id": self.id,
            "from_component_mark": self.from_component_mark,
            "to_component_mark": self.to_component_mark,
            "wire_label": self.wire_label,
            "terminal_from": self.terminal_from,
            "terminal_to": self.terminal_to,
            "pdf_page_index": self.pdf_page_index,
            "schematic_page_number": self.schematic_page_number,
            "path_coordinates": self.path_coordinates,
            "is_external": self.is_external,
        }


# 6. Wire Labels table
class WireLabel(Base):
    """Wire labels with positions."""
    __tablename__ = "wire_labels"
    
    id = Column(Integer, primary_key=True)
    schematic_file_id = Column(Integer, ForeignKey("schematic_files.id"), nullable=False)
    label = Column(String(50), nullable=False, index=True)
    pdf_page_index = Column(Integer, nullable=False, index=True)
    schematic_page_number = Column(Integer, nullable=True, index=True)
    x = Column(Float, nullable=True)
    y = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index("idx_wire_labels_file_page", "schematic_file_id", "pdf_page_index"),
        Index("idx_wire_labels_label", "schematic_file_id", "label"),
    )
    
    # Relationships
    schematic_file = relationship("SchematicFile", back_populates="wire_labels")
    
    def __repr__(self):
        return f"<WireLabel(id={self.id}, label='{self.label}', page={self.schematic_page_number})>"
    
    def to_dict(self):
        return {
            "id": self.id,
            "label": self.label,
            "pdf_page_index": self.pdf_page_index,
            "schematic_page_number": self.schematic_page_number,
            "x": self.x,
            "y": self.y,
        }


# 7. Continuations table (external references)
class Continuation(Base):
    """Store continuation markers pointing to other pages."""
    __tablename__ = "continuations"
    
    id = Column(Integer, primary_key=True)
    schematic_file_id = Column(Integer, ForeignKey("schematic_files.id"), nullable=False)
    from_component_mark = Column(String(50), nullable=True)
    pdf_page_index = Column(Integer, nullable=False)
    schematic_page_number = Column(Integer, nullable=True)
    to_page_hint = Column(String(50), nullable=True)  # e.g., "→5" or "P.12"
    direction = Column(String(20), nullable=True)  # "to" or "from"
    is_external = Column(Boolean, default=True)  # Points to unprocessed page
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    schematic_file = relationship("SchematicFile", back_populates="continuations")
    
    def __repr__(self):
        return f"<Continuation(from='{self.from_component_mark}', to_page='{self.to_page_hint}')>"


# 8. Extraction Errors table
class ExtractionError(Base):
    """Track extraction errors for debugging and retry."""
    __tablename__ = "extraction_errors"
    
    id = Column(Integer, primary_key=True)
    schematic_file_id = Column(Integer, ForeignKey("schematic_files.id"), nullable=False)
    pdf_page_index = Column(Integer, nullable=True)
    error_type = Column(String(50), nullable=False)  # api_error, parsing_error, validation_error
    error_message = Column(Text, nullable=False)
    error_details = Column(JSONType)  # Additional context
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    schematic_file = relationship("SchematicFile", back_populates="extraction_errors")
    
    def __repr__(self):
        return f"<ExtractionError(type='{self.error_type}', page={self.pdf_page_index})>"


# 9. Validation Results table
class ValidationResult(Base):
    """Store validation results for QC tracking."""
    __tablename__ = "validation_results"
    
    id = Column(Integer, primary_key=True)
    schematic_file_id = Column(Integer, ForeignKey("schematic_files.id"), nullable=False)
    pdf_page_index = Column(Integer, nullable=True)  # Null for full-file validation
    validation_type = Column(String(20), nullable=False)  # page, full_file, component, connection, wire_label
    status = Column(String(10), nullable=False)  # pass, fail, warning
    confidence_score = Column(Float, nullable=True)  # 0.0 - 1.0
    discrepancies = Column(JSONType)  # List of found issues
    validated_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    schematic_file = relationship("SchematicFile", back_populates="validation_results")
    
    def __repr__(self):
        return f"<ValidationResult(type='{self.validation_type}', status='{self.status}')>"


# Database initialization
engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """Create all tables."""
    Base.metadata.create_all(engine)
    print(f"Database initialized at {Config.SQLALCHEMY_DATABASE_URI}")


def get_db():
    """Get database session. Use as context manager."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


if __name__ == "__main__":
    init_db()

