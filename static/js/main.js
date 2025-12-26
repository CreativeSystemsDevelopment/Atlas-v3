/**
 * Schematic Extraction MVP - Frontend JavaScript
 * Handles file upload, streaming extraction, and search functionality.
 */

// State
let currentSchematicFileId = null;
let eventSource = null;

// DOM Elements (populated after DOMContentLoaded)
let elements = {};

// ============================================
// Upload Handlers
// ============================================

function updateUploadButton() {
    const hasFile = elements.pdfFile && elements.pdfFile.files.length > 0;
    const hasMachine = elements.machineName && elements.machineName.value.trim() !== '';
    if (elements.uploadBtn) {
        elements.uploadBtn.disabled = !(hasFile && hasMachine);
    }
}

async function handleUpload() {
    const file = elements.pdfFile.files[0];
    const machineName = elements.machineName.value.trim();
    
    if (!file || !machineName) return;
    
    const formData = new FormData();
    formData.append('file', file);
    formData.append('machine_name', machineName);
    
    try {
        elements.uploadBtn.disabled = true;
        elements.uploadBtn.textContent = 'Uploading...';
        
        const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (response.ok) {
            currentSchematicFileId = data.schematic_file_id;
            
            if (data.is_duplicate) {
                // Show duplicate warning
                elements.duplicateMessage.textContent = data.message;
                elements.duplicateWarning.classList.remove('hidden');
            } else {
                // Proceed to context pages
                showContextSection();
            }
        } else {
            alert(data.error || 'Upload failed');
        }
    } catch (error) {
        alert('Upload error: ' + error.message);
    } finally {
        elements.uploadBtn.disabled = false;
        elements.uploadBtn.textContent = 'Upload';
    }
}

async function handleReplace() {
    try {
        const response = await fetch(`/api/upload/${currentSchematicFileId}/replace`, {
            method: 'POST'
        });
        
        if (response.ok) {
            elements.duplicateWarning.classList.add('hidden');
            showContextSection();
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}

function handleSkip() {
    elements.duplicateWarning.classList.add('hidden');
    showContextSection();
}

// ============================================
// Context Pages & Extraction
// ============================================

function showContextSection() {
    elements.uploadSection.classList.add('hidden');
    elements.contextSection.classList.remove('hidden');
}

async function handleStartExtraction() {
    const instructionsPage = parseInt(elements.instructionsPage.value) - 1; // Convert to 0-based
    const legendPage = parseInt(elements.legendPage.value) - 1;
    
    try {
        // Start extraction
        const response = await fetch('/api/extract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                schematic_file_id: currentSchematicFileId,
                context_pages: [instructionsPage, legendPage]
            })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            elements.contextSection.classList.add('hidden');
            elements.extractionSection.classList.remove('hidden');
            
            // Start streaming
            startExtractionStream();
        } else {
            alert(data.error || 'Failed to start extraction');
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}

function startExtractionStream() {
    // Clear previous results
    elements.resultsBody.innerHTML = '';
    elements.extractionSummary.classList.add('hidden');
    
    // Connect to SSE stream
    eventSource = new EventSource(`/api/extract/${currentSchematicFileId}/stream`);
    
    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleExtractionEvent(data);
    };
    
    eventSource.onerror = (error) => {
        console.error('SSE error:', error);
        eventSource.close();
        elements.statusText.textContent = 'Connection error. Check console.';
    };
}

function handleExtractionEvent(event) {
    const { type, data } = event;
    
    switch (type) {
        case 'progress':
            updateProgress(data);
            break;
        case 'component':
            addResultRow('Component', data.mark, data.pdf_page_index, data.schematic_page_number, data.name || data.symbol);
            break;
        case 'connection':
            addResultRow('Connection', data.wire_label || '-', data.pdf_page_index, data.schematic_page_number, 
                `${data.from_component_mark || '?'} → ${data.to_component_mark || '?'}`);
            break;
        case 'wire_label':
            addResultRow('Wire Label', data.label, data.pdf_page_index, data.schematic_page_number, '');
            break;
        case 'continuation':
            addResultRow('Continuation', data.from_component_mark || '-', data.pdf_page_index, data.schematic_page_number, 
                `→ ${data.to_page_hint}`);
            break;
        case 'error':
            elements.statusText.textContent = 'Error: ' + data.error;
            if (eventSource) eventSource.close();
            break;
        case 'complete':
            handleExtractionComplete(data);
            break;
    }
}

function updateProgress(data) {
    elements.statusText.textContent = data.message || data.status;
    
    if (data.percent !== undefined) {
        elements.progressPercent.textContent = data.percent + '%';
        elements.progressFill.style.width = data.percent + '%';
    }
}

function addResultRow(type, mark, pdfPage, schematicPage, details) {
    const row = document.createElement('tr');
    
    const typeClass = {
        'Component': 'type-component',
        'Connection': 'type-connection',
        'Wire Label': 'type-wire-label',
        'Continuation': 'type-continuation'
    }[type] || '';
    
    row.innerHTML = `
        <td class="${typeClass}">${type}</td>
        <td>${escapeHtml(mark || '-')}</td>
        <td>${pdfPage !== undefined ? pdfPage + 1 : '-'}</td>
        <td>${schematicPage || '-'}</td>
        <td>${escapeHtml(details || '')}</td>
    `;
    
    elements.resultsBody.appendChild(row);
    
    // Auto-scroll to bottom
    const container = document.querySelector('.table-container');
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

function handleExtractionComplete(data) {
    if (eventSource) eventSource.close();
    
    elements.statusText.textContent = 'Extraction complete';
    elements.progressPercent.textContent = '100%';
    elements.progressFill.style.width = '100%';
    
    // Show summary
    elements.totalComponents.textContent = data.total_components || 0;
    elements.totalConnections.textContent = data.total_connections || 0;
    elements.totalWireLabels.textContent = data.total_wire_labels || 0;
    elements.extractionSummary.classList.remove('hidden');
    
    // Show search section
    elements.searchSection.classList.remove('hidden');
}

// ============================================
// Search
// ============================================

async function performSearch() {
    const query = elements.searchInput.value.trim();
    if (!query) return;
    
    try {
        const url = `/api/search?q=${encodeURIComponent(query)}&schematic_file_id=${currentSchematicFileId}`;
        const response = await fetch(url);
        const data = await response.json();
        
        if (response.ok) {
            displaySearchResults(data.results);
        } else {
            alert(data.error || 'Search failed');
        }
    } catch (error) {
        alert('Search error: ' + error.message);
    }
}

function displaySearchResults(results) {
    elements.searchResults.innerHTML = '';
    
    if (results.length === 0) {
        elements.searchResults.innerHTML = '<p class="hint">No results found</p>';
        return;
    }
    
    results.forEach(component => {
        const item = document.createElement('div');
        item.className = 'result-item glow-hover';
        item.innerHTML = `
            <span class="result-mark">${escapeHtml(component.mark)}</span>
            <span class="result-name">${escapeHtml(component.name || component.type || '')}</span>
            <span class="result-page">Page ${component.schematic_page_number || component.pdf_page_index + 1}</span>
        `;
        item.addEventListener('click', () => showComponentDetail(component.id));
        elements.searchResults.appendChild(item);
    });
}

// ============================================
// Component Detail
// ============================================

async function showComponentDetail(componentId) {
    try {
        const response = await fetch(`/api/trace/${componentId}`);
        const data = await response.json();
        
        if (response.ok) {
            displayComponentDetail(data);
            elements.searchSection.classList.add('hidden');
            elements.detailSection.classList.remove('hidden');
        } else {
            alert(data.error || 'Failed to load component');
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}

function displayComponentDetail(data) {
    const { component, connections, connected_components } = data;
    
    // Component info
    elements.componentInfo.innerHTML = `
        <div class="info-row">
            <span class="info-label">Mark</span>
            <span class="info-value">${escapeHtml(component.mark)}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Name</span>
            <span class="info-value">${escapeHtml(component.name || '-')}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Symbol</span>
            <span class="info-value">${escapeHtml(component.symbol || '-')}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Type</span>
            <span class="info-value">${escapeHtml(component.type || '-')}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Page</span>
            <span class="info-value">PDF ${component.pdf_page_index + 1} / Schematic ${component.schematic_page_number || '-'}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Position</span>
            <span class="info-value">(${component.x?.toFixed(1) || '-'}, ${component.y?.toFixed(1) || '-'})</span>
        </div>
    `;
    
    // Connections
    let connectionsHtml = '<h3>Connections</h3>';
    if (connections.length === 0) {
        connectionsHtml += '<p class="hint">No connections found</p>';
    } else {
        connections.forEach(conn => {
            connectionsHtml += `
                <div class="connection-item">
                    <span>${escapeHtml(conn.from_component_mark || '?')}</span>
                    <span> → </span>
                    <span>${escapeHtml(conn.to_component_mark || '?')}</span>
                    <span> (Wire: ${escapeHtml(conn.wire_label || '-')})</span>
                </div>
            `;
        });
    }
    elements.connectionsList.innerHTML = connectionsHtml;
    
    // Store current component ID for PDF view
    elements.viewPdfBtn.dataset.componentId = component.id;
}

function handleViewPdf() {
    const componentId = elements.viewPdfBtn.dataset.componentId;
    if (componentId) {
        window.open(`/api/pdf/trace/${componentId}`, '_blank');
    }
}

function handleBackToSearch() {
    elements.detailSection.classList.add('hidden');
    elements.searchSection.classList.remove('hidden');
}

// ============================================
// Utilities
// ============================================

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================
// Initialize
// ============================================

document.addEventListener('DOMContentLoaded', () => {
    console.log('Schematic Extraction MVP loaded');
    
    // Populate elements object
    elements = {
        // Upload section
        uploadSection: document.getElementById('upload-section'),
        machineName: document.getElementById('machine-name'),
        pdfFile: document.getElementById('pdf-file'),
        fileName: document.getElementById('file-name'),
        uploadBtn: document.getElementById('upload-btn'),
        duplicateWarning: document.getElementById('duplicate-warning'),
        duplicateMessage: document.getElementById('duplicate-message'),
        replaceBtn: document.getElementById('replace-btn'),
        skipBtn: document.getElementById('skip-btn'),
        
        // Context section
        contextSection: document.getElementById('context-section'),
        instructionsPage: document.getElementById('instructions-page'),
        legendPage: document.getElementById('legend-page'),
        startExtractionBtn: document.getElementById('start-extraction-btn'),
        
        // Extraction section
        extractionSection: document.getElementById('extraction-section'),
        statusText: document.getElementById('status-text'),
        progressPercent: document.getElementById('progress-percent'),
        progressFill: document.getElementById('progress-fill'),
        resultsBody: document.getElementById('results-body'),
        extractionSummary: document.getElementById('extraction-summary'),
        totalComponents: document.getElementById('total-components'),
        totalConnections: document.getElementById('total-connections'),
        totalWireLabels: document.getElementById('total-wire-labels'),
        
        // Search section
        searchSection: document.getElementById('search-section'),
        searchInput: document.getElementById('search-input'),
        searchBtn: document.getElementById('search-btn'),
        searchResults: document.getElementById('search-results'),
        
        // Detail section
        detailSection: document.getElementById('detail-section'),
        componentInfo: document.getElementById('component-info'),
        connectionsList: document.getElementById('connections-list'),
        viewPdfBtn: document.getElementById('view-pdf-btn'),
        backToSearchBtn: document.getElementById('back-to-search-btn')
    };
    
    // File label click handler - triggers file input
    const fileLabel = document.getElementById('file-label');
    if (fileLabel && elements.pdfFile) {
        fileLabel.addEventListener('click', () => {
            elements.pdfFile.click();
        });
    }
    
    // File input change handler
    if (elements.pdfFile) {
        elements.pdfFile.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (file && elements.fileName) {
                elements.fileName.textContent = file.name;
                updateUploadButton();
            }
        });
    }
    
    // Machine name input handler
    if (elements.machineName) {
        elements.machineName.addEventListener('input', updateUploadButton);
    }
    
    // Upload button handler
    if (elements.uploadBtn) {
        elements.uploadBtn.addEventListener('click', handleUpload);
    }
    
    // Replace button handler
    if (elements.replaceBtn) {
        elements.replaceBtn.addEventListener('click', handleReplace);
    }
    
    // Skip button handler
    if (elements.skipBtn) {
        elements.skipBtn.addEventListener('click', handleSkip);
    }
    
    // Start extraction button handler
    if (elements.startExtractionBtn) {
        elements.startExtractionBtn.addEventListener('click', handleStartExtraction);
    }
    
    // Search handlers
    if (elements.searchBtn) {
        elements.searchBtn.addEventListener('click', performSearch);
    }
    if (elements.searchInput) {
        elements.searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') performSearch();
        });
    }
    
    // Detail section handlers
    if (elements.viewPdfBtn) {
        elements.viewPdfBtn.addEventListener('click', handleViewPdf);
    }
    if (elements.backToSearchBtn) {
        elements.backToSearchBtn.addEventListener('click', handleBackToSearch);
    }
});
