"""
Flask routes for Schematic Extraction MVP.
API endpoints and main UI.
"""
import os
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional

from flask import (
    Blueprint, request, jsonify, render_template, 
    Response, send_file, current_app
)
from werkzeug.utils import secure_filename
from sqlalchemy.orm import Session

from config import Config
from models import (
    Machine, SchematicFile, SchematicPage, Component, 
    Connection, WireLabel, ExtractionStatus, SessionLocal, init_db
)
from services import (
    GeminiService, PDFProcessor, ExtractionService, 
    ValidationService, OverlayService
)
from services.extraction_service import ExtractionEvent

# Create blueprints
api = Blueprint('api', __name__)
ui = Blueprint('ui', __name__)


def get_db() -> Session:
    """Get database session."""
    return SessionLocal()


def allowed_file(filename: str) -> bool:
    """Check if file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS


# =============================================================================
# Main UI Routes
# =============================================================================

@ui.route('/')
def index():
    """Main UI page."""
    return render_template('index.html')


# =============================================================================
# API Routes
# =============================================================================

@api.route('/upload', methods=['POST'])
def upload_pdf():
    """
    Upload a PDF file.
    
    Request:
        - file: PDF file
        - machine_name: Machine/line identifier
        
    Response:
        - schematic_file_id: ID of created record
        - is_duplicate: Whether file was already uploaded
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    if not allowed_file(file.filename):
        return jsonify({"error": "File type not allowed. Only PDF files are accepted."}), 400
    
    machine_name = request.form.get('machine_name', '').strip()
    if not machine_name:
        return jsonify({"error": "Machine name is required"}), 400
    
    # Sanitize filename
    filename = secure_filename(file.filename)
    
    # Calculate file hash
    file_content = file.read()
    file_hash = hashlib.sha256(file_content).hexdigest()
    file.seek(0)  # Reset file pointer
    
    db = get_db()
    try:
        # Get or create machine
        machine = db.query(Machine).filter_by(name=machine_name).first()
        if not machine:
            machine = Machine(name=machine_name)
            db.add(machine)
            db.commit()
        
        # Check for duplicate
        existing = db.query(SchematicFile).filter_by(file_hash=file_hash).first()
        if existing:
            return jsonify({
                "schematic_file_id": existing.id,
                "is_duplicate": True,
                "existing_machine": existing.machine.name,
                "message": f"This PDF was already uploaded for machine '{existing.machine.name}'"
            }), 200
        
        # Save file
        upload_dir = Config.UPLOADS_DIR / machine_name
        upload_dir.mkdir(parents=True, exist_ok=True)
        
        filepath = upload_dir / filename
        
        # Handle filename collision
        counter = 1
        while filepath.exists():
            name, ext = filename.rsplit('.', 1)
            filepath = upload_dir / f"{name}_{counter}.{ext}"
            counter += 1
        
        file.save(filepath)
        
        # Create database record
        schematic_file = SchematicFile(
            machine_id=machine.id,
            filename=filename,
            filepath=str(filepath),
            file_hash=file_hash,
            extraction_status=ExtractionStatus.PENDING
        )
        db.add(schematic_file)
        db.commit()
        
        return jsonify({
            "schematic_file_id": schematic_file.id,
            "is_duplicate": False,
            "filename": filename,
            "message": "File uploaded successfully"
        }), 201
        
    finally:
        db.close()


@api.route('/upload/<int:schematic_file_id>/replace', methods=['POST'])
def replace_upload(schematic_file_id: int):
    """
    Replace an existing upload with new extraction.
    Clears existing extraction data.
    """
    db = get_db()
    try:
        schematic_file = db.query(SchematicFile).get(schematic_file_id)
        if not schematic_file:
            return jsonify({"error": "Schematic file not found"}), 404
        
        # Clear existing data
        db.query(Component).filter_by(schematic_file_id=schematic_file_id).delete()
        db.query(Connection).filter_by(schematic_file_id=schematic_file_id).delete()
        db.query(WireLabel).filter_by(schematic_file_id=schematic_file_id).delete()
        db.query(SchematicPage).filter_by(schematic_file_id=schematic_file_id).delete()
        
        # Reset status
        schematic_file.extraction_status = ExtractionStatus.PENDING
        schematic_file.extraction_started_at = None
        schematic_file.extraction_completed_at = None
        schematic_file.total_pages_processed = 0
        
        db.commit()
        
        return jsonify({
            "schematic_file_id": schematic_file_id,
            "message": "Ready for re-extraction"
        }), 200
        
    finally:
        db.close()


@api.route('/extract', methods=['POST'])
def start_extraction():
    """
    Start extraction process.
    
    Request:
        - schematic_file_id: ID of schematic file
        - context_pages: Optional list of context page indices [instructions, legend]
        - pdf_pages: Optional list of PDF page indices to process
        
    Response:
        - extraction_id: ID for tracking
    """
    data = request.get_json()
    schematic_file_id = data.get('schematic_file_id')
    
    if not schematic_file_id:
        return jsonify({"error": "schematic_file_id is required"}), 400
    
    context_pages = data.get('context_pages', Config.DEFAULT_CONTEXT_PAGES)
    pdf_pages = data.get('pdf_pages', Config.MVP_PDF_PAGES)
    
    db = get_db()
    try:
        schematic_file = db.query(SchematicFile).get(schematic_file_id)
        if not schematic_file:
            return jsonify({"error": "Schematic file not found"}), 404
        
        # Store context pages
        schematic_file.context_pages = {
            "reading_instructions_page": context_pages[0] if len(context_pages) > 0 else 1,
            "legend_page": context_pages[1] if len(context_pages) > 1 else 2
        }
        db.commit()
        
        return jsonify({
            "schematic_file_id": schematic_file_id,
            "status": "ready",
            "stream_url": f"/api/extract/{schematic_file_id}/stream"
        }), 200
        
    finally:
        db.close()


@api.route('/extract/<int:schematic_file_id>/stream')
def stream_extraction(schematic_file_id: int):
    """
    Stream extraction progress using Server-Sent Events.
    
    Returns SSE stream with extraction events.
    """
    def generate():
        db = get_db()
        try:
            schematic_file = db.query(SchematicFile).get(schematic_file_id)
            if not schematic_file:
                yield f"data: {{'type': 'error', 'data': {{'error': 'Schematic file not found'}}}}\n\n"
                return
            
            # Get context pages
            context_pages = []
            if schematic_file.context_pages:
                context_pages = [
                    schematic_file.context_pages.get("reading_instructions_page", 1),
                    schematic_file.context_pages.get("legend_page", 2)
                ]
            else:
                context_pages = Config.DEFAULT_CONTEXT_PAGES
            
            # Start extraction
            service = ExtractionService(db)
            
            for result in service.extract_schematic(
                schematic_file=schematic_file,
                pdf_page_indices=Config.MVP_PDF_PAGES,
                context_page_indices=context_pages
            ):
                yield result.to_sse()
                
        except Exception as e:
            yield f"data: {{\"type\": \"error\", \"data\": {{\"error\": \"{str(e)}\"}}}}\n\n"
        finally:
            db.close()
    
    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@api.route('/extract/<int:schematic_file_id>/cancel', methods=['POST'])
def cancel_extraction(schematic_file_id: int):
    """Cancel an ongoing extraction."""
    # Note: In MVP, we can't truly cancel mid-stream
    # This sets status to cancelled for future checks
    db = get_db()
    try:
        schematic_file = db.query(SchematicFile).get(schematic_file_id)
        if not schematic_file:
            return jsonify({"error": "Schematic file not found"}), 404
        
        schematic_file.extraction_status = ExtractionStatus.CANCELLED
        db.commit()
        
        return jsonify({"message": "Cancellation requested"}), 200
    finally:
        db.close()


@api.route('/extraction-status/<int:schematic_file_id>')
def get_extraction_status(schematic_file_id: int):
    """Get current extraction status and summary."""
    db = get_db()
    try:
        schematic_file = db.query(SchematicFile).get(schematic_file_id)
        if not schematic_file:
            return jsonify({"error": "Schematic file not found"}), 404
        
        # Get counts
        component_count = db.query(Component).filter_by(
            schematic_file_id=schematic_file_id
        ).count()
        
        connection_count = db.query(Connection).filter_by(
            schematic_file_id=schematic_file_id
        ).count()
        
        wire_label_count = db.query(WireLabel).filter_by(
            schematic_file_id=schematic_file_id
        ).count()
        
        return jsonify({
            "schematic_file_id": schematic_file_id,
            "status": schematic_file.extraction_status,
            "pages_processed": schematic_file.total_pages_processed,
            "started_at": schematic_file.extraction_started_at.isoformat() if schematic_file.extraction_started_at else None,
            "completed_at": schematic_file.extraction_completed_at.isoformat() if schematic_file.extraction_completed_at else None,
            "counts": {
                "components": component_count,
                "connections": connection_count,
                "wire_labels": wire_label_count
            }
        }), 200
    finally:
        db.close()


@api.route('/search')
def search_components():
    """
    Search components by mark, name, or description.
    
    Query params:
        - q: Search query
        - schematic_file_id: Optional filter by file
        - limit: Max results (default 50)
    """
    query = request.args.get('q', '').strip()
    schematic_file_id = request.args.get('schematic_file_id', type=int)
    limit = request.args.get('limit', 50, type=int)
    
    if not query:
        return jsonify({"error": "Search query is required"}), 400
    
    db = get_db()
    try:
        q = db.query(Component)
        
        if schematic_file_id:
            q = q.filter(Component.schematic_file_id == schematic_file_id)
        
        # Search in mark, name, description
        search_filter = (
            Component.mark.ilike(f'%{query}%') |
            Component.name.ilike(f'%{query}%') |
            Component.description.ilike(f'%{query}%')
        )
        
        results = q.filter(search_filter).limit(limit).all()
        
        return jsonify({
            "query": query,
            "count": len(results),
            "results": [c.to_dict() for c in results]
        }), 200
    finally:
        db.close()


@api.route('/components')
def list_components():
    """
    List all components with pagination.
    
    Query params:
        - schematic_file_id: Filter by file (required)
        - page: Page number (default 1)
        - per_page: Items per page (default 50)
    """
    schematic_file_id = request.args.get('schematic_file_id', type=int)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    
    if not schematic_file_id:
        return jsonify({"error": "schematic_file_id is required"}), 400
    
    db = get_db()
    try:
        total = db.query(Component).filter_by(
            schematic_file_id=schematic_file_id
        ).count()
        
        components = db.query(Component).filter_by(
            schematic_file_id=schematic_file_id
        ).offset((page - 1) * per_page).limit(per_page).all()
        
        return jsonify({
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
            "components": [c.to_dict() for c in components]
        }), 200
    finally:
        db.close()


@api.route('/trace/<int:component_id>')
def trace_component(component_id: int):
    """
    Get circuit trace for a component.
    Shows all connections and related components.
    """
    db = get_db()
    try:
        component = db.query(Component).get(component_id)
        if not component:
            return jsonify({"error": "Component not found"}), 404
        
        # Get connections
        connections = db.query(Connection).filter(
            (Connection.from_component_id == component_id) |
            (Connection.to_component_id == component_id) |
            (Connection.from_component_mark == component.mark) |
            (Connection.to_component_mark == component.mark)
        ).all()
        
        # Get connected components
        connected_ids = set()
        for conn in connections:
            if conn.from_component_id:
                connected_ids.add(conn.from_component_id)
            if conn.to_component_id:
                connected_ids.add(conn.to_component_id)
        
        connected_ids.discard(component_id)
        
        connected = db.query(Component).filter(
            Component.id.in_(connected_ids)
        ).all() if connected_ids else []
        
        return jsonify({
            "component": component.to_dict(),
            "connections": [c.to_dict() for c in connections],
            "connected_components": [c.to_dict() for c in connected]
        }), 200
    finally:
        db.close()


@api.route('/trace/mark/<mark>')
def trace_by_mark(mark: str):
    """Get circuit trace by component mark."""
    schematic_file_id = request.args.get('schematic_file_id', type=int)
    
    db = get_db()
    try:
        q = db.query(Component).filter(Component.mark == mark)
        if schematic_file_id:
            q = q.filter(Component.schematic_file_id == schematic_file_id)
        
        component = q.first()
        if not component:
            return jsonify({"error": f"Component with mark '{mark}' not found"}), 404
        
        return trace_component(component.id)
    finally:
        db.close()


@api.route('/pdf/trace/<int:component_id>')
def get_pdf_with_overlay(component_id: int):
    """
    Get PDF page with component highlighted.
    """
    db = get_db()
    try:
        component = db.query(Component).get(component_id)
        if not component:
            return jsonify({"error": "Component not found"}), 404
        
        schematic_file = db.query(SchematicFile).get(component.schematic_file_id)
        if not schematic_file:
            return jsonify({"error": "Schematic file not found"}), 404
        
        pdf_path = Path(schematic_file.filepath)
        if not pdf_path.exists():
            return jsonify({"error": "PDF file not found on disk"}), 404
        
        overlay_service = OverlayService(db)
        pdf_bytes = overlay_service.create_component_overlay(
            pdf_path=pdf_path,
            component=component,
            include_connections=True,
            include_wire_labels=True
        )
        
        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'inline; filename="overlay_{component.mark}.pdf"'
            }
        )
    finally:
        db.close()


@api.route('/export/<int:schematic_file_id>')
def export_data(schematic_file_id: int):
    """
    Export extracted data as JSON or CSV.
    
    Query params:
        - format: json or csv (default json)
    """
    export_format = request.args.get('format', 'json').lower()
    
    db = get_db()
    try:
        schematic_file = db.query(SchematicFile).get(schematic_file_id)
        if not schematic_file:
            return jsonify({"error": "Schematic file not found"}), 404
        
        components = db.query(Component).filter_by(
            schematic_file_id=schematic_file_id
        ).all()
        
        connections = db.query(Connection).filter_by(
            schematic_file_id=schematic_file_id
        ).all()
        
        wire_labels = db.query(WireLabel).filter_by(
            schematic_file_id=schematic_file_id
        ).all()
        
        if export_format == 'json':
            data = {
                "schematic_file": {
                    "id": schematic_file.id,
                    "filename": schematic_file.filename,
                    "machine": schematic_file.machine.name,
                    "extraction_status": schematic_file.extraction_status,
                    "pages_processed": schematic_file.total_pages_processed
                },
                "components": [c.to_dict() for c in components],
                "connections": [c.to_dict() for c in connections],
                "wire_labels": [w.to_dict() for w in wire_labels]
            }
            return jsonify(data), 200
        
        elif export_format == 'csv':
            # For CSV, return a simple response (full implementation would use zipfile)
            import csv
            import io
            
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Components
            writer.writerow(['--- COMPONENTS ---'])
            writer.writerow(['id', 'mark', 'name', 'symbol', 'type', 'pdf_page', 'schematic_page', 'x', 'y'])
            for c in components:
                writer.writerow([c.id, c.mark, c.name, c.symbol, c.type, 
                               c.pdf_page_index, c.schematic_page_number, c.x, c.y])
            
            writer.writerow([])
            writer.writerow(['--- CONNECTIONS ---'])
            writer.writerow(['id', 'from_mark', 'to_mark', 'wire_label', 'pdf_page', 'schematic_page'])
            for c in connections:
                writer.writerow([c.id, c.from_component_mark, c.to_component_mark, 
                               c.wire_label, c.pdf_page_index, c.schematic_page_number])
            
            return Response(
                output.getvalue(),
                mimetype='text/csv',
                headers={
                    'Content-Disposition': f'attachment; filename="export_{schematic_file_id}.csv"'
                }
            )
        
        else:
            return jsonify({"error": f"Unknown format: {export_format}"}), 400
            
    finally:
        db.close()

