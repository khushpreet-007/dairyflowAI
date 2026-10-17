let vendors = [];
let inventory = {};
let cachedWeather = "Loading...";

async function getWeather(locationName = "Noida") {
    try {
        const res = await fetch(`/api/weather?location=${encodeURIComponent(locationName)}`);
        const data = await res.json();
        cachedWeather = data.summary;
        const weatherEl = document.getElementById('liveWeatherDisplay');
        if (weatherEl) weatherEl.textContent = cachedWeather;
        return cachedWeather;
    } catch (e) {
        return "28°C, Clear [MODERATE RISK]";
    }
}

async function refreshData() {
    try {
        // 1. Get live inventory from DB
        const invRes = await fetch('/api/inventory');
        inventory = await invRes.json();
        
        const el0 = document.getElementById('milk0days');
        if (el0) el0.textContent = inventory.milk_0days + 'L';
        document.getElementById('milk1day').textContent = inventory.milk_1day + 'L';
        document.getElementById('milk2days').textContent = inventory.milk_2days + 'L';
        
        // 2. Get vendors
        const venRes = await fetch('/api/vendors');
        vendors = await venRes.json();
        renderVendors();
        updateKPIs();
        
        document.getElementById('lastUpdate').textContent = `Last sync: ${new Date().toLocaleTimeString()}`;
    } catch(e) {
        console.error('Refresh failed:', e);
    }
}

function updateKPIs() {
    // Calculate total milk allocated to accepted vendors
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
        container.innerHTML = '<p>No vendor requests yet. Waiting for registrations...</p>';
        return;
    }
    
    container.innerHTML = vendors.map(v => {
        const status = (v.status || 'pending').toLowerCase();
        
        const assignedInfo = status === 'accepted' 
            ? `<span style="color: #15803d; font-weight: bold;"> | ✅ Dispatched: ${v.assigned_quantity}L</span>` 
            : (status === 'rejected' ? `<span style="color: #b91c1c; font-weight: bold;"> | ❌ Order Rejected</span>` : '');

        const actionButtons = status === 'pending' ? `
            <div class="vendor-actions">
                <button class="btn-accept" onclick="manualDecision(${v.id}, '${v.name.replace(/'/g, "\\'")}', 'accepted', ${v.required_milk})">
                    ✅ Accept & Send Notification (${v.required_milk}L)
                </button>
                <button class="btn-reject" onclick="manualDecision(${v.id}, '${v.name.replace(/'/g, "\\'")}', 'rejected', 0)">
                    ❌ Reject & Send Notification
                </button>
            </div>
        ` : '';

        return `
            <div class="vendor-item ${status}" style="border: 1px solid #e2e8f0; padding: 14px; border-radius: 10px; margin-bottom: 10px; background: #fff;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <h4 style="margin: 0;">${v.name}</h4>
                    <span class="badge ${status}">${status.toUpperCase()}</span>
                </div>
                <p style="margin: 6px 0;">📍 ${v.location} | 🥛 Demand: <strong>${v.required_milk}L</strong> | ⭐ ${v.rating} ${assignedInfo}</p>
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
            const actionLabel = decisionStatus === 'accepted' ? `ACCEPTED (${data.assigned_quantity}L deducted via FEFO)` : 'REJECTED';
            alert(`📱 Notification sent to ${vendorName}: Order ${actionLabel}`);
            await refreshData();
        } else {
            alert(`❌ Error: ${data.detail || 'Failed to update status'}`);
        }
    } catch (err) {
        console.error("Manual decision error:", err);
        alert("Network error sending notification.");
    }
}

async function handleOrdersByAI() {
    const summaryBox = document.getElementById('aiAutoSummary');
    summaryBox.style.display = 'block';
    summaryBox.innerHTML = '🤖 <strong>Gemini AI is running FEFO inventory optimization & thermal routing...</strong>';

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
            `<li><strong>${d.name}</strong>: <span class="badge ${d.status}">${d.status.toUpperCase()}</span> (${d.assigned_quantity}L) — <em>${d.reason}</em></li>`
        ).join('');

        summaryBox.innerHTML = `
            <h4 style="margin: 0 0 6px 0; color: #1d4ed8;">✨ AI FEFO Auto-Allocation Complete</h4>
            <p style="margin: 0 0 8px 0;">${data.summary}</p>
            ${decisionsHtml ? `<ul style="margin: 0; padding-left: 20px;">${decisionsHtml}</ul>` : ''}
        `;

        await refreshData();
    } catch (err) {
        console.error('AI Auto-Handle failed:', err);
        summaryBox.innerHTML = '❌ Failed to run AI auto-allocation.';
    }
}

async function askAI() {
    const query = document.getElementById('aiQuery').value;
    if(!query) return;
    
    const aiDiv = document.getElementById('aiResponse');
    aiDiv.innerHTML = '🤔 AI is analyzing live telemetry & stock...';
    
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
            <h3>✅ ${result.vendor} (Recommended)</h3>
            <div style="background: #e8f5e8; padding: 15px; border-radius: 10px; margin-bottom: 10px;">
                <strong>Reasoning:</strong> ${result.reasoning}
            </div>
            <p><strong>Recommended Quantity:</strong> ${result.quantity}L | <strong>Deliver by:</strong> ${result.delivery_time}</p>
            ${targetId ? `
                <button class="btn-accept" onclick="manualDecision(${targetId}, '${result.vendor.replace(/'/g, "\\'")}', 'accepted', ${result.quantity})">
                    📱 Accept & Dispatch (${result.quantity}L)
                </button>
            ` : ''}
        `;
        
        document.getElementById('aiQuery').value = '';
    } catch(e) {
        console.error(e);
        aiDiv.innerHTML = '❌ AI error. Try again.';
    }
}

async function triggerDisruption() {
    await fetch('/api/inventory/disrupt', { method: 'POST' });
    await refreshData();
    alert('🚨 CHILLER FAILURE SIMULATED: -7L lost from 1-day stock! Click "Handle Orders by AI" to re-optimize.');
}

async function resetInventory() {
    await fetch('/api/inventory/reset', { method: 'POST' });
    await refreshData();
}

window.refreshData = refreshData;
window.manualDecision = manualDecision;
window.handleOrdersByAI = handleOrdersByAI;
window.askAI = askAI;
window.triggerDisruption = triggerDisruption;
window.resetInventory = resetInventory;

// Initial load
getWeather("Noida");
refreshData();
setInterval(refreshData, 5000);