"""
Gemini 3 API Service using google.genai client with context caching.
Handles file upload, caching, and structured extraction with JSON Schema.
"""
import time
import json
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path

from google import genai
from google.genai import types

from config import Config

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class GeminiService:
    """
    Service for interacting with Gemini 3 API using google.genai client.
    
    Features:
    - Context caching (90% cost savings)
    - File upload and reuse
    - Structured output with JSON Schema
    - Retry with exponential backoff
    """
    
    # JSON Schema for title block detection
    TITLE_BLOCK_SCHEMA = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "pdf_page": {"type": "INTEGER"},
                "schematic_page": {"type": "INTEGER", "nullable": True},
                "schematic_total": {"type": "INTEGER", "nullable": True},
                "dwg_no": {"type": "STRING", "nullable": True},
                "drawing_title": {"type": "STRING", "nullable": True},
                "confidence": {"type": "NUMBER"},
                "raw_text": {"type": "STRING", "nullable": True}
            },
            "required": ["pdf_page"]
        }
    }
    
    # JSON Schema for component extraction
    EXTRACTION_SCHEMA = {
        "type": "OBJECT",
        "properties": {
            "components": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "mark": {"type": "STRING"},
                        "symbol": {"type": "STRING", "nullable": True},
                        "name": {"type": "STRING", "nullable": True},
                        "type": {"type": "STRING", "nullable": True},
                        "x": {"type": "NUMBER", "nullable": True},
                        "y": {"type": "NUMBER", "nullable": True},
                        "width": {"type": "NUMBER", "nullable": True},
                        "height": {"type": "NUMBER", "nullable": True},
                        "description": {"type": "STRING", "nullable": True}
                    },
                    "required": ["mark"]
                }
            },
            "connections": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "from_component_mark": {"type": "STRING", "nullable": True},
                        "to_component_mark": {"type": "STRING", "nullable": True},
                        "wire_label": {"type": "STRING", "nullable": True},
                        "terminal_from": {"type": "STRING", "nullable": True},
                        "terminal_to": {"type": "STRING", "nullable": True},
                        "path": {
                            "type": "ARRAY",
                            "items": {
                                "type": "ARRAY",
                                "items": {"type": "NUMBER"}
                            }
                        },
                        "is_external": {"type": "BOOLEAN"}
                    }
                }
            },
            "wire_labels": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "label": {"type": "STRING"},
                        "x": {"type": "NUMBER", "nullable": True},
                        "y": {"type": "NUMBER", "nullable": True}
                    },
                    "required": ["label"]
                }
            },
            "continuations": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "from_component_mark": {"type": "STRING", "nullable": True},
                        "to_page_hint": {"type": "STRING", "nullable": True},
                        "direction": {"type": "STRING", "nullable": True}
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
        
        logger.info(f"Initializing Gemini service with models: {Config.GEMINI_MODEL}, {Config.GEMINI_FLASH_MODEL}")
        
        # Initialize client
        self.client = genai.Client(api_key=Config.GEMINI_API_KEY)
        
        self.model_name = Config.GEMINI_MODEL
        self.flash_model_name = Config.GEMINI_FLASH_MODEL
        self.temperature = Config.GEMINI_TEMPERATURE
        self.timeout = Config.GEMINI_TIMEOUT
        self.max_retries = Config.GEMINI_MAX_RETRIES
        
        # Cache storage
        self._file_cache: Dict[str, Any] = {}  # path -> uploaded file object
        self._content_cache: Dict[str, Any] = {}  # file_path -> cached content object
    
    def upload_file(self, file_path: Path, display_name: Optional[str] = None) -> Any:
        """
        Upload file to Gemini Files API.
        
        Args:
            file_path: Path to PDF file
            display_name: Optional display name
            
        Returns:
            Uploaded file object
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Check cache
        cache_key = str(file_path)
        if cache_key in self._file_cache:
            logger.info(f"Using cached file upload: {file_path.name}")
            return self._file_cache[cache_key]
        
        logger.info(f"Uploading file to Gemini: {file_path.name}")
        
        # Upload with retry
        for attempt in range(self.max_retries):
            try:
                uploaded_file = self.client.files.upload(path=str(file_path))
                logger.info(f"File uploaded: {uploaded_file.name}")
                
                self._file_cache[cache_key] = uploaded_file
                return uploaded_file
                
            except Exception as e:
                logger.error(f"Upload attempt {attempt + 1} failed: {e}")
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(self._calculate_backoff(attempt))
        
        raise RuntimeError("File upload failed after max retries")
    
    def create_cached_content(
        self,
        uploaded_file: Any,
        system_instruction: Optional[str] = None,
        use_flash: bool = True,
        ttl: str = "3600s"
    ) -> Any:
        """
        Create cached content for reuse across multiple requests.
        
        Args:
            uploaded_file: Uploaded file object from upload_file()
            system_instruction: Optional system instruction
            use_flash: Use flash model (faster, cheaper)
            ttl: Time to live (e.g., "3600s" for 1 hour)
            
        Returns:
            Cached content object
        """
        model = self.flash_model_name if use_flash else self.model_name
        
        # Check if already cached
        cache_key = f"{uploaded_file.name}_{model}"
        if cache_key in self._content_cache:
            logger.info(f"Using existing cached content: {cache_key}")
            return self._content_cache[cache_key]
        
        logger.info(f"Creating cached content with model: {model}")
        
        config = {"contents": [uploaded_file]}
        if system_instruction:
            config["system_instruction"] = system_instruction
        
        try:
            cached_content = self.client.caches.create(
                model=model,
                config=config,
                ttl=ttl
            )
            logger.info(f"Cached content created: {cached_content.name}")
            
            self._content_cache[cache_key] = cached_content
            return cached_content
            
        except Exception as e:
            logger.error(f"Failed to create cached content: {e}")
            raise
    
    def detect_title_blocks(
        self,
        cached_content: Any,
        pdf_page_indices: List[int]
    ) -> Dict[int, Dict[str, Any]]:
        """
        Detect title blocks for all pages in one call using cached PDF.
        
        Args:
            cached_content: Cached content object
            pdf_page_indices: List of 0-based page indices
            
        Returns:
            Dict mapping pdf_page_index -> title block data
        """
        page_list = ", ".join([str(idx + 1) for idx in pdf_page_indices])
        
        prompt = f"""You are analyzing an industrial schematic diagram PDF.

For each of the following PDF pages: {page_list}

Examine the title block (usually at the bottom-right corner of each page).

Extract:
1. **Schematic page number**: Found in a diagonal box format "X/Y" (e.g., "1/207" means schematic page 1 of 207 total). The X is the schematic page number we need. May be in Japanese full-width numbers (１/２０７).
2. **DWG NO.**: Drawing number, explicitly labeled "DWG NO." on the schematic.
3. **Drawing title**: The title of the drawing (e.g., "MAIN POWER POWER LAMP"). Infer from context in the title block - usually the largest or most prominent text.

Return a JSON array with one object per PDF page:
{{
  "pdf_page": <1-based PDF page number>,
  "schematic_page": <X from X/Y or null>,
  "schematic_total": <Y from X/Y or null>,
  "dwg_no": <drawing number or null>,
  "drawing_title": <inferred title or null>,
  "confidence": <0.0-1.0>,
  "raw_text": <optional: raw title block text for debugging>
}}

Example:
[
  {{
    "pdf_page": 7,
    "schematic_page": 1,
    "schematic_total": 207,
    "dwg_no": "151-E8810-202-0",
    "drawing_title": "MAIN POWER POWER LAMP",
    "confidence": 0.95,
    "raw_text": "..."
  }}
]

Only return the JSON array, nothing else."""
        
        try:
            logger.info(f"Detecting title blocks for PDF pages: {page_list}")
            
            response = self.client.models.generate_content(
                model=self.flash_model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    cached_content=cached_content.name,
                    temperature=0.1,
                    response_mime_type="application/json",
                    response_schema=self.TITLE_BLOCK_SCHEMA
                )
            )
            
            result = json.loads(response.text)
            logger.debug(f"Title block detection response: {result}")
            
            # Convert to dict keyed by 0-based pdf_page_index
            mapping = {}
            for item in result:
                pdf_page = item.get("pdf_page")
                if pdf_page:
                    pdf_idx = pdf_page - 1  # Convert to 0-based
                    mapping[pdf_idx] = {
                        "schematic_page_number": item.get("schematic_page"),
                        "schematic_total": item.get("schematic_total"),
                        "dwg_no": item.get("dwg_no"),
                        "drawing_title": item.get("drawing_title"),
                        "confidence": item.get("confidence", 0.5),
                        "raw_text": item.get("raw_text")
                    }
            
            logger.info(f"Title blocks detected: {mapping}")
            return mapping
            
        except Exception as e:
            logger.error(f"Title block detection failed: {e}")
            # Return empty mapping
            return {idx: {
                "schematic_page_number": None,
                "schematic_total": None,
                "dwg_no": None,
                "drawing_title": None,
                "confidence": 0.0,
                "raw_text": None
            } for idx in pdf_page_indices}
    
    def extract_page(
        self,
        cached_content: Any,
        pdf_page_index: int,
        context_text: Optional[str] = None,
        page_mapping: Optional[Dict[int, Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Extract components, connections, and wire labels from a specific page.
        
        Args:
            cached_content: Cached content object
            pdf_page_index: 0-based page index
            context_text: Optional context (instructions, legend)
            page_mapping: Optional title block mapping
            
        Returns:
            Extracted data dictionary matching EXTRACTION_SCHEMA
        """
        prompt = self._build_extraction_prompt(pdf_page_index, context_text, page_mapping)
        
        try:
            logger.info(f"Extracting page {pdf_page_index + 1} with model {self.model_name}")
            
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    cached_content=cached_content.name,
                    temperature=self.temperature,
                    response_mime_type="application/json",
                    response_schema=self.EXTRACTION_SCHEMA
                )
            )
            
            result = json.loads(response.text)
            logger.debug(f"Extraction result: {len(result.get('components', []))} components, {len(result.get('connections', []))} connections")
            
            return result
            
        except Exception as e:
            logger.error(f"Extraction failed for page {pdf_page_index}: {e}")
            raise
    
    def _build_extraction_prompt(
        self,
        pdf_page_index: int,
        context_text: Optional[str],
        page_mapping: Optional[Dict[int, Dict[str, Any]]]
    ) -> str:
        """Build extraction prompt with context."""
        pdf_page_num = pdf_page_index + 1
        
        prompt = f"""You are analyzing an industrial electrical schematic diagram.

**Page to analyze**: PDF page {pdf_page_num}"""
        
        if page_mapping and pdf_page_index in page_mapping:
            info = page_mapping[pdf_page_index]
            prompt += f"""
**Page info from title block**:
- Schematic page: {info.get('schematic_page_number', '?')}
- Drawing No.: {info.get('dwg_no', 'N/A')}
- Title: {info.get('drawing_title', 'N/A')}"""
        
        if context_text:
            prompt += f"""

**Reading instructions and legend**:
{context_text[:2000]}  
"""
        
        prompt += """

**Task**: Extract ALL electrical components, connections, wire labels, and continuations from this page with 100% accuracy.

**Requirements**:
1. **Components**: Every component with its mark (e.g., MCB10, SOL-1), symbol type, name, position (x, y), and dimensions if visible.
2. **Connections**: Every wire connection between components, including wire labels, terminal designations, and path coordinates.
3. **Wire labels**: Every wire label visible on the page with its text and position.
4. **Continuations**: Any continuation markers (e.g., "→5", "P.12") showing connections to other pages.

Extract coordinates as accurately as possible for tracing and overlay purposes.

Return a JSON object matching the schema provided."""
        
        return prompt
    
    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay."""
        base_delay = Config.RETRY_BASE_DELAY
        max_delay = Config.RETRY_MAX_DELAY
        delay = min(base_delay * (2 ** attempt), max_delay)
        return delay
