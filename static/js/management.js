/**
 * Phase 5.5 - Management Module
 * Events, Policies, and Hosts Management via REST API
 * 
 * Features:
 * - Event History Loader with real-time updates
 * - Policy Manager with enable/disable toggles
 * - Host Manager with connection testing
 * - Modals for create/edit operations
 * - Comprehensive error handling
 * - Dry-Run Mode support
 */

// ========================================
// Global Constants & Configuration
// ========================================

const API_BASE = '/api';
const API_EVENTS = API_BASE + '/events';
const API_POLICIES = API_BASE + '/policies';
const API_HOSTS = API_BASE + '/hosts';
const API_GROUPS = API_BASE + '/ups-groups';
const API_BATTERY = API_BASE + '/battery_status';  // REPARATUR: Battery Status API

// Dry-Run Mode Status (wird beim Laden aktualisiert)
let DRY_RUN_ENABLED = false;

const SEVERITY_COLORS = {
    'EMERGENCY': '#ef4444',  // Red
    'CRITICAL': '#f97316',   // Orange-Red
    'ERROR': '#f59e0b',      // Orange
    'WARNING': '#eab308',    // Yellow
    'INFO': '#3b82f6'        // Blue
};

const SEVERITY_CLASS = {
    'EMERGENCY': 'severity-emergency',
    'CRITICAL': 'severity-critical',
    'ERROR': 'severity-error',
    'WARNING': 'severity-warning',
    'INFO': 'severity-info'
};

// ========================================
// Battery Status Management (v20.1.1 REPARATUR)
// ========================================

/**
 * Lade und zeige Batterie-Status für die gewählte USV
 * Repariert das Problem: Battery-Tab zeigte nur "--%"
 */
async function populateBatteryProfiles() {
    const sel = document.getElementById('batteryProfileSelect');
    if (!sel || sel.options.length > 1) return; // bereits befüllt
    try {
        const res = await fetch('/api/profiles');
        if (!res.ok) return;
        (await res.json()).forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.name;
            opt.textContent = (p.display_name || p.name) + (p.enabled === false ? T('common.disabled_suffix') : '');
            sel.appendChild(opt);
        });
    } catch (e) { console.error('Profile laden Fehler:', e); }
}

async function loadBatteryStatus() {
    const sel = document.getElementById('batteryProfileSelect');
    const upsId = sel ? sel.value : '';
    const rec = document.getElementById('batteryRecommendation');
    if (rec) rec.textContent = '⏳ ' + T('bat.loading');
    try {
        const response = await fetch(`${API_BATTERY}?ups_id=${encodeURIComponent(upsId)}`);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            displayBatteryStatus({ status: T('grid.no_data_badge'), message: data.message || data.error || `HTTP ${response.status}`, lifetime: data.lifetime });
            return;
        }
        displayBatteryStatus(data);
    } catch (error) {
        console.error('❌ Battery Status Error:', error);
        displayBatteryStatus({ status: T('common.error'), message: T('common.error_prefix', { error: error.message }) });
    }
}

/** Formatiert Zahlen; fehlende Werte (null/undefined) werden als "--" angezeigt. */
function _fmtNum(value, digits, unit) {
    return (typeof value === 'number' && isFinite(value)) ? `${value.toFixed(digits)}${unit}` : `--${unit}`;
}

/** Bezeichnung eines Meldungstyps in der gewählten Sprache. */
function eventLabel(type) {
    const text = T('events.' + type);
    return text === 'events.' + type ? type : text;
}

/** Anzeigetext: Meldungen der USVs übersetzt; Protokoll der Regeln steht so in der Datenbank. */
function eventText(event) {
    if (event.source === 'AUTOMATION') return event.raw_message;
    const label = eventLabel(event.event_type);
    return label !== event.event_type ? label : event.raw_message;
}

/**
 * Zeige Batteriemetriken im Batterie-Tab an
 */
function displayBatteryStatus(data) {
    data = data || {};
    const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };

    set('batTabCharge', _fmtNum(data.battery_charge, 1, ' %'));
    set('batteryTemp', _fmtNum(data.battery_temperature, 1, ' °C'));
    set('batTabVoltage', _fmtNum(data.battery_voltage, 1, ' V'));
    set('batteryRuntime', _fmtNum(data.battery_runtime_minutes, 1, ' min'));
    set('batteryStatus', data.status || '--');
    set('batteryLoad', _fmtNum(data.ups_load, 1, ' %'));

    set('batteryCycles', typeof data.cycles_estimated === 'number' ? String(data.cycles_estimated) : '--');
    set('batteryLastEvent', data.last_battery_event
        ? new Date(data.last_battery_event * 1000).toLocaleString(window.LANG) : (data.success ? T('bat.none_yet') : '--'));
    const STATUS = {
        CRITICAL: ['🔴 ' + T('status.critical'), '#ef4444'],
        WARNING: ['⚠️ ' + T('status.warning'), '#f59e0b'],
        GOOD: ['✅ ' + T('status.good'), '#10b981'],
        UNKNOWN: ['❔ ' + T('status.unknown'), '#94a3b8'],
    };
    const QUALITY = { GOOD: T('quality.good'), LIMITED: T('quality.limited'), INSUFFICIENT: T('quality.insufficient') };
    const paint = (id, status, text) => {
        const el = document.getElementById(id);
        if (!el) return;
        const [label, color] = STATUS[status] || ['--', '#94a3b8'];
        el.textContent = text || label;
        el.style.color = color;
    };

    const cond = data.condition || {};
    const life = data.lifetime || {};
    const dq = data.data_quality;
    paint('batteryRisk', cond.status);
    paint('batteryLifespan', life.status, typeof life.months === 'number' ? T('bat.months_value', { months: life.months }) : T('bat.unknown'));
    set('batteryDataQuality', dq ? `${QUALITY[dq.level] || dq.level} (${dq.available.length}/${dq.available.length + dq.missing.length})` : '--');
    paint('batteryWarning', data.risk_level, data.overall_text);

    if (window.AutoBattery) window.AutoBattery.show(data);

    const rec = document.getElementById('batteryRecommendation');
    if (rec) {
        const lines = [];
        if (data.message) lines.push(`ℹ️ ${data.message}`);
        if (data.profile) lines.push(`📍 ${data.profile}`);
        if (data.battery_installed) {
            const installed = new Date(data.battery_installed).toLocaleDateString(window.LANG);
            lines.push('🔋 ' + T('bat.installed_line', { date: installed, years: data.battery_age_years.toFixed(1) }));
        }
        (cond.findings || []).forEach(f => lines.push((cond.status === 'UNKNOWN' ? 'ℹ️ ' : '⚠️ ') + f));
        (cond.evidence || []).forEach(e => lines.push(`✅ ${e}`));
        (life.findings || []).forEach(f => lines.push(`⚠️ ${f}`));
        if (life.basis) lines.push('📅 ' + T('bat.lifetime_line', { basis: life.basis }));
        if (dq && dq.missing.length) lines.push('📋 ' + T('pred.missing', { items: dq.missing.join(' · ') }));
        (data.hints || []).forEach(h => lines.push(`ℹ️ ${h}`));
        rec.style.whiteSpace = 'pre-line';
        rec.textContent = lines.join('\n');
    }
}


// ========================================
// Event Management
// ========================================

/**
 * Load and display event history
 */
async function loadEvents() {
    const hours = parseInt(document.getElementById('eventsHours').value || 24);
    const tableBody = document.getElementById('eventsTableBody');
    
    // Show loading state
    tableBody.innerHTML = '<tr><td colspan="6" class="text-center"><div class="spinner"></div> ' + T('ev.loading') + '</td></tr>';
    
    try {
        // Fetch event history
        const historyResponse = await fetch(
            `${API_EVENTS}/history?hours=${hours}&limit=500`,
            { headers: { 'Accept': 'application/json' } }
        );
        
        if (!historyResponse.ok) {
            if (historyResponse.status === 403) {
                showAlert(T('ev.forbidden'), 'error');
            } else {
                throw new Error(`API error: ${historyResponse.status}`);
            }
            return;
        }
        
        const historyData = await historyResponse.json();
        const events = historyData.events || [];
        
        // Fetch active events
        let activeEvents = {};
        try {
            const activeResponse = await fetch(`${API_EVENTS}/active`);
            if (activeResponse.ok) {
                const activeData = await activeResponse.json();
                activeEvents = activeData.active_events || {};
            }
        } catch (e) {
            console.debug('Could not fetch active events');
        }
        
        // Render event table
        if (events.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="6" class="text-center">' + T('ev.none') + '</td></tr>';
        } else {
            tableBody.innerHTML = events.map(event => renderEventRow(event)).join('');
        }
        
        // Render active events widget
        renderActiveEvents(activeEvents);
        
    } catch (error) {
        console.error('Error loading events:', error);
        tableBody.innerHTML = `<tr><td colspan="6" class="text-center" style="color:#fca5a5;">❌ ${T('ev.load_failed_short', { error: error.message })}</td></tr>`;
        showAlert(T('ev.load_failed', { error: error.message }), 'error');
    }
}

/**
 * Render a single event row
 */
function renderEventRow(event) {
    const timestamp = new Date(event.timestamp * 1000).toLocaleString(window.LANG);
    const severity = event.severity || 'INFO';
    const severityClass = SEVERITY_CLASS[severity] || 'severity-info';
    const eventType = event.event_type || 'UNKNOWN_EVENT';
    const upsId = event.ups_id || '-';
    const source = event.source || '-';
    
    const details = eventText(event) ||
                   (event.battery_charge ? T('ev.charge', { value: event.battery_charge.toFixed(0) }) :
                    event.ups_load ? T('ev.load', { value: event.ups_load.toFixed(0) }) : '-');
    
    return `
        <tr>
            <td style="font-family: monospace; font-size: 0.8rem;">${timestamp}</td>
            <td><code style="background:rgba(255,255,255,0.05); padding:2px 6px; border-radius:3px;">${eventLabel(eventType)}</code></td>
            <td>${upsId}</td>
            <td>
                <span class="severity-badge ${severityClass}">
                    ${severity}
                </span>
            </td>
            <td>${source}</td>
            <td style="color:#cbd5e1;">${details}</td>
        </tr>
    `;
}

/**
 * Render active events widget
 */
function renderActiveEvents(activeEvents) {
    const widget = document.getElementById('activeEventsWidget');
    const list = document.getElementById('activeEventsList');
    
    const totalActive = Object.values(activeEvents).reduce((sum, events) => sum + events.length, 0);
    
    if (totalActive === 0) {
        widget.style.display = 'none';
        return;
    }
    
    widget.style.display = 'block';
    
    let html = '';
    Object.entries(activeEvents).forEach(([upsId, events]) => {
        events.forEach(event => {
            const severity = event.severity || 'INFO';
            const severityClass = SEVERITY_CLASS[severity] || 'severity-info';
            const since = new Date(event.since).toLocaleString(window.LANG);
            
            html += `
                <div class="card" style="border-left: 3px solid ${SEVERITY_COLORS[severity] || '#3b82f6'};">
                    <div style="display:flex; justify-content:space-between; align-items:start; margin-bottom:8px;">
                        <strong style="color:#e2e8f0;">${eventLabel(event.event_type)}</strong>
                        <span class="severity-badge ${severityClass}">${severity}</span>
                    </div>
                    <div style="font-size:0.8rem; color:#94a3b8;">
                        <div>📡 ${T('ev.ups_line', { ups: upsId })}</div>
                        <div>🕐 ${T('ev.since', { time: since })}</div>
                        ${event.battery_charge !== null ? `<div>🔋 ${T('ev.charge', { value: event.battery_charge.toFixed(0) })}</div>` : ''}
                        ${event.battery_runtime !== null ? `<div>⏱️ ${T('ev.runtime', { value: event.battery_runtime.toFixed(0) })}</div>` : ''}
                    </div>
                </div>
            `;
        });
    });
    
    list.innerHTML = html;
}

// ========================================
// Tab Navigation
// ========================================

/**
 * Extended showTab function to handle new tabs
 */
const originalShowTab = window.showTab;
window.showTab = function(tabName) {
    // Call original showTab if it exists
    if (originalShowTab && typeof originalShowTab === 'function') {
        originalShowTab(tabName);
    } else {
        // Fallback tab implementation
        document.querySelectorAll('[id^="tab-"]').forEach(tab => {
            tab.style.display = 'none';
        });
        
        const targetTab = document.getElementById(`tab-${tabName}`);
        if (targetTab) {
            targetTab.style.display = 'block';
        }
        
        document.querySelectorAll('.nav-tab').forEach(navTab => {
            navTab.classList.remove('active');
        });
        
        event.target.classList.add('active');
    }
    
    // Load data when tab is shown
    if (tabName === 'events') {
        loadEvents();
    }
};

// ========================================
// Utility Functions
// ========================================

/**
 * Show alert message
 */
function showAlert(message, type = 'info') {
    // Try to find existing alert container or create one
    let alertContainer = document.getElementById('alertContainer');
    
    if (!alertContainer) {
        alertContainer = document.createElement('div');
        alertContainer.id = 'alertContainer';
        alertContainer.style.cssText = 'position:fixed; top:20px; right:20px; max-width:400px; z-index:9999;';
        document.body.appendChild(alertContainer);
    }
    
    // Add dry-run indicator if enabled
    let displayMessage = message;
    if (DRY_RUN_ENABLED && type === 'success') {
        displayMessage = `[Simulation] ${message}`;
    }
    
    const alertEl = document.createElement('div');
    alertEl.className = `alert alert-${type} show`;
    alertEl.textContent = displayMessage;
    alertEl.style.cssText = `
        padding: 12px 16px;
        margin-bottom: 8px;
        border-radius: 8px;
        font-size: 0.9rem;
        animation: slideIn 0.3s ease;
    `;
    
    alertContainer.appendChild(alertEl);
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        alertEl.remove();
    }, 5000);
}

/**
 * Escape HTML special characters
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========================================
// Auto-refresh
// ========================================

// Auto-refresh events every 30 seconds if tab is visible
setInterval(() => {
    const eventsTab = document.getElementById('tab-events');
    if (eventsTab && eventsTab.style.display !== 'none') {
        loadEvents().catch(e => console.debug('Auto-refresh events failed:', e));
    }
}, 30000);

// ========================================
/**
 * Load and display IPMI availability status
 */
let IPMI_AVAILABLE = false;
let IPMI_HOSTS = [];

async function loadIpmiStatus() {
    try {
        const response = await fetch('/api/config/ipmi-status');
        
        if (!response.ok) {
            console.warn('Could not fetch IPMI status');
            return;
        }
        
        const data = await response.json();
        IPMI_AVAILABLE = data.ipmi_available === true;
        IPMI_HOSTS = data.ipmi_hosts_configured || [];
        
        console.log(`ℹ️ IPMI Status: available=${IPMI_AVAILABLE}, hosts=${IPMI_HOSTS.length}`);
        
        // Show warning if IPMI unavailable but hosts are configured
        if (!IPMI_AVAILABLE && IPMI_HOSTS.length > 0) {
            console.warn('⚠️ IPMI WARNING - ipmitool not available but IPMI hosts configured');
            showIpmiWarningBanner();
        } else {
            hideIpmiWarningBanner();
        }
        
        // Disable IPMI test buttons if not available
        if (!IPMI_AVAILABLE) {
            disableIpmiTestButtons();
        }
        
    } catch (error) {
        console.error('Error loading IPMI status:', error);
        IPMI_AVAILABLE = false;
    }
}

/**
 * Show IPMI warning banner
 */
function showIpmiWarningBanner() {
    let warningContainer = document.getElementById('ipmiWarningContainer');
    
    if (!warningContainer) {
        warningContainer = document.createElement('div');
        warningContainer.id = 'ipmiWarningContainer';
        warningContainer.style.cssText = `
            position: fixed;
            top: 50px;
            left: 0;
            right: 0;
            z-index: 9998;
            animation: slideDown 0.3s ease;
        `;
        document.body.appendChild(warningContainer);
    }
    
    warningContainer.innerHTML = `
        <div style="
            background: linear-gradient(135deg, #ff9800, #f57c00);
            color: white;
            padding: 12px 20px;
            text-align: center;
            font-weight: 500;
            box-shadow: 0 2px 8px rgba(255, 152, 0, 0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
        ">
            <span style="font-size: 1.2rem;">⚠️</span>
            <span>IPMI-Warnung: 'ipmitool' fehlt auf dem Hostsystem. IPMI-Shutdowns für konfigurierte Server sind inaktiv!</span>
            <span style="font-size: 0.9rem; opacity: 0.9;">(${IPMI_HOSTS.length} Host(s) betroffen)</span>
        </div>
    `;
}

/**
 * Hide IPMI warning banner
 */
function hideIpmiWarningBanner() {
    const warningContainer = document.getElementById('ipmiWarningContainer');
    if (warningContainer) {
        warningContainer.remove();
    }
}

/**
 * Disable IPMI test buttons and show tooltip
 */
function disableIpmiTestButtons() {
    // Find all host cards
    const hostCards = document.querySelectorAll('[data-host-type="ipmi"]');
    
    hostCards.forEach(card => {
        // Find test button in this card
        const testButton = card.querySelector('[data-action="test"]');
        if (testButton) {
            testButton.disabled = true;
            testButton.style.opacity = '0.5';
            testButton.style.cursor = 'not-allowed';
            testButton.title = '❌ ' + T('ipmi.unavailable');
            
            // Add visual indicator
            testButton.classList.add('ipmi-unavailable');
        }
    });
}

// Initialization
// ========================================

console.log('✓ Phase 5.5 Management Module loaded');

// ========================================
// Automation Pause Toggle (v20.1.2)
// ========================================

/**
 * Toggle automation pause state via API
 * Sends POST request to /api/config/toggle-automation
 * Updates button UI based on response
 */
async function toggleAutomation() {
    const button = document.getElementById('automationToggle');
    if (!button) {
        console.warn('Automation toggle button not found');
        return;
    }
    
    try {
        button.disabled = true;
        button.style.opacity = '0.5';
        
        const response = await fetch('/api/config/toggle-automation', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        const data = await response.json();
        
        if (data.success) {
            updateAutomationButtonUI(data.automation_paused);
            showAlert(data.message, 'info');
            console.log(`✅ Automation Toggle: ${data.status_text}`);
        } else {
            showAlert(T('auto.toggle_failed', { error: data.error || T('common.unknown_error') }), 'error');
            console.error('Toggle automation failed:', data);
        }
    } catch (error) {
        console.error('❌ Automation toggle error:', error);
        showAlert(T('auto.toggle_failed', { error: error.message }), 'error');
    } finally {
        button.disabled = false;
        button.style.opacity = '1';
    }
}

/** Schreibt die gepufferten Messwerte sofort auf den Datenträger. */
async function syncDatabase(event) {
    event.preventDefault();
    try {
        const response = await fetch('/api/database/sync', { method: 'POST' });
        const data = await response.json();
        if (response.ok && data.success) {
            showAlert(data.message, 'info');
        } else {
            showAlert(data.error || data.detail || T('common.unknown_error'), 'error');
        }
    } catch (error) {
        showAlert(T('db.sync_failed'), 'error');
    }
}

/**
 * Update automation button UI based on pause state
 * @param {boolean} isPaused - Whether automation is paused
 */
function updateAutomationButtonUI(isPaused) {
    const button = document.getElementById('automationToggle');
    if (!button) return;
    
    if (isPaused) {
        button.classList.add('paused');
        button.textContent = '⏸️ ' + T('auto.paused');
        button.title = T('auto.paused_title');
    } else {
        button.classList.remove('paused');
        button.textContent = '▶️ ' + T('auto.active');
        button.title = T('auto.active_title');
    }
}

/**
 * Load and display current automation status on page load
 */
async function updateAutomationStatus() {
    try {
        const response = await fetch('/api/config/automation-status');
        const data = await response.json();
        
        if (data.success) {
            updateAutomationButtonUI(data.automation_paused);
            console.log(`📊 Automation Status: ${data.status_text}`);
        } else {
            console.warn('Could not load automation status:', data);
        }
    } catch (error) {
        console.debug('Error loading automation status:', error);
    }
}

// ========================================
// Load dry-run and IPMI status on page load
document.addEventListener('DOMContentLoaded', () => {
    loadIpmiStatus().catch(e => console.debug('Error loading IPMI status:', e));
    updateAutomationStatus().catch(e => console.debug('Error loading automation status:', e));
});

// Also call immediately in case DOM is already loaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        loadIpmiStatus();
        updateAutomationStatus();
    });
} else {
    loadIpmiStatus();
    updateAutomationStatus();
}
