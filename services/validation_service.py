"""
Validation Service for QC/accuracy verification.
Verifies extracted data mirrors schematic exactly.
"""
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from sqlalchemy.orm import Session

from config import Config
from models import (
    SchematicFile, Component, Connection, WireLabel,
    ValidationResult, ValidationStatus, ValidationType
)


class ValidationService:
    """
    Service for validating extraction accuracy.
    
    Features:
    - Completeness checking
    - Coordinate bounds validation
    - Data integrity checks
    - Configurable thresholds
    """
    
    def __init__(self, db: Session):
        """Initialize validation service."""
        self.db = db
        
        # Thresholds from config
        self.component_pass = Config.VALIDATION_COMPONENT_PASS
        self.component_warning = Config.VALIDATION_COMPONENT_WARNING
        self.connection_pass = Config.VALIDATION_CONNECTION_PASS
        self.connection_warning = Config.VALIDATION_CONNECTION_WARNING
        self.wire_label_pass = Config.VALIDATION_WIRE_LABEL_PASS
        self.wire_label_warning = Config.VALIDATION_WIRE_LABEL_WARNING
        self.coord_error_pass = Config.VALIDATION_COORD_ERROR_PASS
        self.coord_error_warning = Config.VALIDATION_COORD_ERROR_WARNING
    
    def validate_page(
        self,
        schematic_file: SchematicFile,
        pdf_page_index: int,
        expected_counts: Optional[Dict[str, int]] = None
    ) -> ValidationResult:
        """
        Validate extraction results for a single page.
        
        Args:
            schematic_file: SchematicFile record
            pdf_page_index: Page to validate
            expected_counts: Optional expected counts for completeness check
            
        Returns:
            ValidationResult record
        """
        discrepancies = []
        scores = []
        
        # Count extracted elements
        component_count = self.db.query(Component).filter_by(
            schematic_file_id=schematic_file.id,
            pdf_page_index=pdf_page_index
        ).count()
        
        connection_count = self.db.query(Connection).filter_by(
            schematic_file_id=schematic_file.id,
            pdf_page_index=pdf_page_index
        ).count()
        
        wire_label_count = self.db.query(WireLabel).filter_by(
            schematic_file_id=schematic_file.id,
            pdf_page_index=pdf_page_index
        ).count()
        
        # Basic sanity checks
        if component_count == 0:
            discrepancies.append({
                "type": "no_components",
                "message": f"No components extracted from page {pdf_page_index + 1}",
                "severity": "warning"
            })
        
        # Coordinate bounds validation
        coord_issues = self._validate_coordinates(schematic_file.id, pdf_page_index)
        discrepancies.extend(coord_issues)
        
        # Data integrity checks
        integrity_issues = self._validate_data_integrity(schematic_file.id, pdf_page_index)
        discrepancies.extend(integrity_issues)
        
        # Calculate completeness score (if expected counts provided)
        if expected_counts:
            if expected_counts.get("components", 0) > 0:
                comp_score = component_count / expected_counts["components"]
                scores.append(comp_score)
            if expected_counts.get("connections", 0) > 0:
                conn_score = connection_count / expected_counts["connections"]
                scores.append(conn_score)
        
        # Determine overall status
        if discrepancies:
            has_errors = any(d.get("severity") == "error" for d in discrepancies)
            status = ValidationStatus.FAIL if has_errors else ValidationStatus.WARNING
        else:
            status = ValidationStatus.PASS
        
        confidence_score = sum(scores) / len(scores) if scores else 0.9
        
        # Create validation result
        result = ValidationResult(
            schematic_file_id=schematic_file.id,
            pdf_page_index=pdf_page_index,
            validation_type=ValidationType.PAGE,
            status=status,
            confidence_score=min(confidence_score, 1.0),
            discrepancies=discrepancies
        )
        
        self.db.add(result)
        self.db.commit()
        
        return result
    
    def validate_full_file(
        self,
        schematic_file: SchematicFile
    ) -> ValidationResult:
        """
        Validate entire schematic file after extraction.
        
        Args:
            schematic_file: SchematicFile record
            
        Returns:
            ValidationResult record
        """
        discrepancies = []
        
        # Get total counts
        total_components = self.db.query(Component).filter_by(
            schematic_file_id=schematic_file.id
        ).count()
        
        total_connections = self.db.query(Connection).filter_by(
            schematic_file_id=schematic_file.id
        ).count()
        
        total_wire_labels = self.db.query(WireLabel).filter_by(
            schematic_file_id=schematic_file.id
        ).count()
        
        # Check for empty extraction
        if total_components == 0:
            discrepancies.append({
                "type": "empty_extraction",
                "message": "No components were extracted",
                "severity": "error"
            })
        
        # Check for orphaned connections
        orphan_count = self._count_orphaned_connections(schematic_file.id)
        if orphan_count > 0:
            discrepancies.append({
                "type": "orphaned_connections",
                "message": f"{orphan_count} connections reference unknown components",
                "severity": "warning",
                "count": orphan_count
            })
        
        # Check for duplicate component marks
        duplicates = self._find_duplicate_marks(schematic_file.id)
        if duplicates:
            discrepancies.append({
                "type": "duplicate_marks",
                "message": f"Found duplicate component marks: {', '.join(duplicates[:5])}",
                "severity": "warning",
                "marks": duplicates
            })
        
        # Calculate overall score
        pages_processed = schematic_file.total_pages_processed or 0
        if pages_processed > 0:
            avg_components_per_page = total_components / pages_processed
            # Rough heuristic: expect at least 5 components per page
            score = min(avg_components_per_page / 5, 1.0)
        else:
            score = 0.0
        
        # Determine status based on discrepancies
        if any(d.get("severity") == "error" for d in discrepancies):
            status = ValidationStatus.FAIL
        elif discrepancies:
            status = ValidationStatus.WARNING
        else:
            status = ValidationStatus.PASS
        
        result = ValidationResult(
            schematic_file_id=schematic_file.id,
            pdf_page_index=None,  # Full file validation
            validation_type=ValidationType.FULL_FILE,
            status=status,
            confidence_score=score,
            discrepancies=discrepancies
        )
        
        self.db.add(result)
        self.db.commit()
        
        return result
    
    def _validate_coordinates(
        self,
        schematic_file_id: int,
        pdf_page_index: int
    ) -> List[Dict[str, Any]]:
        """Check coordinate bounds for components."""
        issues = []
        
        # Get page dimensions
        from models import SchematicPage
        page = self.db.query(SchematicPage).filter_by(
            schematic_file_id=schematic_file_id,
            pdf_page_index=pdf_page_index
        ).first()
        
        if not page or not page.width or not page.height:
            return issues  # Can't validate without dimensions
        
        # Check components
        components = self.db.query(Component).filter_by(
            schematic_file_id=schematic_file_id,
            pdf_page_index=pdf_page_index
        ).all()
        
        for comp in components:
            if comp.x is not None and comp.y is not None:
                if comp.x < 0 or comp.x > page.width:
                    issues.append({
                        "type": "coord_out_of_bounds",
                        "message": f"Component {comp.mark} has x={comp.x} outside page width {page.width}",
                        "severity": "warning",
                        "component_id": comp.id
                    })
                if comp.y < 0 or comp.y > page.height:
                    issues.append({
                        "type": "coord_out_of_bounds",
                        "message": f"Component {comp.mark} has y={comp.y} outside page height {page.height}",
                        "severity": "warning",
                        "component_id": comp.id
                    })
        
        return issues
    
    def _validate_data_integrity(
        self,
        schematic_file_id: int,
        pdf_page_index: int
    ) -> List[Dict[str, Any]]:
        """Check data integrity for extracted elements."""
        issues = []
        
        # Check for components without marks
        no_mark = self.db.query(Component).filter_by(
            schematic_file_id=schematic_file_id,
            pdf_page_index=pdf_page_index
        ).filter(
            (Component.mark == None) | (Component.mark == "") | (Component.mark == "UNKNOWN")
        ).count()
        
        if no_mark > 0:
            issues.append({
                "type": "missing_marks",
                "message": f"{no_mark} components have missing or unknown marks",
                "severity": "warning",
                "count": no_mark
            })
        
        # Check for wire labels without labels
        no_label = self.db.query(WireLabel).filter_by(
            schematic_file_id=schematic_file_id,
            pdf_page_index=pdf_page_index
        ).filter(
            (WireLabel.label == None) | (WireLabel.label == "")
        ).count()
        
        if no_label > 0:
            issues.append({
                "type": "missing_wire_labels",
                "message": f"{no_label} wire labels have no label text",
                "severity": "warning",
                "count": no_label
            })
        
        return issues
    
    def _count_orphaned_connections(self, schematic_file_id: int) -> int:
        """Count connections that reference non-existent components."""
        # Get all component marks
        components = self.db.query(Component.mark).filter_by(
            schematic_file_id=schematic_file_id
        ).all()
        marks = {c.mark for c in components}
        
        # Check connections
        connections = self.db.query(Connection).filter_by(
            schematic_file_id=schematic_file_id
        ).all()
        
        orphan_count = 0
        for conn in connections:
            if conn.from_component_mark and conn.from_component_mark not in marks:
                if not conn.is_external:
                    orphan_count += 1
            if conn.to_component_mark and conn.to_component_mark not in marks:
                if not conn.is_external:
                    orphan_count += 1
        
        return orphan_count
    
    def _find_duplicate_marks(self, schematic_file_id: int) -> List[str]:
        """Find component marks that appear multiple times on same page."""
        from sqlalchemy import func
        
        duplicates = self.db.query(
            Component.mark,
            Component.pdf_page_index,
            func.count(Component.id).label("count")
        ).filter_by(
            schematic_file_id=schematic_file_id
        ).group_by(
            Component.mark,
            Component.pdf_page_index
        ).having(
            func.count(Component.id) > 1
        ).all()
        
        return [d.mark for d in duplicates]
    
    def get_validation_summary(
        self,
        schematic_file_id: int
    ) -> Dict[str, Any]:
        """Get summary of all validation results for a file."""
        results = self.db.query(ValidationResult).filter_by(
            schematic_file_id=schematic_file_id
        ).all()
        
        summary = {
            "total_validations": len(results),
            "passed": sum(1 for r in results if r.status == ValidationStatus.PASS),
            "warnings": sum(1 for r in results if r.status == ValidationStatus.WARNING),
            "failed": sum(1 for r in results if r.status == ValidationStatus.FAIL),
            "avg_confidence": sum(r.confidence_score or 0 for r in results) / len(results) if results else 0,
            "all_discrepancies": []
        }
        
        for result in results:
            if result.discrepancies:
                summary["all_discrepancies"].extend(result.discrepancies)
        
        return summary

