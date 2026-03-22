const API = "http://127.0.0.1:5000";
let currentRole = null;
let profile = null;

// --- DOM ELEMENTS ---
const sections = {
    landing: document.getElementById('section-landing'),
    login: document.getElementById('section-login'),
    student: document.getElementById('section-student'),
    staff: document.getElementById('section-staff')
};

const overlays = {
    hero: document.getElementById('bg-hero'),
    login: document.getElementById('bg-login'),
    student: document.getElementById('bg-student'),
    staff: document.getElementById('bg-staff')
};

// --- NAVIGATION ---
function showView(viewId) {
    // Hide all
    Object.values(sections).forEach(s => s.classList.add('hidden'));
    Object.values(overlays).forEach(o => o.classList.add('hidden'));
    
    // Show target section
    sections[viewId].classList.remove('hidden');
    
    // Background management
    if(viewId === 'landing') {
        overlays.hero.classList.remove('hidden');
    } else if(viewId === 'login') {
        overlays.login.classList.remove('hidden');
    } else if(viewId === 'student') {
        overlays.student.classList.remove('hidden');
    } else if(viewId === 'staff') {
        overlays.staff.classList.remove('hidden');
    }
    
    window.scrollTo(0, 0);
}

function initPortal(role) {
    currentRole = role;
    document.getElementById('portal-select').classList.add('hidden');
    document.getElementById('auth-form').classList.remove('hidden');
    document.getElementById('login-title').innerText = role === 'student' ? 'Student Link' : 'Faculty Key';
    document.getElementById('login-hint').innerText = role === 'student' ? 'Required: Registration ID & DOB' : 'Required: Staff ID & Secret Key';
}

function resetPortal() {
    currentRole = null;
    document.getElementById('portal-select').classList.remove('hidden');
    document.getElementById('auth-form').classList.add('hidden');
    document.getElementById('login-title').innerText = 'System Entry';
    document.getElementById('login-id').value = '';
    document.getElementById('login-pass').value = '';
}

function goHome() {
    resetPortal();
    showView('landing');
}

// --- AUTHENTICATION ---
async function handleLogin() {
    const id = document.getElementById('login-id').value.trim();
    const pass = document.getElementById('login-pass').value.trim();

    if(!id || !pass) return alert("System requires full credentials.");

    if(currentRole === 'staff') {
        if(pass === 'admin') {
            showView('staff');
        } else {
            alert("Entry Denied: Invalid Faculty Key.");
        }
        return;
    }

    try {
        const res = await fetch(`${API}/student/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reg_no: id, password: pass })
        });
        const data = await res.json();
        if(data.status === 'success') {
            profile = data.profile;
            setupDashboard();
            showView('student');
        } else {
            alert(data.message);
        }
    } catch(e) {
        alert("Fatal Error: Neural Link Timeout.");
    }
}

function setupDashboard() {
    document.getElementById('stu-name').innerText = profile.name;
    document.getElementById('stu-dept').innerText = `${profile.year} • ${profile.dept}`;
    document.getElementById('val-cgpa').innerText = profile.cgpa;
    document.getElementById('val-att').innerText = profile.attendance + '%';
    document.getElementById('val-proj').innerText = profile.projects.padStart(2, '0');
    document.getElementById('rec-id').innerText = profile.reg_no;
    document.getElementById('rec-email').innerText = profile.email;
}

// --- CHAT SYSTEM ---
async function sendQuery(role) {
    const inputId = role === 'student' ? 'stu-input' : 'staff-input';
    const boxId = role === 'student' ? 'stu-chat-box' : 'staff-chat-box';
    const input = document.getElementById(inputId);
    const box = document.getElementById(boxId);
    const text = input.value.trim();

    if(!text) return;

    addMsg(box, text, 'msg-user');
    input.value = '';

    const endpoint = role === 'student' ? '/student/chat' : '/chat';
    const body = role === 'student' ? { reg_no: profile.reg_no, message: text } : { message: text };

    try {
        const res = await fetch(`${API}${endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await res.json();

        if(data.type === 'chart') {
            addMsg(box, data.reply || "Analysis Complete.", 'msg-ai');
            renderChart(box, data.chart_data);
        } else {
            addMsg(box, data.reply || data.message || "Logic Error.", 'msg-ai');
        }
    } catch(e) {
        addMsg(box, "Fatal Error: Stream Interrupted.", 'msg-ai');
    }
}

function addMsg(box, text, cls) {
    const d = document.createElement('div');
    d.className = `msg ${cls}`;
    d.innerText = text;
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
}

function renderChart(box, data) {
    const wrap = document.createElement('div');
    wrap.className = 'bento-card';
    wrap.style.height = '300px';
    wrap.style.marginTop = '10px';
    wrap.innerHTML = `<canvas></canvas>`;
    box.appendChild(wrap);
    
    new Chart(wrap.querySelector('canvas'), {
        type: data.chart_type,
        data: {
            labels: data.labels,
            datasets: [{
                label: data.title,
                data: data.data,
                backgroundColor: ['#818cf8', '#c084fc', '#22d3ee', '#f472b6', '#fbbf24'],
                borderWidth: 0,
                borderRadius: 12
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { 
                legend: { labels: { color: '#a1a1aa', font: { family: 'Outfit', size: 12 } } }
            },
            scales: {
                y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#a1a1aa' } },
                x: { grid: { display: false }, ticks: { color: '#a1a1aa' } }
            }
        }
    });
    box.scrollTop = box.scrollHeight;
}

// Ensure icons load
document.addEventListener('DOMContentLoaded', () => {
    if(window.lucide) lucide.createIcons();
});
