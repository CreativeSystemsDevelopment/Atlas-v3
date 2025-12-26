"""
Gemini 3 API Service for schematic extraction.
Handles file upload, caching, and structured extraction with JSON Schema.
"""
import time
import random
import logging
from typing import Optional, Dict, Any, List, Generator
from pathlib import Path

import google.generativeai as genai

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
from google.generativeai.types import HarmCategory, HarmBlockThreshold

from config import Config


class GeminiService:
    """
    Service for interacting with Gemini 3 API.
    
    Features:
    - File upload with caching (90% cost savings)
    - Structured output with JSON Schema enforcement
    - Configurable thinking_level and media_resolution
    - Retry with exponential backoff
    """
    
    # JSON Schema for extraction output
    EXTRACTION_SCHEMA = {
        "type": "object",
        "properties": {
            "page_info": {
                "type": "object",
                "properties": {
                    "pdf_page_index": {"type": "integer"},
                    "schematic_page_number": {"type": "integer"},
                    "page_width": {"type": "number"},
                    "page_height": {"type": "number"}
                },
                "required": ["pdf_page_index"]
            },
            "components": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "name": {"type": "string"},
                        "mark": {"type": "string"},
                        "type": {"type": "string"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "width": {"type": "number"},
                        "height": {"type": "number"},
                        "description": {"type": "string"}
                    },
                    "required": ["mark"]
                }
            },
            "connections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "from_component_mark": {"type": "string"},
                        "to_component_mark": {"type": "string"},
                        "wire_label": {"type": "string"},
                        "terminal_from": {"type": "string"},
                        "terminal_to": {"type": "string"},
                        "path": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "number"}
                            }
                        },
                        "is_external": {"type": "boolean"}
                    }
                }
            },
            "wire_labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "x": {"type": "number"},
                        "y": {"type": "number"}
                    },
                    "required": ["label"]
                }
            },
            "continuations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "from_component_mark": {"type": "string"},
                        "to_page_hint": {"type": "string"},
                        "direction": {"type": "string"}
                    }
                }
            }
        },
        "required": ["components", "connections", "wire_labels"]
    }
    
    def __init__(self):
        """Initialize Gemini service with API key validation."""
        if not Config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is required")
        
        logger.info(f"Initializing Gemini service with model: {Config.GEMINI_MODEL}")
        genai.configure(api_key=Config.GEMINI_API_KEY)
        
        self.model_name = Config.GEMINI_MODEL
        self.flash_model_name = Config.GEMINI_FLASH_MODEL
        self.temperature = Config.GEMINI_TEMPERATURE
        self.thinking_level = Config.GEMINI_THINKING_LEVEL
        self.media_resolution = Config.GEMINI_MEDIA_RESOLUTION
        self.timeout = Config.GEMINI_TIMEOUT
        self.max_retries = Config.GEMINI_MAX_RETRIES
        
        # Cached file URIs
        self._file_cache: Dict[str, str] = {}
    
    def _get_model(self, use_flash: bool = False) -> genai.GenerativeModel:
        """Get configured Gemini model."""
        model_name = self.flash_model_name if use_flash else self.model_name
        
        # Safety settings - allow all content for schematic analysis
        safety_settings = {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
        
        return genai.GenerativeModel(
            model_name=model_name,
            safety_settings=safety_settings
        )
    
    def upload_file(self, file_path: Path, display_name: Optional[str] = None) -> str:
        """
        Upload file to Gemini Files API for caching.
        Returns file URI for subsequent calls.
        
        Args:
            file_path: Path to PDF file
            display_name: Optional display name for the file
            
        Returns:
            File URI string for use in generate_content calls
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Check cache first
        cache_key = str(file_path)
        if cache_key in self._file_cache:
            return self._file_cache[cache_key]
        
        display_name = display_name or file_path.name
        
        logger.info(f"Uploading file to Gemini: {file_path}")
        
        # Upload with retry
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"Upload attempt {attempt + 1}/{self.max_retries}")
                uploaded_file = genai.upload_file(
                    path=str(file_path),
                    display_name=display_name
                )
                
                # Wait for processing
                while uploaded_file.state.name == "PROCESSING":
                    time.sleep(1)
                    uploaded_file = genai.get_file(uploaded_file.name)
                
                if uploaded_file.state.name == "FAILED":
                    raise RuntimeError(f"File upload failed: {uploaded_file.state.name}")
                
                file_uri = uploaded_file.uri
                self._file_cache[cache_key] = file_uri
                
                return file_uri
                
            except Exception as e:
                logger.error(f"Upload attempt {attempt + 1} failed: {e}")
                if attempt == self.max_retries - 1:
                    raise
                delay = self._calculate_backoff(attempt)
                time.sleep(delay)
        
        raise RuntimeError("File upload failed after max retries")
    
    def extract_page(
        self,
        file_uri: str,
        pdf_page_index: int,
        context_text: Optional[str] = None,
        page_mapping: Optional[Dict[int, int]] = None
    ) -> Dict[str, Any]:
        """
        Extract components, connections, and wire labels from a specific page.
        
        Args:
            file_uri: Cached file URI from upload_file()
            pdf_page_index: 0-based page index in PDF
            context_text: Optional context (reading instructions, legend)
            page_mapping: Optional mapping of PDF pages to schematic pages
            
        Returns:
            Extracted data dictionary matching EXTRACTION_SCHEMA
        """
        prompt = self._build_extraction_prompt(
            pdf_page_index=pdf_page_index,
            context_text=context_text,
            page_mapping=page_mapping
        )
        
        # Get the uploaded file reference
        file_ref = genai.get_file(file_uri.split("/")[-1]) if "/" in file_uri else genai.get_file(file_uri)
        
        # Build generation config with JSON Schema
        generation_config = {
            "temperature": self.temperature,
            "response_mime_type": "application/json",
            "response_schema": self.EXTRACTION_SCHEMA,
        }
        
        # Add thinking_level if supported (Gemini 3 feature)
        # Note: This may need adjustment based on actual API support
        
        model = self._get_model()
        
        logger.info(f"Extracting page {pdf_page_index} with model {self.model_name}")
        
        # Retry with backoff
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"Extraction attempt {attempt + 1}/{self.max_retries}")
                response = model.generate_content(
                    contents=[file_ref, prompt],
                    generation_config=generation_config,
                    request_options={"timeout": self.timeout}
                )
                
                logger.debug(f"Got response: {response.text[:500] if response.text else 'No text'}")
                
                # Parse JSON response
                import json
                result = json.loads(response.text)
                
                # Add page info if not present
                if "page_info" not in result:
                    result["page_info"] = {"pdf_page_index": pdf_page_index}
                
                return result
                
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                delay = self._calculate_backoff(attempt)
                time.sleep(delay)
        
        raise RuntimeError("Extraction failed after max retries")
    
    def detect_all_page_numbers(
        self,
        file_uri: str,
        pdf_page_indices: List[int]
    ) -> Dict[int, Optional[int]]:
        """
        Detect schematic page numbers for multiple pages in one call.
        More efficient than calling detect_page_number multiple times.
        
        Args:
            file_uri: Cached file URI
            pdf_page_indices: List of 0-based page indices to check
            
        Returns:
            Dict mapping pdf_page_index -> schematic_page_number (or None)
        """
        if not pdf_page_indices:
            return {}
        
        # Build prompt asking for all pages at once
        page_list = ", ".join([str(idx + 1) for idx in pdf_page_indices])
        prompt = f"""You are analyzing a schematic diagram PDF. 

For each of the following PDF pages: {page_list}

Look at the title block (usually at the bottom right of each page).
Find the schematic page number in the format "X/Y" (e.g., "1/207" means schematic page 1 of 207 total).
The "X" is the schematic page number we need.

Return a JSON object mapping PDF page numbers to schematic page numbers:
{{
  "page_mapping": {{
    "<pdf_page_number>": <schematic_page_number or null>
  }},
  "total_schematic_pages": <total from any page's X/Y format or null>
}}

Example:
{{
  "page_mapping": {{
    "7": 1,
    "8": 2,
    "9": 3
  }},
  "total_schematic_pages": 207
}}

Only return the JSON object, nothing else."""
        
        file_ref = genai.get_file(file_uri.split("/")[-1]) if "/" in file_uri else genai.get_file(file_uri)
        
        # Use a schema that avoids additionalProperties (unsupported by Gemini schema validation)
        schema = {
            "type": "object",
            "properties": {
                "page_mapping": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pdf_page": {"type": "integer"},
                            "schematic_page_number": {
                                "oneOf": [
                                    {"type": "integer"},
                                    {"type": "null"}
                                ]
                            }
                        },
                        "required": ["pdf_page"]
                    }
                },
                "total_schematic_pages": {
                    "oneOf": [
                        {"type": "integer"},
                        {"type": "null"}
                    ]
                }
            },
            "required": ["page_mapping"]
        }
        
        generation_config = {
            "temperature": 0.1,
            "response_mime_type": "application/json",
            "response_schema": schema
        }
        
        model = self._get_model(use_flash=True)
        
        try:
            logger.info(f"Detecting page numbers for PDF pages: {page_list}")
            response = model.generate_content(
                contents=[file_ref, prompt],
                generation_config=generation_config,
                request_options={"timeout": 60}
            )
            
            import json
            result = json.loads(response.text)
            page_mapping_items = result.get("page_mapping", []) or []
            
            # Build dict from array items
            page_mapping = {}
            for item in page_mapping_items:
                pdf_page_num = item.get("pdf_page")
                schematic_num = item.get("schematic_page_number")
                if pdf_page_num is not None:
                    page_mapping[int(pdf_page_num) - 1] = schematic_num  # convert to 0-based
            
            # Ensure all requested pages are present
            result_mapping = {idx: page_mapping.get(idx) for idx in pdf_page_indices}
            
            logger.info(f"Detected page mapping: {result_mapping}")
            return result_mapping
            
        except Exception as e:
            logger.error(f"Page number detection failed: {e}")
            # Fallback: return None for all pages
            return {idx: None for idx in pdf_page_indices}
    
    def detect_page_number(
        self,
        file_uri: str,
        pdf_page_index: int
    ) -> Optional[int]:
        """
        Detect schematic page number from title block (single page).
        Uses Flash model for speed.
        
        Args:
            file_uri: Cached file URI
            pdf_page_index: 0-based page index
            
        Returns:
            Schematic page number or None if not detected
        """
        result = self.detect_all_page_numbers(file_uri, [pdf_page_index])
        return result.get(pdf_page_index)
    
    def _build_extraction_prompt(
        self,
        pdf_page_index: int,
        context_text: Optional[str] = None,
        page_mapping: Optional[Dict[int, int]] = None
    ) -> str:
        """Build the extraction prompt with context."""
        
        prompt_parts = [
            "You are analyzing an industrial electrical schematic diagram.",
            "",
            "CONTEXT:",
        ]
        
        if context_text:
            prompt_parts.append(context_text)
        else:
            prompt_parts.append("- Refer to the reading instructions and symbol legend pages for component identification.")
        
        if page_mapping:
            prompt_parts.append("")
            prompt_parts.append("PAGE MAPPING:")
            for pdf_idx, schematic_num in page_mapping.items():
                prompt_parts.append(f"- PDF page {pdf_idx + 1} ΓåÆ Schematic page {schematic_num}")
        
        prompt_parts.extend([
            "",
            f"TASK: Extract ALL components, connections, and wire labels from PDF page {pdf_page_index + 1}.",
            "",
            "For each COMPONENT, extract:",
            "- symbol: Component symbol type (from legend)",
            "- name: Full component name",
            "- mark: Component identifier (e.g., 'SOL-1', 'MC1', 'CR5')",
            "- type: Component type category",
            "- x, y, width, height: Bounding box coordinates in PDF points",
            "- description: Any additional description text",
            "",
            "For each CONNECTION (wire), extract:",
            "- from_component_mark: Source component mark",
            "- to_component_mark: Destination component mark",
            "- wire_label: Wire number/label",
            "- terminal_from, terminal_to: Terminal numbers if visible",
            "- path: Array of [x, y] coordinates along the wire path",
            "- is_external: true if connection goes to a page not being processed",
            "",
            "For each WIRE LABEL, extract:",
            "- label: The wire number/label text",
            "- x, y: Position coordinates",
            "",
            "For CONTINUATIONS (arrows pointing to other pages):",
            "- from_component_mark: Component near the continuation",
            "- to_page_hint: Text indicating destination page (e.g., 'ΓåÆ5', 'P.12')",
            "- direction: 'to' or 'from'",
            "",
            "IMPORTANT:",
            "- Extract EVERY visible component, connection, and wire label",
            "- Use exact marks as shown in the schematic",
            "- Coordinates should be in PDF points from top-left origin",
            "- If uncertain, include the element with best guess",
        ])
        
        return "\n".join(prompt_parts)
    
    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff with jitter."""
        base_delay = Config.RETRY_BASE_DELAY
        max_delay = Config.RETRY_MAX_DELAY
        
        delay = min(base_delay * (2 ** attempt), max_delay)
        jitter = random.uniform(0, delay * 0.1)
        
        return delay + jitter
    
    def clear_cache(self):
        """Clear file URI cache."""
        self._file_cache.clear()

