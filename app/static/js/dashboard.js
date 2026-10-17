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
        container.innerHTML = '<p>No new vendors. Waiting for registrations...</p>';
        return;
    }
    
    container.innerHTML = vendors.map(v => `
        <div class="vendor-item ${v.status}">
            <h4>${v.name}</h4>
            <p>📍 ${v.location} | 🥛 ${v.required_milk}L | ⭐ ${v.rating}</p>
            <small>${(v.status || 'PENDING').toUpperCase()}</small>
        </div>
    `).join('');
}

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
        
        aiDiv.innerHTML = `
            <h3>✅ ${result.vendor} (Recommended)</h3>
            <div style="background: #e8f5e8; padding: 15px; border-radius: 10px;">
                <strong>Reasoning:</strong> ${result.reasoning}
            </div>
            <p><strong>Quantity:</strong> ${result.quantity}L | <strong>Deliver by:</strong> ${result.delivery_time}</p>
            <button onclick="notifyVendor('${result.vendor}', ${result.quantity})">📱 Send Notification</button>
        `;
        
        document.getElementById('aiQuery').value = '';
    } catch(e) {
        console.error(e);
        aiDiv.innerHTML = '❌ AI error. Try again.';
    }
}

async function notifyVendor(vendorName, quantity) {
    // Case-insensitive lookup to avoid slight naming mismatches
    const vendor = vendors.find(v => v.name.trim().toLowerCase() === vendorName.trim().toLowerCase()) 
                 || vendors[0]; // Fallback to first vendor if name differed slightly

    if(!vendor) {
        alert("No vendor found in records!");
        return;
    }

    try {
        const res = await fetch(`/api/notify/${vendor.id}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ quantity: parseFloat(quantity) || 30.0 })
        });

        if (res.ok) {
            alert(`📱 Notification sent to ${vendor.name} (${quantity || 30}L)!`);
            await refreshData();
        } else {
            const errData = await res.json();
            alert(`❌ Failed to update vendor: ${JSON.stringify(errData)}`);
        }
    } catch (err) {
        console.error("Notify failed:", err);
        alert("Network error sending notification.");
    }
}

async function getWeather() {
    return "Rainy, 22°C";
}

async function triggerDisruption() {
    inventory.milk_1day -= 7;
    document.getElementById('milk1day').textContent = inventory.milk_1day + 'L';
    alert('🚨 YIELD DROP: -7L! Ask AI for recommendations.');
}

// AUTO-REFRESH every 5s
setInterval(refreshData, 5000);
refreshData();