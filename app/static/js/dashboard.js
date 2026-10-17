let vendors = [];
let trucks = [];
let inventory = {};
let cachedWeather = "28.5°C";

async function getWeather(locationName = "Noida") {
    try {
        const res = await fetch(`/api/weather?location=${encodeURIComponent(locationName)}`);
        const data = await res.json();
        cachedWeather = data.summary;
        const weatherEl = document.getElementById('liveWeatherDisplay');
        if (weatherEl) weatherEl.textContent = cachedWeather;
        return cachedWeather;
    } catch (e) {
        return "28°C · MODERATE RISK";
    }
}

async function refreshData() {
    try {
        const invRes = await fetch('/api/inventory');
        inventory = await invRes.json();
        document.getElementById('milk0days').textContent = inventory.milk_0days + 'L';
        document.getElementById('milk1day').textContent = inventory.milk_1day + 'L';
        document.getElementById('milk2days').textContent = inventory.milk_2days + 'L';
        const totalEl = document.getElementById('kpiTotalStock');
        if (totalEl) totalEl.textContent = inventory.total_milk + ' L';

        const truckRes = await fetch('/api/trucks');
        trucks = await truckRes.json();
        renderTrucks();

        const venRes = await fetch('/api/vendors');
        vendors = await venRes.json();
        renderVendors();
        updateKPIs();

        document.getElementById('lastUpdate').textContent = `Last sync: ${new Date().toLocaleTimeString()}`;
    } catch(e) {
        console.error('Refresh failed:', e);
    }
}

function renderTrucks() {
    const container = document.getElementById('trucksList');
    if (!container) return;

    container.innerHTML = trucks.map(t => {
        const pct = Math.min(100, Math.round((t.current_load / t.capacity) * 100));
        const isTransit = t.status === 'in_transit';
        return `
            <div class="truck-item">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <strong style="font-size: 0.9rem;">🚚 ${t.name}</strong>
                    <span class="badge ${t.status}">${isTransit ? 'IN TRANSIT · ' + t.assigned_route.toUpperCase() : 'STANDBY (ANY ROUTE)'}</span>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.8rem; color: #64748b; margin-top: 6px;">
                    <span>Payload: <strong>${t.current_load}L / ${t.capacity}L</strong> (${pct}%)</span>
                    <span>${isTransit ? 'ETA: ' + t.eta : 'Ready for assignment'}</span>
                </div>
                <div class="truck-bar-bg">
                    <div class="truck-bar-fill" style="width: ${pct}%; background: ${pct >= 100 ? '#059669' : '#2563eb'};"></div>
                </div>
                ${isTransit ? `
                    <button class="btn btn-outline" onclick="returnTruck(${t.id})" style="width:100%; margin-top:4px; padding:5px; font-size:0.76rem;">
                        Mark Trip Completed & Recall Unit
                    </button>
                ` : ''}
            </div>
        `;
    }).join('');
}

async function returnTruck(truckId) {
    await fetch(`/api/trucks/${truckId}/return`, { method: 'POST' });
    await refreshData();
}

function updateKPIs() {
    const totalDispatched = vendors
        .filter(v => (v.status || '').toLowerCase() === 'accepted')
        .reduce((sum, v) => sum + (parseFloat(v.assigned_quantity) || 0), 0);

    const kpiDisp = document.getElementById('kpiDispatched');
    const kpiRev = document.getElementById('kpiRevenue');
    if (kpiDisp) kpiDisp.textContent = `${totalDispatched.toFixed(1)} L`;
    if (kpiRev) kpiRev.textContent = `₹${(totalDispatched * 55).toLocaleString('en-IN')}`;
}

function renderVendors() {
    const container = document.getElementById('vendorsList');
    if(!container) return;
    if(vendors.length === 0) {
        container.innerHTML = '<p style="color:#64748b; font-size:0.9rem;">No vendor orders in queue. Submit orders via the Vendor Portal.</p>';
        return;
    }
    
    container.innerHTML = vendors.map(v => {
        const status = (v.status || 'pending').toLowerCase();
        
        const assignedInfo = status === 'accepted' 
            ? `<div style="margin-top:8px; padding:8px 10px; background:#f0fdf4; border-radius:6px; color:#065f46; font-size:0.82rem;">
                 <strong>Dispatched:</strong> ${v.assigned_quantity}L &nbsp;|&nbsp; <strong>Unit:</strong> ${v.truck_name || 'Reefer'} &nbsp;|&nbsp; <strong>ETA:</strong> ${v.eta || '--'}
               </div>` 
            : (status === 'rejected' ? `<div style="margin-top:6px; color:#991b1b; font-size:0.82rem; font-weight:600;">Order Unfulfilled (Stock / Capacity Limit)</div>` : '');

        const actionButtons = status === 'pending' ? `
            <div class="vendor-actions">
                <button class="btn btn-accept" onclick="manualDecision(${v.id}, '${v.name.replace(/'/g, "\\'")}', 'accepted', ${v.required_milk})">
                    Approve & Dispatch (${v.required_milk}L)
                </button>
                <button class="btn btn-reject" onclick="manualDecision(${v.id}, '${v.name.replace(/'/g, "\\'")}', 'rejected', 0)">
                    Decline Order
                </button>
            </div>
        ` : '';

        return `
            <div class="vendor-item">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <strong style="font-size: 0.95rem;">${v.name}</strong>
                    <span class="badge ${status}">${status}</span>
                </div>
                <div style="font-size: 0.83rem; color: #475569; margin-top: 4px;">
                    Route: <strong>${v.location}</strong> (${v.distance_km} km) &nbsp;·&nbsp; Demand: <strong>${v.required_milk}L</strong> &nbsp;·&nbsp; SLA Rating: ${v.rating}★
                </div>
                ${assignedInfo}
                ${actionButtons}
            </div>
        `;
    }).join('');
}

async function manualDecision(vendorId, vendorName, decisionStatus, quantity) {
    try {
        const res = await fetch(`/api/notify/${vendorId}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                status: decisionStatus,
                quantity: parseFloat(quantity) || 0.0
            })
        });

        const data = await res.json();
        if (res.ok) {
            await refreshData();
        } else {
            alert(`Cannot Dispatch: ${data.detail || 'Error'}`);
        }
    } catch (err) {
        console.error("Manual decision error:", err);
    }
}

async function handleOrdersByAI() {
    const summaryBox = document.getElementById('aiAutoSummary');
    summaryBox.style.display = 'block';
    summaryBox.innerHTML = '<strong>Running FEFO Stock Depletion & Multi-Route Reefer Allocation...</strong>';

    try {
        const weather = await getWeather("Noida");
        const res = await fetch('/api/ai/handle-orders', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                inventory: inventory,
                weather: weather
            })
        });

        const data = await res.json();
        const decisionsHtml = (data.decisions || []).map(d => 
            `<li style="margin-bottom:4px;"><strong>${d.name}</strong>: <span class="badge ${d.status}">${d.status}</span> (${d.assigned_quantity}L) — <span style="color:#475569;">${d.reason}</span></li>`
        ).join('');

        summaryBox.innerHTML = `
            <div style="font-weight: 700; color: #1e40af; margin-bottom: 6px;">AI Dispatch Execution Report</div>
            <p style="margin: 0 0 10px 0; color: #334155;">${data.summary}</p>
            ${decisionsHtml ? `<ul style="margin: 0; padding-left: 18px;">${decisionsHtml}</ul>` : ''}
        `;

        await refreshData();
    } catch (err) {
        summaryBox.innerHTML = 'Error executing AI dispatch.';
    }
}

async function askAI() {
    const query = document.getElementById('aiQuery').value;
    if(!query) return;
    
    const aiDiv = document.getElementById('aiResponse');
    aiDiv.innerHTML = 'Analyzing telemetry & fleet availability...';
    
    try {
        const weather = await getWeather("Noida");
        const response = await fetch('/api/ai/recommend', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                query: query,
                inventory: inventory,
                weather: weather
            })
        });
        
        const result = await response.json();
        const matchedVendor = vendors.find(v => v.name.trim().toLowerCase() === result.vendor.trim().toLowerCase()) || vendors[0];
        const targetId = matchedVendor ? matchedVendor.id : 0;

        aiDiv.innerHTML = `
            <div style="background: #f8fafc; border: 1px solid #e2e8f0; padding: 12px; border-radius: 8px; margin-top: 8px;">
                <div style="font-weight: 700; color: #0f172a;">Recommended: ${result.vendor} (${result.quantity}L · ETA ${result.delivery_time})</div>
                <p style="margin: 6px 0; color: #475569;">${result.reasoning}</p>
                ${targetId ? `
                    <button class="btn btn-accept" onclick="manualDecision(${targetId}, '${result.vendor.replace(/'/g, "\\'")}', 'accepted', ${result.quantity})">
                        Approve & Dispatch ${result.vendor}
                    </button>
                ` : ''}
            </div>
        `;
        document.getElementById('aiQuery').value = '';
    } catch(e) {
        aiDiv.innerHTML = 'Unable to reach AI advisor.';
    }
}

async function triggerDisruption() {
    await fetch('/api/inventory/disrupt', { method: 'POST' });
    await refreshData();
}

async function resetInventory() {
    await fetch('/api/inventory/reset', { method: 'POST' });
    document.getElementById('aiAutoSummary').style.display = 'none';
    await refreshData();
}

window.refreshData = refreshData;
window.manualDecision = manualDecision;
window.handleOrdersByAI = handleOrdersByAI;
window.askAI = askAI;
window.triggerDisruption = triggerDisruption;
window.resetInventory = resetInventory;
window.returnTruck = returnTruck;

getWeather("Noida");
refreshData();
setInterval(refreshData, 5000);