"""
Overlay Service for PDF highlighting.
Generates PDF overlays for component/connection visualization.
"""
from pathlib import Path
from typing import Optional, List, Tuple
import io

import fitz  # PyMuPDF

from sqlalchemy.orm import Session

from models import Component, Connection, WireLabel, SchematicPage


class OverlayColors:
    """Color definitions for overlay highlighting."""
    SELECTED_COMPONENT = (1.0, 0.0, 0.0)      # Red
    CONNECTED_COMPONENT = (0.0, 0.0, 1.0)     # Blue
    WIRE_LABEL = (0.0, 0.8, 0.0)              # Green
    CONNECTION_PATH = (1.0, 0.5, 0.0)         # Orange
    
    # With alpha for fill
    SELECTED_FILL = (1.0, 0.0, 0.0, 0.2)      # Red, 20% opacity
    CONNECTED_FILL = (0.0, 0.0, 1.0, 0.2)     # Blue, 20% opacity
    WIRE_LABEL_FILL = (0.0, 0.8, 0.0, 0.2)    # Green, 20% opacity


class OverlayService:
    """
    Service for generating PDF overlays with highlighted elements.
    
    Features:
    - Highlight selected component (red)
    - Highlight connected components (blue)
    - Highlight wire labels (green)
    - Draw connection paths (orange)
    """
    
    # Padding around highlighted elements (in points)
    PADDING = 2.0
    
    def __init__(self, db: Session):
        """Initialize overlay service."""
        self.db = db
    
    def create_component_overlay(
        self,
        pdf_path: Path,
        component: Component,
        include_connections: bool = True,
        include_wire_labels: bool = True
    ) -> bytes:
        """
        Create PDF with highlighted component and related elements.
        
        Args:
            pdf_path: Path to original PDF
            component: Component to highlight
            include_connections: Whether to highlight connected components
            include_wire_labels: Whether to highlight related wire labels
            
        Returns:
            PDF bytes with overlay
        """
        pdf_path = Path(pdf_path)
        doc = fitz.open(pdf_path)
        
        try:
            page = doc[component.pdf_page_index]
            page_height = page.rect.height
            
            # Highlight the selected component
            if component.x is not None and component.y is not None:
                self._draw_component_highlight(
                    page=page,
                    component=component,
                    page_height=page_height,
                    color=OverlayColors.SELECTED_COMPONENT,
                    fill_color=OverlayColors.SELECTED_FILL
                )
            
            if include_connections:
                # Find and highlight connected components
                connections = self._get_component_connections(component)
                connected_components = set()
                
                for conn in connections:
                    if conn.from_component_id and conn.from_component_id != component.id:
                        connected_components.add(conn.from_component_id)
                    if conn.to_component_id and conn.to_component_id != component.id:
                        connected_components.add(conn.to_component_id)
                    
                    # Draw connection path
                    if conn.path_coordinates and conn.pdf_page_index == component.pdf_page_index:
                        self._draw_connection_path(
                            page=page,
                            path=conn.path_coordinates,
                            page_height=page_height
                        )
                
                # Highlight connected components
                for comp_id in connected_components:
                    connected = self.db.query(Component).get(comp_id)
                    if connected and connected.pdf_page_index == component.pdf_page_index:
                        if connected.x is not None and connected.y is not None:
                            self._draw_component_highlight(
                                page=page,
                                component=connected,
                                page_height=page_height,
                                color=OverlayColors.CONNECTED_COMPONENT,
                                fill_color=OverlayColors.CONNECTED_FILL
                            )
            
            if include_wire_labels:
                # Find related wire labels
                wire_labels = self._get_related_wire_labels(component)
                for wl in wire_labels:
                    if wl.pdf_page_index == component.pdf_page_index:
                        self._draw_wire_label_highlight(
                            page=page,
                            wire_label=wl,
                            page_height=page_height
                        )
            
            # Save to bytes
            output = io.BytesIO()
            doc.save(output)
            return output.getvalue()
            
        finally:
            doc.close()
    
    def create_page_overlay(
        self,
        pdf_path: Path,
        pdf_page_index: int,
        schematic_file_id: int,
        highlight_all: bool = True
    ) -> bytes:
        """
        Create PDF with all elements on a page highlighted.
        
        Args:
            pdf_path: Path to original PDF
            pdf_page_index: Page to highlight
            schematic_file_id: Schematic file ID
            highlight_all: Whether to highlight all elements
            
        Returns:
            PDF bytes with overlay
        """
        pdf_path = Path(pdf_path)
        doc = fitz.open(pdf_path)
        
        try:
            page = doc[pdf_page_index]
            page_height = page.rect.height
            
            if highlight_all:
                # Get all components on this page
                components = self.db.query(Component).filter_by(
                    schematic_file_id=schematic_file_id,
                    pdf_page_index=pdf_page_index
                ).all()
                
                for comp in components:
                    if comp.x is not None and comp.y is not None:
                        self._draw_component_highlight(
                            page=page,
                            component=comp,
                            page_height=page_height,
                            color=OverlayColors.SELECTED_COMPONENT,
                            fill_color=OverlayColors.SELECTED_FILL
                        )
                
                # Get all wire labels
                wire_labels = self.db.query(WireLabel).filter_by(
                    schematic_file_id=schematic_file_id,
                    pdf_page_index=pdf_page_index
                ).all()
                
                for wl in wire_labels:
                    self._draw_wire_label_highlight(
                        page=page,
                        wire_label=wl,
                        page_height=page_height
                    )
            
            output = io.BytesIO()
            doc.save(output)
            return output.getvalue()
            
        finally:
            doc.close()
    
    def _draw_component_highlight(
        self,
        page: fitz.Page,
        component: Component,
        page_height: float,
        color: Tuple[float, float, float],
        fill_color: Optional[Tuple[float, float, float, float]] = None
    ):
        """Draw highlight rectangle around a component."""
        # Convert coordinates (pdfplumber uses top-left origin, PyMuPDF uses bottom-left)
        x = component.x
        y = component.y
        width = component.width or 30  # Default width if not specified
        height = component.height or 20  # Default height if not specified
        
        # Note: fitz (PyMuPDF) actually uses top-left origin in newer versions
        # So we may not need conversion. Test and adjust if needed.
        
        # Create rectangle with padding
        rect = fitz.Rect(
            x - self.PADDING,
            y - self.PADDING,
            x + width + self.PADDING,
            y + height + self.PADDING
        )
        
        # Draw rectangle
        page.draw_rect(rect, color=color, width=1.5)
        
        # Fill with transparent color if specified
        if fill_color and len(fill_color) == 4:
            shape = page.new_shape()
            shape.draw_rect(rect)
            shape.finish(
                color=color,
                fill=color[:3],
                fill_opacity=fill_color[3]
            )
            shape.commit()
    
    def _draw_wire_label_highlight(
        self,
        page: fitz.Page,
        wire_label: WireLabel,
        page_height: float
    ):
        """Draw highlight around a wire label."""
        if wire_label.x is None or wire_label.y is None:
            return
        
        # Approximate label size
        label_width = len(wire_label.label) * 6 + 4  # Rough estimate
        label_height = 12
        
        rect = fitz.Rect(
            wire_label.x - self.PADDING,
            wire_label.y - self.PADDING,
            wire_label.x + label_width + self.PADDING,
            wire_label.y + label_height + self.PADDING
        )
        
        page.draw_rect(rect, color=OverlayColors.WIRE_LABEL, width=1.0)
    
    def _draw_connection_path(
        self,
        page: fitz.Page,
        path: List[List[float]],
        page_height: float
    ):
        """Draw a connection path (wire)."""
        if not path or len(path) < 2:
            return
        
        # Convert path points
        points = []
        for point in path:
            if len(point) >= 2:
                x, y = point[0], point[1]
                points.append(fitz.Point(x, y))
        
        if len(points) < 2:
            return
        
        # Draw polyline
        shape = page.new_shape()
        shape.draw_polyline(points)
        shape.finish(color=OverlayColors.CONNECTION_PATH, width=2.0)
        shape.commit()
    
    def _get_component_connections(self, component: Component) -> List[Connection]:
        """Get all connections involving a component."""
        connections = self.db.query(Connection).filter(
            (Connection.schematic_file_id == component.schematic_file_id) &
            (
                (Connection.from_component_id == component.id) |
                (Connection.to_component_id == component.id) |
                (Connection.from_component_mark == component.mark) |
                (Connection.to_component_mark == component.mark)
            )
        ).all()
        
        return connections
    
    def _get_related_wire_labels(self, component: Component) -> List[WireLabel]:
        """Get wire labels connected to a component."""
        # Get connections for this component
        connections = self._get_component_connections(component)
        
        # Get wire label texts
        wire_labels_text = set()
        for conn in connections:
            if conn.wire_label:
                wire_labels_text.add(conn.wire_label)
        
        if not wire_labels_text:
            return []
        
        # Find matching wire labels
        wire_labels = self.db.query(WireLabel).filter(
            WireLabel.schematic_file_id == component.schematic_file_id,
            WireLabel.label.in_(wire_labels_text)
        ).all()
        
        return wire_labels
    
    def render_page_image(
        self,
        pdf_path: Path,
        pdf_page_index: int,
        zoom: float = 2.0
    ) -> bytes:
        """
        Render a page as a PNG image.
        
        Args:
            pdf_path: Path to PDF
            pdf_page_index: Page to render
            zoom: Zoom factor
            
        Returns:
            PNG image bytes
        """
        doc = fitz.open(pdf_path)
        try:
            page = doc[pdf_page_index]
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            return pix.tobytes("png")
        finally:
            doc.close()

