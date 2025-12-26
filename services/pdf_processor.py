"""
PDF Processor Service for schematic extraction.
Handles PDF manipulation, page extraction, and coordinate conversion.
"""
import re
import hashlib
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any

import fitz  # PyMuPDF
import pdfplumber


class PDFProcessor:
    """
    Service for PDF manipulation and processing.
    
    Features:
    - Extract individual pages as separate PDFs
    - Detect schematic page numbers from title blocks
    - Coordinate conversion between systems
    - Page dimension extraction
    """
    
    # Regex pattern for schematic page number (e.g., "1/207", "25/207")
    # Also matches full-width Japanese numerals (１/２０７)
    PAGE_NUMBER_PATTERN = re.compile(r'([0-9０-９]+)\s*[/／]\s*([0-9０-９]+)')
    
    # Full-width to half-width digit mapping
    FULLWIDTH_DIGITS = str.maketrans('０１２３４５６７８９', '0123456789')
    
    @classmethod
    def _fullwidth_to_int(cls, s: str) -> int:
        """Convert a string with full-width digits to an integer."""
        return int(s.translate(cls.FULLWIDTH_DIGITS))
    
    def __init__(self, pdf_path: Path):
        """
        Initialize PDF processor with a PDF file.
        
        Args:
            pdf_path: Path to the PDF file
        """
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {self.pdf_path}")
        
        self._doc: Optional[fitz.Document] = None
        self._plumber: Optional[pdfplumber.PDF] = None
    
    def __enter__(self):
        """Context manager entry."""
        self._doc = fitz.open(self.pdf_path)
        self._plumber = pdfplumber.open(self.pdf_path)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if self._doc:
            self._doc.close()
        if self._plumber:
            self._plumber.close()
    
    @property
    def page_count(self) -> int:
        """Get total number of pages in PDF."""
        if self._doc:
            return len(self._doc)
        with fitz.open(self.pdf_path) as doc:
            return len(doc)
    
    def get_file_hash(self) -> str:
        """Calculate SHA-256 hash of the PDF file."""
        sha256 = hashlib.sha256()
        with open(self.pdf_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    
    def get_page_dimensions(self, page_index: int) -> Tuple[float, float]:
        """
        Get page dimensions in points.
        
        Args:
            page_index: 0-based page index
            
        Returns:
            Tuple of (width, height) in points
        """
        if self._doc:
            page = self._doc[page_index]
            rect = page.rect
            return (rect.width, rect.height)
        
        with fitz.open(self.pdf_path) as doc:
            page = doc[page_index]
            rect = page.rect
            return (rect.width, rect.height)
    
    def extract_pages(
        self,
        page_indices: List[int],
        output_path: Path
    ) -> Path:
        """
        Extract specific pages to a new PDF file.
        
        Args:
            page_indices: List of 0-based page indices to extract
            output_path: Path for the output PDF
            
        Returns:
            Path to the created PDF
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with fitz.open(self.pdf_path) as src_doc:
            new_doc = fitz.open()
            
            for idx in page_indices:
                if 0 <= idx < len(src_doc):
                    new_doc.insert_pdf(src_doc, from_page=idx, to_page=idx)
                else:
                    raise IndexError(f"Page index {idx} out of range (0-{len(src_doc)-1})")
            
            new_doc.save(str(output_path))
            new_doc.close()
        
        return output_path
    
    def detect_schematic_page_number(self, page_index: int) -> Optional[int]:
        """
        Detect schematic page number from title block.
        
        Looks for pattern like "1/207" in the bottom-right area of the page.
        
        Args:
            page_index: 0-based page index
            
        Returns:
            Schematic page number or None if not detected
        """
        if self._plumber:
            page = self._plumber.pages[page_index]
        else:
            with pdfplumber.open(self.pdf_path) as pdf:
                page = pdf.pages[page_index]
                return self._detect_page_number_from_page(page)
        
        return self._detect_page_number_from_page(page)
    
    def _detect_page_number_from_page(self, page) -> Optional[int]:
        """Extract page number from a pdfplumber page object."""
        import logging
        logger = logging.getLogger(__name__)
        
        # Get page dimensions
        width = page.width
        height = page.height
        
        # Focus on bottom-right quadrant (title block area)
        # Expand area to be more forgiving - last 50% width, last 25% height
        crop_box = (
            width * 0.5,   # left
            height * 0.75,  # top
            width,          # right
            height          # bottom
        )
        
        try:
            cropped = page.within_bbox(crop_box)
            text = cropped.extract_text() or ""
            logger.debug(f"Title block text extracted: {text[:200] if text else 'None'}")
        except Exception as e:
            logger.warning(f"Crop failed: {e}, using full page")
            # Fallback to full page
            text = page.extract_text() or ""
        
        # Find page number pattern (e.g., "1/207", "25/207")
        matches = self.PAGE_NUMBER_PATTERN.findall(text)
        logger.debug(f"Page number matches: {matches}")
        
        if matches:
            # Take the last match (most likely to be the page number)
            page_num, total = matches[-1]
            # Convert full-width numbers to regular integers
            page_num_int = self._fullwidth_to_int(page_num)
            total_int = self._fullwidth_to_int(total)
            logger.info(f"Detected schematic page: {page_num_int}/{total_int}")
            return page_num_int
        
        # Also try looking in full page if not found
        if not matches:
            full_text = page.extract_text() or ""
            all_matches = self.PAGE_NUMBER_PATTERN.findall(full_text)
            if all_matches:
                page_num, total = all_matches[-1]
                page_num_int = self._fullwidth_to_int(page_num)
                total_int = self._fullwidth_to_int(total)
                logger.info(f"Detected schematic page from full text: {page_num_int}/{total_int}")
                return page_num_int
        
        logger.warning("Could not detect schematic page number")
        return None
    
    def detect_all_page_numbers(
        self,
        page_indices: Optional[List[int]] = None
    ) -> Dict[int, Optional[int]]:
        """
        Detect schematic page numbers for multiple pages.
        
        Args:
            page_indices: List of pages to check. None = all pages.
            
        Returns:
            Dict mapping pdf_page_index -> schematic_page_number (or None)
        """
        if page_indices is None:
            page_indices = list(range(self.page_count))
        
        result = {}
        
        if self._plumber:
            for idx in page_indices:
                if 0 <= idx < len(self._plumber.pages):
                    result[idx] = self._detect_page_number_from_page(self._plumber.pages[idx])
                else:
                    result[idx] = None
        else:
            with pdfplumber.open(self.pdf_path) as pdf:
                for idx in page_indices:
                    if 0 <= idx < len(pdf.pages):
                        result[idx] = self._detect_page_number_from_page(pdf.pages[idx])
                    else:
                        result[idx] = None
        
        return result
    
    def extract_text_from_page(self, page_index: int) -> str:
        """
        Extract all text from a page.
        
        Args:
            page_index: 0-based page index
            
        Returns:
            Extracted text as string
        """
        if self._plumber:
            page = self._plumber.pages[page_index]
            return page.extract_text() or ""
        
        with pdfplumber.open(self.pdf_path) as pdf:
            page = pdf.pages[page_index]
            return page.extract_text() or ""
    
    def extract_context_pages_text(
        self,
        instructions_page: int = 1,
        legend_page: int = 2
    ) -> str:
        """
        Extract text from context pages (reading instructions and legend).
        
        Args:
            instructions_page: 0-based index of reading instructions page
            legend_page: 0-based index of symbol legend page
            
        Returns:
            Combined text from context pages
        """
        context_parts = []
        
        if 0 <= instructions_page < self.page_count:
            text = self.extract_text_from_page(instructions_page)
            if text.strip():
                context_parts.append(f"READING INSTRUCTIONS (PDF page {instructions_page + 1}):")
                context_parts.append(text)
                context_parts.append("")
        
        if 0 <= legend_page < self.page_count:
            text = self.extract_text_from_page(legend_page)
            if text.strip():
                context_parts.append(f"SYMBOL LEGEND (PDF page {legend_page + 1}):")
                context_parts.append(text)
                context_parts.append("")
        
        return "\n".join(context_parts)
    
    @staticmethod
    def convert_coords_pdfplumber_to_pymupdf(
        x: float,
        y: float,
        page_height: float
    ) -> Tuple[float, float]:
        """
        Convert coordinates from pdfplumber system to PyMuPDF system.
        
        pdfplumber: origin at top-left, y increases downward
        PyMuPDF: origin at bottom-left, y increases upward
        
        Args:
            x: X coordinate in pdfplumber system
            y: Y coordinate in pdfplumber system
            page_height: Height of the page in points
            
        Returns:
            Tuple of (x, y) in PyMuPDF system
        """
        return (x, page_height - y)
    
    @staticmethod
    def convert_coords_pymupdf_to_pdfplumber(
        x: float,
        y: float,
        page_height: float
    ) -> Tuple[float, float]:
        """
        Convert coordinates from PyMuPDF system to pdfplumber system.
        
        Args:
            x: X coordinate in PyMuPDF system
            y: Y coordinate in PyMuPDF system
            page_height: Height of the page in points
            
        Returns:
            Tuple of (x, y) in pdfplumber system
        """
        return (x, page_height - y)
    
    def render_page_as_image(
        self,
        page_index: int,
        zoom: float = 2.0
    ) -> bytes:
        """
        Render a page as PNG image bytes.
        
        Args:
            page_index: 0-based page index
            zoom: Zoom factor for resolution
            
        Returns:
            PNG image as bytes
        """
        if self._doc:
            page = self._doc[page_index]
        else:
            doc = fitz.open(self.pdf_path)
            page = doc[page_index]
        
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        
        if not self._doc:
            doc.close()
        
        return pix.tobytes("png")

