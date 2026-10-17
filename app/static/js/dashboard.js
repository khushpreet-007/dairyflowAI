let vendors = [];
let inventory = {};

async function refreshData() {
    try {
        // Get inventory
        const invRes = await fetch('/api/inventory');
        inventory = await invRes.json();
        document.getElementById('milk1day').textContent = inventory.milk_1day + 'L';
        document.getElementById('milk2days').textContent = inventory.milk_2days + 'L';
        
        // Get vendors
        const venRes = await fetch('/api/vendors');
        vendors = await venRes.json();
        renderVendors();
        
        document.getElementById('lastUpdate').textContent = `Last sync: ${new Date().toLocaleTimeString()}`;
    } catch(e) {
        console.error('Refresh failed:', e);
    }
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
            ? `<span style="color: #15803d; font-weight: bold;"> | ✅ Allocated: ${v.assigned_quantity}L</span>` 
            : (status === 'rejected' ? `<span style="color: #b91c1c; font-weight: bold;"> | ❌ Order Rejected</span>` : '');

        return `
            <div class="vendor-item ${status}" style="border: 1px solid #e2e8f0; padding: 14px; border-radius: 10px; margin-bottom: 10px; background: #fff;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <h4 style="margin: 0;">${v.name}</h4>
                    <span class="badge ${status}">${status.toUpperCase()}</span>
                </div>
                <p style="margin: 6px 0;">📍 ${v.location} | 🥛 Demand: <strong>${v.required_milk}L</strong> | ⭐ ${v.rating} ${assignedInfo}</p>
                
                <!-- Manual Accept / Reject Buttons for BMC Manager -->
                <div class="vendor-actions">
                    <button class="btn-accept" onclick="manualDecision(${v.id}, '${v.name}', 'accepted', ${v.required_milk})">
                        ✅ Accept (${v.required_milk}L)
                    </button>
                    <button class="btn-reject" onclick="manualDecision(${v.id}, '${v.name}', 'rejected', 0)">
                        ❌ Reject
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

// Manual Accept or Reject by BMC Manager
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

        if (res.ok) {
            const actionLabel = decisionStatus === 'accepted' ? `ACCEPTED (${quantity}L)` : 'REJECTED';
            alert(`📱 Notification sent to ${vendorName}: Order ${actionLabel}`);
            await refreshData();
        } else {
            alert('❌ Failed to update vendor order status.');
        }
    } catch (err) {
        console.error("Manual decision error:", err);
        alert("Network error sending notification.");
    }
}

// One-Click Automatic Order Handling by AI
async function handleOrdersByAI() {
    const summaryBox = document.getElementById('aiAutoSummary');
    summaryBox.style.display = 'block';
    summaryBox.innerHTML = '🤖 <strong>Gemini AI is evaluating all pending vendor orders against inventory...</strong>';

    try {
        const weather = await getWeather();
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
            <h4 style="margin: 0 0 6px 0; color: #1d4ed8;">✨ AI Auto-Allocation Complete</h4>
            <p style="margin: 0 0 8px 0;">${data.summary}</p>
            ${decisionsHtml ? `<ul style="margin: 0; padding-left: 20px;">${decisionsHtml}</ul>` : ''}
        `;

        await refreshData();
    } catch (err) {
        console.error('AI Auto-Handle failed:', err);
        summaryBox.innerHTML = '❌ Failed to run AI auto-allocation. You can use the manual buttons below.';
    }
}

// Single Chat Question to AI
async function askAI() {
    const query = document.getElementById('aiQuery').value;
    if(!query) return;
    
    const aiDiv = document.getElementById('aiResponse');
    aiDiv.innerHTML = '🤔 AI is analyzing...';
    
    try {
        const weather = await getWeather();
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
                <button class="btn-accept" onclick="manualDecision(${targetId}, '${result.vendor}', 'accepted', ${result.quantity})">
                    📱 Accept & Notify ${result.vendor}
                </button>
            ` : ''}
        `;
        
        document.getElementById('aiQuery').value = '';
    } catch(e) {
        console.error(e);
        aiDiv.innerHTML = '❌ AI error. Try again.';
    }
}

async function getWeather() {
    return "Rainy, 22°C";
}

async function triggerDisruption() {
    inventory.milk_1day -= 7;
    document.getElementById('milk1day').textContent = inventory.milk_1day + 'L';
    alert('🚨 YIELD DROP: -7L! Click "Handle Orders by AI" or ask AI for recommendations.');
}

// AUTO-REFRESH every 5s
setInterval(refreshData, 5000);
refreshData();