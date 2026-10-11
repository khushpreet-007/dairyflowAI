let vendors = [];
let inventory = {};

async function refreshData() {
    try {
        // Get inventory
        const invRes = await fetch('/api/inventory');
        inventory = await invRes.json();
        document.getElementById('milk1day').textContent = inventory.milk_1day + 'L';
        document.getElementById('milk2days').textContent = inventory.milk_2days + 'L';
        
        // Get vendors (NEW REGISTRATIONS!)
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
    if(vendors.length === 0) {
        container.innerHTML = '<p>No new vendors. Waiting for registrations...</p>';
        return;
    }
    
    container.innerHTML = vendors.map(v => `
        <div class="vendor-item ${v.status}">
            <h4>${v.name}</h4>
            <p>📍 ${v.location} | 🥛 ${v.required_milk}L | ⭐ ${v.rating}</p>
            <small>${v.status.toUpperCase()}</small>
        </div>
    `).join('');
}

async function askAI() {
    const query = document.getElementById('aiQuery').value;
    if(!query) return;
    
    const aiDiv = document.getElementById('aiResponse');
    aiDiv.innerHTML = '🤔 AI is analyzing...';
    
    try {
        const weather = await getWeather(); // Simple weather
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
            <button onclick="notifyVendor('${result.vendor}')">📱 Send Notification</button>
        `;
        
        document.getElementById('aiQuery').value = '';
    } catch(e) {
        aiDiv.innerHTML = '❌ AI error. Try again.';
    }
}

async function notifyVendor(vendorName) {
    const vendor = vendors.find(v => v.name === vendorName);
    if(vendor) {
        await fetch(`/api/notify/${vendor.id}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({quantity: 5})
        });
        alert(`📱 Notification sent to ${vendorName}!`);
        refreshData();
    }
}

async function getWeather() {
    // Mock weather (replace with real API)
    return "Rainy, 22°C";
}

async function triggerDisruption() {
    // Simulate yield drop
    inventory.milk_1day -= 7;
    document.getElementById('milk1day').textContent = inventory.milk_1day + 'L';
    alert('🚨 YIELD DROP: -7L! Ask AI for recommendations.');
}

// AUTO-REFRESH every 5s (sees new vendor registrations!)
setInterval(refreshData, 5000);
refreshData(); // Initial load
