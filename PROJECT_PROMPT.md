# Industrial Schematic Analysis Application - Project Prompt

## Project Overview

Create a comprehensive web application for analyzing industrial schematic diagrams (specifically UBE diecast machine schematics) that extracts all components, connections, wire labels, and related information from PDF documents, stores them in a database, and provides a search/query interface for maintenance engineers to trace circuits and view component information.

## Core Requirements

### 1. PDF Extraction & Parsing
- Extract **100% accurate and complete** data from schematic PDFs - must be a perfect mirror of the schematic
- Extract all electrical components (solenoid valves, contactors, relays, limit switches, pressure switches, flow switches, motors, transformers, etc.)
- Extract all wire connections and paths with coordinates
- Extract all wire labels and numbers
- Extract terminal numbers and connection points
- Extract component positions (x, y coordinates) for overlay rendering
- Support multiple PDF types:
  - Schematic diagrams (main document)
  - Cable lists (files with "list" in filename)
  - Parts lists (files with "parts" and "list" in filename)
  - Terminal box wiring diagrams (files with "terminal" and "diagram" in filename)

### 2. Gemini 3 API Integration
- Use Gemini 3 API for PDF analysis with **ultra_high** media resolution for small text
- Use **context caching** for 90% cost savings on repeated PDF processing
- Upload PDFs once to cache, then reference cached content in subsequent API calls
- Support both Gemini 3 Pro (complex analysis) and Gemini 3 Flash (bulk operations)
- Extract pages 2 (reading instructions) and 3 (symbol legend) as separate PDFs for visual context
- Include context pages in API calls so Gemini can reference manufacturer-specific notation and symbol definitions

### 3. Database Design
- Store components with: symbol, name, mark, type, page number, coordinates (x, y, width, height), description
- Store connections between components with wire labels, paths, terminal numbers
- Store wire labels with positions
- Store cable list entries: cable numbers, wire numbers, wire colors, connections, component associations
- Store parts list entries: component marks, part numbers, part names, manufacturers, descriptions, quantities
- Store terminal box wiring: terminal numbers, wire numbers, component connections
- Cross-reference table to link schematic components with cable/parts/terminal data
- Support relational queries between all data sources

### 4. Circuit Tracing
- Trace connections between components
- Find all components connected to a given component
- Trace wire labels through the circuit
- Find related components via connections
- Support natural language queries (e.g., "shot forward solenoid valve")

### 5. PDF Overlay Rendering
- Generate PDF overlays highlighting:
  - Selected component (red highlight)
  - Related/connected components (blue highlight)
  - Wire labels (green highlights)
  - Connection paths (orange lines)
- Use coordinates from database to position overlays accurately
- Support coordinate system conversion (pdfplumber top-left origin vs PyMuPDF bottom-left origin)

### 6. Web Interface
- Search interface for components (natural language or component marks)
- Display search results as clickable list
- Display PDF with overlay when component is selected
- Show component details, connections, wire labels
- Display cross-referenced data (cables, parts, terminals) for each component
- Initialize database button to trigger extraction from PDFs

### 7. Cross-Referencing
- Match schematic components with cable list entries
- Match schematic components with parts list entries  
- Match schematic components with terminal box wiring entries
- Use component marks and wire numbers for matching
- Normalize component mark variations (e.g., SOL-1 vs SOL1)
- Calculate match confidence scores
- Store relationships in cross-reference table

## Technical Specifications

### Technology Stack
- **Backend**: Python, Flask
- **Database**: SQLite with SQLAlchemy ORM
- **PDF Processing**: 
  - PyMuPDF (fitz) for PDF manipulation and rendering
  - pdfplumber for text extraction
  - Gemini 3 API for intelligent extraction
- **AI/ML**: Google Gemini 3 API with context caching
- **Frontend**: HTML/CSS/JavaScript (vanilla, no framework needed)

### Gemini 3 Configuration
- Model: Gemini 3 Pro (complex analysis), Gemini 3 Flash (bulk operations)
- Media Resolution: `ultra_high` for small schematic text
- Context Caching: Enabled (90% cost savings)
- Temperature: 0.1 for accurate extraction
- Use Files API for PDF upload and caching

### Data Accuracy Requirements
- Must extract EVERY component, wire, label, and connection
- Must use manufacturer's symbol legend (page 3) for component identification
- Must follow reading instructions (page 2) for diagram conventions
- Coordinates must be accurate for overlay positioning
- Cross-references must be correctly matched with confidence scores

### API Endpoints Required
- `GET /` - Main web interface
- `GET /api/search?q=<term>` - Search components
- `GET /api/trace/<component_id>` - Get circuit trace for component
- `GET /api/trace/mark/<mark>` - Get trace by component mark
- `GET /api/pdf/trace/<component_id>` - Get PDF with overlay
- `GET /api/cross-ref/<component_id>` - Get cross-referenced data
- `GET /api/components` - List all components
- `POST /api/init` - Initialize database from PDFs (uses Gemini 3)

### File Structure
- Main schematic PDF: `01_SCHEMATIC DIAGRAM_151-E8810-202-0.pdf`
- Manual folder contains all related PDFs
- Database file: `schematic_analysis.db`
- Environment file: `.env` with `GEMINI_API_KEY`

## Key Features

1. **100% Accurate Extraction**: Must extract all elements with perfect accuracy
2. **Multi-PDF Processing**: Process schematic + cable lists + parts lists + terminal diagrams
3. **Intelligent Cross-Referencing**: Link related data across multiple document types
4. **Visual Overlays**: Highlight components and connections on original PDF
5. **Natural Language Search**: Find components using descriptions
6. **Cost Optimization**: Use context caching to minimize API costs
7. **Fail-Fast Design**: No silent fallbacks - all errors should be explicit

## Success Criteria

- All components from schematic are extracted and stored
- All connections/wires are extracted with paths
- All wire labels are extracted with positions
- Cross-references correctly link components across document types
- Circuit traces show complete connection paths
- PDF overlays accurately highlight components and connections
- Search finds components using various query formats
- Application handles errors explicitly (fail-fast design)

## Questions to Consider in Planning

1. How to handle very large schematics (200+ pages)?
2. How to validate extraction completeness?
3. How to handle coordinate system differences between PDF libraries?
4. How to optimize Gemini API usage and costs?
5. How to handle updates when PDFs change?
6. How to improve cross-reference matching accuracy?
7. How to handle partial matches or ambiguous component marks?
8. Should we support multiple machines/schematics in one database?

## Example Use Cases

1. Maintenance engineer searches "shot forward solenoid" → finds SOL-1 → views circuit trace with all connections → sees PDF overlay highlighting the component
2. Engineer queries component mark "MC1" → sees all connections, related components, cable information, parts list entry, terminal connections
3. Engineer traces wire label "25" → sees all components connected via that wire
4. System administrator initializes database → processes all PDFs → creates cross-references → database ready for queries

