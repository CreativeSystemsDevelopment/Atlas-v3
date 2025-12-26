"""
Extraction Service for orchestrating schematic extraction workflow.
Handles sequential page processing with streaming results.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Generator, Tuple

logger = logging.getLogger(__name__)

from sqlalchemy.orm import Session

from config import Config
from models import (
    SchematicFile, SchematicPage, Component, Connection, 
    WireLabel, Continuation, ExtractionError, ExtractionStatus
)
from .gemini_service import GeminiService
from .pdf_processor import PDFProcessor


class ExtractionEvent:
    """Event types for streaming extraction progress."""
    PROGRESS = "progress"
    PAGE_MAPPING = "page_mapping"
    COMPONENT = "component"
    CONNECTION = "connection"
    WIRE_LABEL = "wire_label"
    CONTINUATION = "continuation"
    VALIDATION = "validation"
    ERROR = "error"
    COMPLETE = "complete"


class ExtractionResult:
    """Result container for streaming events."""
    
    def __init__(
        self,
        event_type: str,
        data: Dict[str, Any],
        seq: int,
        schematic_file_id: int
    ):
        self.type = event_type
        self.data = data
        self.seq = seq
        self.schematic_file_id = schematic_file_id
        self.timestamp = datetime.utcnow().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "data": self.data,
            "seq": self.seq,
            "schematic_file_id": self.schematic_file_id,
            "timestamp": self.timestamp
        }
    
    def to_sse(self) -> str:
        """Format as Server-Sent Event."""
        return f"data: {json.dumps(self.to_dict())}\n\n"


class ExtractionService:
    """
    Service for orchestrating schematic extraction.
    
    Features:
    - Sequential page processing (MVP)
    - Context caching (single upload)
    - Streaming results
    - Per-page retry with backoff
    - Resume capability
    """
    
    def __init__(self, db: Session):
        """
        Initialize extraction service.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
        self.gemini = GeminiService()
        self._seq_counter = 0
        self._cancelled = False
    
    def cancel(self):
        """Cancel ongoing extraction."""
        self._cancelled = True
    
    def extract_schematic(
        self,
        schematic_file: SchematicFile,
        pdf_page_indices: Optional[List[int]] = None,
        context_page_indices: Optional[List[int]] = None
    ) -> Generator[ExtractionResult, None, None]:
        """
        Extract components, connections, and wire labels from schematic.
        
        Args:
            schematic_file: SchematicFile database record
            pdf_page_indices: List of 0-based page indices to process (default: MVP pages)
            context_page_indices: Context pages for instructions/legend (default: [1, 2])
            
        Yields:
            ExtractionResult events for streaming
        """
        self._seq_counter = 0
        self._cancelled = False
        
        # Use defaults if not provided
        if pdf_page_indices is None:
            pdf_page_indices = Config.MVP_PDF_PAGES
        if context_page_indices is None:
            context_page_indices = Config.DEFAULT_CONTEXT_PAGES
        
        pdf_path = Path(schematic_file.filepath)
        
        # Update status
        schematic_file.extraction_status = ExtractionStatus.IN_PROGRESS
        schematic_file.extraction_started_at = datetime.utcnow()
        self.db.commit()
        
        yield self._emit(ExtractionEvent.PROGRESS, {
            "status": "starting",
            "message": "Starting extraction...",
            "pages_total": len(pdf_page_indices),
            "pages_processed": 0
        }, schematic_file.id)
        
        try:
            with PDFProcessor(pdf_path) as processor:
                # Step 1: Upload PDF to Gemini (once)
                yield self._emit(ExtractionEvent.PROGRESS, {
                    "status": "uploading",
                    "message": "Uploading PDF to Gemini API..."
                }, schematic_file.id)
                
                file_uri = self.gemini.upload_file(pdf_path, display_name=schematic_file.filename)
                schematic_file.gemini_file_uri = file_uri
                self.db.commit()
                
                # Step 2: Extract context text
                context_text = processor.extract_context_pages_text(
                    instructions_page=context_page_indices[0] if len(context_page_indices) > 0 else 1,
                    legend_page=context_page_indices[1] if len(context_page_indices) > 1 else 2
                )
                
                # Step 3: Detect page numbers using Gemini (all at once)
                yield self._emit(ExtractionEvent.PROGRESS, {
                    "status": "detecting_pages",
                    "message": f"Identifying schematic page numbers for {len(pdf_page_indices)} pages..."
                }, schematic_file.id)
                
                # Use Gemini to detect all page numbers in one call
                try:
                    page_mapping = self.gemini.detect_all_page_numbers(file_uri, pdf_page_indices)
                    logger.info(f"Page mapping detected: {page_mapping}")
                except Exception as e:
                    logger.error(f"Gemini page detection failed: {e}")
                    # Fallback: no page numbers detected
                    page_mapping = {
                        idx: {
                            "schematic_page_number": None,
                            "schematic_total": None,
                            "dwg_no": None,
                            "drawing_title": None,
                            "confidence": None,
                            "raw_text": None
                        } for idx in pdf_page_indices
                    }
                
                # Store page mappings
                for pdf_idx, meta in page_mapping.items():
                    schematic_num = meta.get("schematic_page_number") if meta else None
                    width, height = processor.get_page_dimensions(pdf_idx)
                    page = SchematicPage(
                        schematic_file_id=schematic_file.id,
                        pdf_page_index=pdf_idx,
                        schematic_page_number=schematic_num,
                        schematic_total=meta.get("schematic_total") if meta else None,
                        dwg_no=meta.get("dwg_no") if meta else None,
                        drawing_title=meta.get("drawing_title") if meta else None,
                        width=width,
                        height=height,
                        detection_confidence=meta.get("confidence") if meta and meta.get("confidence") is not None else (1.0 if schematic_num else 0.5),
                        is_processed=False
                    )
                    self.db.add(page)
                self.db.commit()
                
                # Emit page mapping to frontend
                mapping_payload = []
                for pdf_idx, meta in page_mapping.items():
                    mapping_payload.append({
                        "pdf_page_index": pdf_idx,
                        "schematic_page_number": meta.get("schematic_page_number") if meta else None,
                        "schematic_total": meta.get("schematic_total") if meta else None,
                        "dwg_no": meta.get("dwg_no") if meta else None,
                        "drawing_title": meta.get("drawing_title") if meta else None,
                        "confidence": meta.get("confidence") if meta else None,
                        "raw_text": meta.get("raw_text") if meta else None
                    })
                
                yield self._emit(ExtractionEvent.PAGE_MAPPING, {
                    "pages": mapping_payload
                }, schematic_file.id)
                
                # Step 4: Process each page sequentially
                pages_processed = 0
                for pdf_idx in pdf_page_indices:
                    if self._cancelled:
                        yield self._emit(ExtractionEvent.PROGRESS, {
                            "status": "cancelled",
                            "message": "Extraction cancelled by user"
                        }, schematic_file.id)
                        schematic_file.extraction_status = ExtractionStatus.CANCELLED
                        self.db.commit()
                        return
                    
                    meta = page_mapping.get(pdf_idx) or {}
                    schematic_num = meta.get("schematic_page_number")
                    
                    yield self._emit(ExtractionEvent.PROGRESS, {
                        "status": "extracting",
                        "message": f"Extracting page {pdf_idx + 1} (schematic page {schematic_num or '?'})...",
                        "current_page": pdf_idx,
                        "schematic_page": schematic_num,
                        "pages_total": len(pdf_page_indices),
                        "pages_processed": pages_processed,
                        "percent": int((pages_processed / len(pdf_page_indices)) * 100)
                    }, schematic_file.id)
                    
                    # Extract with retry
                    try:
                        for result in self._extract_page(
                            schematic_file=schematic_file,
                            file_uri=file_uri,
                            pdf_page_index=pdf_idx,
                            schematic_page_number=schematic_num,
                            context_text=context_text,
                            page_mapping=page_mapping
                        ):
                            yield result
                        
                        # Mark page as processed
                        page_record = self.db.query(SchematicPage).filter_by(
                            schematic_file_id=schematic_file.id,
                            pdf_page_index=pdf_idx
                        ).first()
                        if page_record:
                            page_record.is_processed = True
                        
                        pages_processed += 1
                        schematic_file.total_pages_processed = pages_processed
                        self.db.commit()
                        
                    except Exception as e:
                        error = ExtractionError(
                            schematic_file_id=schematic_file.id,
                            pdf_page_index=pdf_idx,
                            error_type="extraction_error",
                            error_message=str(e),
                            error_details={"page": pdf_idx}
                        )
                        self.db.add(error)
                        self.db.commit()
                        
                        yield self._emit(ExtractionEvent.ERROR, {
                            "page": pdf_idx,
                            "error": str(e)
                        }, schematic_file.id)
                
                # Step 5: Complete
                schematic_file.extraction_status = ExtractionStatus.COMPLETED
                schematic_file.extraction_completed_at = datetime.utcnow()
                self.db.commit()
                
                # Get totals
                total_components = self.db.query(Component).filter_by(
                    schematic_file_id=schematic_file.id
                ).count()
                total_connections = self.db.query(Connection).filter_by(
                    schematic_file_id=schematic_file.id
                ).count()
                total_wire_labels = self.db.query(WireLabel).filter_by(
                    schematic_file_id=schematic_file.id
                ).count()
                
                yield self._emit(ExtractionEvent.COMPLETE, {
                    "status": "completed",
                    "pages_processed": pages_processed,
                    "total_components": total_components,
                    "total_connections": total_connections,
                    "total_wire_labels": total_wire_labels
                }, schematic_file.id)
                
        except Exception as e:
            schematic_file.extraction_status = ExtractionStatus.FAILED
            self.db.commit()
            
            yield self._emit(ExtractionEvent.ERROR, {
                "status": "failed",
                "error": str(e)
            }, schematic_file.id)
            raise
    
    def _extract_page(
        self,
        schematic_file: SchematicFile,
        file_uri: str,
        pdf_page_index: int,
        schematic_page_number: Optional[int],
        context_text: str,
        page_mapping: Dict[int, Optional[int]]
    ) -> Generator[ExtractionResult, None, None]:
        """
        Extract data from a single page.
        
        Yields extraction results for each component, connection, and wire label.
        """
        # Call Gemini API
        extraction_data = self.gemini.extract_page(
            file_uri=file_uri,
            pdf_page_index=pdf_page_index,
            context_text=context_text,
            page_mapping=page_mapping
        )
        
        # Process components
        for comp_data in extraction_data.get("components", []):
            component = Component(
                schematic_file_id=schematic_file.id,
                symbol=comp_data.get("symbol"),
                name=comp_data.get("name"),
                mark=comp_data.get("mark", "UNKNOWN"),
                type=comp_data.get("type"),
                pdf_page_index=pdf_page_index,
                schematic_page_number=schematic_page_number,
                x=comp_data.get("x"),
                y=comp_data.get("y"),
                width=comp_data.get("width"),
                height=comp_data.get("height"),
                description=comp_data.get("description")
            )
            self.db.add(component)
            self.db.flush()  # Get ID
            
            yield self._emit(ExtractionEvent.COMPONENT, {
                "id": component.id,
                **component.to_dict()
            }, schematic_file.id)
        
        # Process connections
        for conn_data in extraction_data.get("connections", []):
            connection = Connection(
                schematic_file_id=schematic_file.id,
                from_component_mark=conn_data.get("from_component_mark"),
                to_component_mark=conn_data.get("to_component_mark"),
                wire_label=conn_data.get("wire_label"),
                terminal_from=conn_data.get("terminal_from"),
                terminal_to=conn_data.get("terminal_to"),
                pdf_page_index=pdf_page_index,
                schematic_page_number=schematic_page_number,
                path_coordinates=conn_data.get("path"),
                is_external=conn_data.get("is_external", False)
            )
            self.db.add(connection)
            self.db.flush()
            
            yield self._emit(ExtractionEvent.CONNECTION, {
                "id": connection.id,
                **connection.to_dict()
            }, schematic_file.id)
        
        # Process wire labels
        for label_data in extraction_data.get("wire_labels", []):
            wire_label = WireLabel(
                schematic_file_id=schematic_file.id,
                label=label_data.get("label", "?"),
                pdf_page_index=pdf_page_index,
                schematic_page_number=schematic_page_number,
                x=label_data.get("x"),
                y=label_data.get("y")
            )
            self.db.add(wire_label)
            self.db.flush()
            
            yield self._emit(ExtractionEvent.WIRE_LABEL, {
                "id": wire_label.id,
                **wire_label.to_dict()
            }, schematic_file.id)
        
        # Process continuations
        for cont_data in extraction_data.get("continuations", []):
            continuation = Continuation(
                schematic_file_id=schematic_file.id,
                from_component_mark=cont_data.get("from_component_mark"),
                pdf_page_index=pdf_page_index,
                schematic_page_number=schematic_page_number,
                to_page_hint=cont_data.get("to_page_hint"),
                direction=cont_data.get("direction"),
                is_external=True
            )
            self.db.add(continuation)
            self.db.flush()
            
            yield self._emit(ExtractionEvent.CONTINUATION, {
                "id": continuation.id,
                "from_component_mark": continuation.from_component_mark,
                "to_page_hint": continuation.to_page_hint,
                "direction": continuation.direction
            }, schematic_file.id)
        
        self.db.commit()
    
    def _emit(
        self,
        event_type: str,
        data: Dict[str, Any],
        schematic_file_id: int
    ) -> ExtractionResult:
        """Create an extraction result event."""
        self._seq_counter += 1
        return ExtractionResult(
            event_type=event_type,
            data=data,
            seq=self._seq_counter,
            schematic_file_id=schematic_file_id
        )
    
    def resolve_component_references(self, schematic_file_id: int):
        """
        Resolve component marks to component IDs in connections.
        
        Should be called after extraction is complete.
        """
        # Get all components for this file
        components = self.db.query(Component).filter_by(
            schematic_file_id=schematic_file_id
        ).all()
        
        # Create mark -> id mapping
        mark_to_id = {}
        for comp in components:
            key = (comp.mark, comp.pdf_page_index)
            mark_to_id[key] = comp.id
            # Also add without page for cross-page resolution
            if comp.mark not in mark_to_id:
                mark_to_id[comp.mark] = comp.id
        
        # Update connections
        connections = self.db.query(Connection).filter_by(
            schematic_file_id=schematic_file_id
        ).all()
        
        for conn in connections:
            if conn.from_component_mark:
                # Try page-specific first
                key = (conn.from_component_mark, conn.pdf_page_index)
                if key in mark_to_id:
                    conn.from_component_id = mark_to_id[key]
                elif conn.from_component_mark in mark_to_id:
                    conn.from_component_id = mark_to_id[conn.from_component_mark]
            
            if conn.to_component_mark:
                key = (conn.to_component_mark, conn.pdf_page_index)
                if key in mark_to_id:
                    conn.to_component_id = mark_to_id[key]
                elif conn.to_component_mark in mark_to_id:
                    conn.to_component_id = mark_to_id[conn.to_component_mark]
        
        self.db.commit()

