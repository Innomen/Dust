#!/usr/bin/env python3
"""
Dust - Linux Package Usage Tracker
A single-file web app to track unused packages on your system
"""

import os
import sys
import sqlite3
import subprocess
import json
from datetime import datetime, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading
import webbrowser
import time

# Configuration
DB_PATH = os.path.expanduser("~/.dust_tracker.db")
PORT = 8765

class DustTracker:
    def __init__(self):
        self.init_db()

    def init_db(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS packages (
                name TEXT PRIMARY KEY,
                description TEXT,
                install_date TEXT,
                explicit_install BOOLEAN,
                last_seen TEXT
            )
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                package_name TEXT,
                event_type TEXT,
                timestamp TEXT,
                FOREIGN KEY (package_name) REFERENCES packages (name)
            )
        ''')
        conn.commit()
        conn.close()

    def scan_installed_packages(self):
        """Scan all installed packages and update database"""
        try:
            # Get explicitly installed packages
            explicit = subprocess.check_output(['pacman', '-Qqe']).decode().strip().split('\n')
            explicit_set = set(explicit)

            # Get all packages with info
            all_packages = subprocess.check_output(['pacman', '-Qi']).decode()

            conn = sqlite3.connect(DB_PATH)

            current_pkg = {}
            for line in all_packages.split('\n'):
                if line.startswith('Name'):
                    if current_pkg:
                        self._save_package(conn, current_pkg, current_pkg['name'] in explicit_set)
                    current_pkg = {'name': line.split(':', 1)[1].strip()}
                elif line.startswith('Description'):
                    current_pkg['description'] = line.split(':', 1)[1].strip()
                elif line.startswith('Install Date'):
                    current_pkg['install_date'] = line.split(':', 1)[1].strip()

            if current_pkg:
                self._save_package(conn, current_pkg, current_pkg['name'] in explicit_set)

            conn.commit()
            conn.close()
            return True

        except subprocess.CalledProcessError as e:
            print(f"Error scanning packages: {e}")
            return False

    def _save_package(self, conn, pkg_info, is_explicit):
        """Save package info to database"""
        now = datetime.now().isoformat()
        conn.execute('''
            INSERT OR REPLACE INTO packages
            (name, description, install_date, explicit_install, last_seen)
            VALUES (?, ?, ?, ?, COALESCE((SELECT last_seen FROM packages WHERE name = ?), ?))
        ''', (
            pkg_info['name'],
            pkg_info.get('description', ''),
            pkg_info.get('install_date', ''),
            is_explicit,
            pkg_info['name'],
            now  # Default to "just installed/seen" instead of "Never"
        ))

    def scan_running_processes(self):
        """Scan currently running processes and update last_seen"""
        try:
            # Get all running processes
            processes = []
            for pid_dir in Path('/proc').iterdir():
                if pid_dir.is_dir() and pid_dir.name.isdigit():
                    try:
                        exe_path = (pid_dir / 'exe').readlink()
                        processes.append(str(exe_path))
                    except (OSError, PermissionError):
                        continue

            # Map processes to packages
            conn = sqlite3.connect(DB_PATH)
            now = datetime.now().isoformat()

            for exe_path in processes:
                try:
                    # Use pacman to find which package owns this file
                    result = subprocess.check_output(['pacman', '-Qo', exe_path],
                                                   stderr=subprocess.DEVNULL).decode()
                    package_name = result.split()[4]  # Extract package name from output

                    # Update last_seen
                    conn.execute('''
                        UPDATE packages SET last_seen = ? WHERE name = ?
                    ''', (now, package_name))

                    # Log usage event
                    conn.execute('''
                        INSERT INTO usage_events (package_name, event_type, timestamp)
                        VALUES (?, ?, ?)
                    ''', (package_name, 'process_scan', now))

                except (subprocess.CalledProcessError, IndexError):
                    continue

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            print(f"Error scanning processes: {e}")
            return False

    def get_package_stats(self):
        """Get package statistics for the web interface"""
        conn = sqlite3.connect(DB_PATH)

        # Get packages with dust levels
        cursor = conn.execute('''
            SELECT name, description, install_date, explicit_install, last_seen,
                   CASE
                       WHEN last_seen = 'Never' THEN 999
                       ELSE CAST((julianday('now') - julianday(last_seen)) AS INTEGER)
                   END as days_unused
            FROM packages
            ORDER BY days_unused DESC, name
        ''')

        packages = []
        for row in cursor:
            dust_level = min(row[5], 365)  # Cap at 365 days for visualization
            dust_percentage = min((dust_level / 30.0) * 100, 100)  # 30 days = 100% dusty

            packages.append({
                'name': row[0],
                'description': row[1],
                'install_date': row[2],
                'explicit_install': row[3],
                'last_seen': row[4],
                'days_unused': row[5],
                'dust_percentage': dust_percentage,
                'safety': 'safe' if row[3] and row[5] > 30 else 'risky'
            })

        # Get summary stats
        cursor = conn.execute('SELECT COUNT(*) FROM packages')
        total_packages = cursor.fetchone()[0]

        # Count packages that haven't been seen in recent scans (truly unused)
        cursor = conn.execute('''
            SELECT COUNT(*) FROM packages
            WHERE CAST((julianday('now') - julianday(last_seen)) AS INTEGER) > 7
        ''')
        unused_week = cursor.fetchone()[0]

        cursor = conn.execute('''
            SELECT COUNT(*) FROM packages
            WHERE explicit_install = 1
            AND CAST((julianday('now') - julianday(last_seen)) AS INTEGER) > 30
            AND last_seen != 'Never'
        ''')
        dusty_explicit = cursor.fetchone()[0]

        conn.close()

        return {
            'packages': packages,
            'stats': {
                'total': total_packages,
                'unused_week': unused_week,
                'dusty_explicit': dusty_explicit
            }
        }

# Web server
class DustHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, tracker=None, **kwargs):
        self.tracker = tracker
        super().__init__(*args, **kwargs)

    def do_GET(self):
        """Handle GET requests"""
        path = urlparse(self.path).path

        if path == '/':
            self._serve_html()
        elif path == '/api/stats':
            self._serve_json(self.tracker.get_package_stats())
        elif path == '/api/scan':
            # Run scans
            pkg_result = self.tracker.scan_installed_packages()
            proc_result = self.tracker.scan_running_processes()
            self._serve_json({
                'success': pkg_result and proc_result,
                'message': 'Scan completed',
                'timestamp': datetime.now().isoformat()
            })
        else:
            self.send_error(404)

    def _serve_html(self):
        """Serve the main HTML interface"""
        html = HTML_TEMPLATE
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def _serve_json(self, data):
        """Serve JSON response"""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        """Suppress server logs"""
        pass

# HTML Template (embedded in the Python file)
HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🧹 Dust - Package Tracker</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🧹</text></svg>">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            color: #333;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }

        .header {
            background: rgba(255, 255, 255, 0.95);
            padding: 20px;
            border-radius: 15px;
            margin-bottom: 20px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
            backdrop-filter: blur(10px);
        }

        .header h1 {
            color: #4a5568;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .dust-icon {
            font-size: 1.2em;
        }

        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-top: 15px;
        }

        .stat-card {
            background: rgba(255, 255, 255, 0.8);
            padding: 15px;
            border-radius: 10px;
            text-align: center;
        }

        .stat-number {
            font-size: 2em;
            font-weight: bold;
            color: #667eea;
        }

        .controls {
            background: rgba(255, 255, 255, 0.95);
            padding: 15px;
            border-radius: 15px;
            margin-bottom: 20px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
        }

        button {
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 25px;
            cursor: pointer;
            font-size: 14px;
            transition: transform 0.2s;
        }

        button:hover {
            transform: translateY(-2px);
        }

        .package-list {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            overflow: hidden;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.1);
        }

        .package-item {
            display: grid;
            grid-template-columns: 1fr 200px 100px 80px;
            gap: 15px;
            padding: 15px 20px;
            border-bottom: 1px solid #e2e8f0;
            align-items: center;
            transition: background 0.2s;
        }

        .package-item:hover {
            background: rgba(102, 126, 234, 0.05);
        }

        .package-name {
            font-weight: bold;
            color: #2d3748;
        }

        .package-desc {
            color: #718096;
            font-size: 0.9em;
            margin-top: 3px;
        }

        .dust-bar {
            width: 100%;
            height: 8px;
            background: #e2e8f0;
            border-radius: 4px;
            overflow: hidden;
        }

        .dust-fill {
            height: 100%;
            border-radius: 4px;
            transition: width 0.3s ease;
        }

        .dust-0-25 { background: linear-gradient(90deg, #48bb78, #38a169); }
        .dust-25-50 { background: linear-gradient(90deg, #ed8936, #dd6b20); }
        .dust-50-75 { background: linear-gradient(90deg, #e53e3e, #c53030); }
        .dust-75-100 { background: linear-gradient(90deg, #9f7aea, #805ad5); }

        .safety-badge {
            padding: 4px 8px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: bold;
        }

        .safety-safe {
            background: #c6f6d5;
            color: #22543d;
        }

        .safety-risky {
            background: #fed7d7;
            color: #742a2a;
        }

        .loading {
            text-align: center;
            padding: 40px;
            color: #718096;
        }

        .filter-controls {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
        }

        .filter-btn {
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 12px;
        }

        .filter-btn.active {
            background: linear-gradient(135deg, #48bb78, #38a169);
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1><span class="dust-icon">🧹</span>Dust - Package Usage Tracker</h1>
            <p>Track unused applications and services on your Linux system</p>

            <div class="stats" id="stats">
                <div class="stat-card">
                    <div class="stat-number" id="total-packages">-</div>
                    <div>Total Packages</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" id="unused-week">-</div>
                    <div>Unused (7+ days)</div>
                </div>
                <div class="stat-card">
                    <div class="stat-number" id="dusty-explicit">-</div>
                    <div>Dusty (30+ days)</div>
                </div>
            </div>
        </div>

        <div class="controls">
            <button onclick="runScan()" id="scan-btn">🔍 Scan System</button>
            <button onclick="loadData()" id="refresh-btn">🔄 Refresh</button>

            <div class="filter-controls" style="margin-top: 10px;">
                <button class="filter-btn active" onclick="filterPackages('all')">All</button>
                <button class="filter-btn" onclick="filterPackages('dusty')">Dusty (30+ days)</button>
                <button class="filter-btn" onclick="filterPackages('unused')">Unused (7+ days)</button>
                <button class="filter-btn" onclick="filterPackages('safe')">Safe to Remove</button>
            </div>

            <div style="margin-top: 10px; font-size: 0.9em; color: #666;">
                <span id="auto-scan-status">Auto-scan: Waiting...</span> |
                <span>Last scan: <span id="last-scan">Never</span></span>
            </div>
        </div>

        <div class="package-list" id="package-list">
            <div class="loading">Loading package data...</div>
        </div>
    </div>

    <script>
        let allPackages = [];
        let currentFilter = 'all';
        let autoScanInterval;
        let scanCounter = 0;

        // Load settings from localStorage
        function loadSettings() {
            const settings = JSON.parse(localStorage.getItem('dust_settings') || '{}');
            return {
                autoScanEnabled: settings.autoScanEnabled !== false, // default true
                scanIntervalMinutes: settings.scanIntervalMinutes || 15,
                lastScanTime: settings.lastScanTime || null
            };
        }

        // Save settings to localStorage
        function saveSettings(settings) {
            localStorage.setItem('dust_settings', JSON.stringify(settings));
        }

        // Start auto-scanning
        function startAutoScan() {
            const settings = loadSettings();
            if (!settings.autoScanEnabled) return;

            const intervalMs = settings.scanIntervalMinutes * 60 * 1000;

            autoScanInterval = setInterval(async () => {
                scanCounter++;
                updateScanStatus(`Auto-scan: Running (${scanCounter})...`);

                try {
                    await runScan(true); // silent scan
                    updateScanStatus(`Auto-scan: Active (every ${settings.scanIntervalMinutes}m)`);
                } catch (error) {
                    console.error('Auto-scan failed:', error);
                    updateScanStatus(`Auto-scan: Error (retrying...)`);
                }
            }, intervalMs);

            updateScanStatus(`Auto-scan: Active (every ${settings.scanIntervalMinutes}m)`);
        }

        // Update scan status display
        function updateScanStatus(message) {
            document.getElementById('auto-scan-status').textContent = message;
        }

        async function runScan(silent = false) {
            const btn = document.getElementById('scan-btn');
            if (!silent) {
                btn.disabled = true;
                btn.textContent = '🔍 Scanning...';
            }

            try {
                const response = await fetch('/api/scan');
                const result = await response.json();

                // Save last scan time
                const settings = loadSettings();
                settings.lastScanTime = result.timestamp || new Date().toISOString();
                saveSettings(settings);
                updateLastScanDisplay(settings.lastScanTime);

                if (result.success) {
                    if (!silent) {
                        btn.textContent = '✅ Scan Complete';
                        setTimeout(() => {
                            btn.textContent = '🔍 Scan System';
                            btn.disabled = false;
                        }, 2000);
                    }
                    loadData();
                } else {
                    if (!silent) {
                        btn.textContent = '❌ Scan Failed';
                        setTimeout(() => {
                            btn.textContent = '🔍 Scan System';
                            btn.disabled = false;
                        }, 2000);
                    }
                }
            } catch (error) {
                console.error('Scan failed:', error);
                if (!silent) {
                    btn.textContent = '❌ Scan Failed';
                    setTimeout(() => {
                        btn.textContent = '🔍 Scan System';
                        btn.disabled = false;
                    }, 2000);
                }
            }
        }

        // Update last scan display
        function updateLastScanDisplay(timestamp) {
            if (!timestamp) {
                document.getElementById('last-scan').textContent = 'Never';
                return;
            }

            const scanTime = new Date(timestamp);
            const now = new Date();
            const diffMinutes = Math.floor((now - scanTime) / (1000 * 60));

            let display;
            if (diffMinutes < 1) display = 'Just now';
            else if (diffMinutes < 60) display = `${diffMinutes}m ago`;
            else if (diffMinutes < 1440) display = `${Math.floor(diffMinutes / 60)}h ago`;
            else display = `${Math.floor(diffMinutes / 1440)}d ago`;

            document.getElementById('last-scan').textContent = display;
        }

        async function loadData() {
            try {
                const response = await fetch('/api/stats');
                const data = await response.json();

                // Update stats
                document.getElementById('total-packages').textContent = data.stats.total;
                document.getElementById('unused-week').textContent = data.stats.unused_week;
                document.getElementById('dusty-explicit').textContent = data.stats.dusty_explicit;

                // Store packages and render
                allPackages = data.packages;
                renderPackages();

            } catch (error) {
                console.error('Failed to load data:', error);
                document.getElementById('package-list').innerHTML =
                    '<div class="loading">Failed to load data. Try running a scan first.</div>';
            }
        }

        function renderPackages() {
            const filtered = filterPackagesList(allPackages, currentFilter);
            const html = filtered.map(pkg => {
                const dustClass = getDustClass(pkg.dust_percentage);
                const safetyClass = pkg.safety === 'safe' ? 'safety-safe' : 'safety-risky';

                return `
                    <div class="package-item">
                        <div>
                            <div class="package-name">${pkg.name}</div>
                            <div class="package-desc">${pkg.description}</div>
                        </div>
                        <div>
                            <div style="font-size: 0.9em; margin-bottom: 5px;">
                                Last used: ${pkg.days_unused === 999 ? 'Unknown' :
                                           pkg.days_unused === 0 ? 'Today' :
                                           pkg.days_unused + ' days ago'}
                            </div>
                            <div class="dust-bar">
                                <div class="dust-fill ${dustClass}" style="width: ${pkg.dust_percentage}%"></div>
                            </div>
                        </div>
                        <div class="safety-badge ${safetyClass}">
                            ${pkg.safety}
                        </div>
                        <div style="font-size: 0.8em; color: #718096;">
                            ${pkg.explicit_install ? 'Explicit' : 'Dependency'}
                        </div>
                    </div>
                `;
            }).join('');

            document.getElementById('package-list').innerHTML = html || '<div class="loading">No packages match current filter.</div>';
        }

        function filterPackages(filter) {
            currentFilter = filter;

            // Update button states
            document.querySelectorAll('.filter-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            event.target.classList.add('active');

            renderPackages();
        }

        function filterPackagesList(packages, filter) {
            switch (filter) {
                case 'dusty':
                    return packages.filter(pkg => pkg.days_unused >= 30 && pkg.days_unused < 999);
                case 'unused':
                    return packages.filter(pkg => pkg.days_unused >= 7);
                case 'safe':
                    return packages.filter(pkg => pkg.safety === 'safe');
                default:
                    return packages;
            }
        }

        function getDustClass(percentage) {
            if (percentage <= 25) return 'dust-0-25';
            if (percentage <= 50) return 'dust-25-50';
            if (percentage <= 75) return 'dust-50-75';
            return 'dust-75-100';
        }

        // Initialize app
        function initApp() {
            loadData();
            startAutoScan();

            // Update last scan display
            const settings = loadSettings();
            updateLastScanDisplay(settings.lastScanTime);

            // Update last scan display every minute
            setInterval(() => {
                const settings = loadSettings();
                updateLastScanDisplay(settings.lastScanTime);
            }, 60000);
        }

        // Start app when page loads
        initApp();

        // Auto-refresh data every 2 minutes (separate from scanning)
        setInterval(loadData, 120000);
    </script>
</body>
</html>'''

def create_systemd_service():
    """Create systemd user service for background operation"""
    script_path = os.path.abspath(__file__)
    service_dir = os.path.expanduser("~/.config/systemd/user")
    service_file = os.path.join(service_dir, "dust_tracker.service")

    service_content = f"""[Unit]
Description=Dust Package Usage Tracker
After=graphical-session.target

[Service]
Type=simple
ExecStart={script_path} --headless
Restart=always
RestartSec=10
Environment=DRI_PRIME=1
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""

    try:
        os.makedirs(service_dir, exist_ok=True)
        with open(service_file, 'w') as f:
            f.write(service_content)

        # Reload systemd and enable service
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', '--user', 'enable', 'dust_tracker.service'], check=True)

        print(f"✅ Service created and enabled: {service_file}")
        print("📋 Service management commands:")
        print("   Start:   systemctl --user start dust_tracker.service")
        print("   Stop:    systemctl --user stop dust_tracker.service")
        print("   Status:  systemctl --user status dust_tracker.service")
        print("   Logs:    journalctl --user -u dust_tracker.service -f")
        print("   Disable: systemctl --user disable dust_tracker.service")

    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to create service: {e}")
        return False
    except Exception as e:
        print(f"❌ Error creating service: {e}")
        return False

    return True

def find_free_port(start_port=8765, max_attempts=10):
    """Find a free port starting from start_port"""
    import socket

    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('localhost', port))
                return port
        except OSError:
            continue
    return None

def check_if_running(port=PORT):
    """Check if Dust is already running on the given port"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(('localhost', port))
            # Try to make a simple request to confirm it's actually Dust
            s.send(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
            response = s.recv(1024).decode()
            return 'Dust' in response
    except:
        return False
    """Print usage help"""
    script_name = os.path.basename(__file__)
    print(f"""
🧹 Dust - Linux Package Usage Tracker

USAGE:
    {script_name}                    # Start web interface (default)
    {script_name} --headless         # Run as background service (no browser)
    {script_name} --scan-only        # Run single scan and exit
    {script_name} --install-service  # Create systemd user service
    {script_name} --help             # Show this help

WEB INTERFACE:
    • Runs on http://localhost:{PORT}
    • Auto-scans every 15 minutes
    • Visual dust accumulation interface
    • Filter by usage patterns

SERVICE MODE:
    • Install service: {script_name} --install-service
    • Runs in background, accessible via web
    • Starts automatically on login
    • Manage with: systemctl --user <start|stop|status> dust_tracker.service

DATABASE:
    • Location: {DB_PATH}
    • SQLite format, portable
    • Stores package usage history

EXAMPLES:
    {script_name}                    # Quick start - opens browser
    {script_name} --install-service  # Set up background service
    systemctl --user start dust_tracker.service  # Start service
    """)

def main():
    # Set environment to suppress DRI_PRIME warning
    os.environ.setdefault('DRI_PRIME', '1')

    if len(sys.argv) > 1:
        arg = sys.argv[1]

        if arg == '--help' or arg == '-h':
            print_help()
            return

        elif arg == '--install-service':
            print("🔧 Installing systemd user service...")
            if create_systemd_service():
                print("\n🎉 Service installed successfully!")
                print("   Run: systemctl --user start dust_tracker.service")
            return

        elif arg == '--scan-only':
            # Command line mode - single scan
            tracker = DustTracker()
            print("📦 Scanning installed packages...")
            pkg_result = tracker.scan_installed_packages()
            print("🔍 Scanning running processes...")
            proc_result = tracker.scan_running_processes()
            if pkg_result and proc_result:
                print("✅ Scan complete!")
                # Show quick stats
                stats = tracker.get_package_stats()
                print(f"   Total packages: {stats['stats']['total']}")
                print(f"   Unused (7+ days): {stats['stats']['unused_week']}")
                print(f"   Dusty explicit: {stats['stats']['dusty_explicit']}")
            else:
                print("❌ Scan failed!")
                sys.exit(1)
            return

        elif arg == '--headless':
            headless_mode = True
        else:
            print(f"❌ Unknown argument: {arg}")
            print(f"   Run '{sys.argv[0]} --help' for usage info")
            return
    else:
        headless_mode = False

    # Web server mode
    tracker = DustTracker()

    # Check if already running
    if check_if_running(PORT):
        print(f"🧹 Dust Tracker is already running on http://localhost:{PORT}")
        if not headless_mode:
            print("🌐 Opening existing instance in browser...")
            try:
                webbrowser.open(f'http://localhost:{PORT}')
            except Exception as e:
                print(f"   Manual access: http://localhost:{PORT}")
        else:
            print("   Already running in headless mode")
        return

    # Try to find a free port if default is taken
    actual_port = PORT
    try:
        server = HTTPServer(('localhost', PORT), lambda *args, **kwargs: DustHandler(*args, tracker=tracker, **kwargs))
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"⚠️  Port {PORT} is busy, finding alternative...")
            actual_port = find_free_port(PORT + 1)
            if actual_port is None:
                print("❌ No free ports available. Try stopping other services.")
                return
            print(f"🔄 Using port {actual_port} instead")
            server = HTTPServer(('localhost', actual_port), lambda *args, **kwargs: DustHandler(*args, tracker=tracker, **kwargs))
        else:
            raise

    print(f"🧹 Dust Tracker starting on http://localhost:{actual_port}")
    if headless_mode:
        print("🔇 Running in headless mode (no browser window)")
        print(f"   Access via: http://localhost:{actual_port}")
        print("   Stop with: systemctl --user stop dust_tracker.service")
        print("   Or kill this process")
    else:
        print("🌐 Opening browser...")
        print("💡 Tip: Pin this tab and let it run for continuous monitoring")

        # Open browser after a short delay
        def open_browser():
            time.sleep(1.5)
            try:
                webbrowser.open(f'http://localhost:{actual_port}')
            except Exception as e:
                print(f"⚠️  Couldn't open browser automatically: {e}")
                print(f"   Please open: http://localhost:{actual_port}")

        browser_thread = threading.Thread(target=open_browser)
        browser_thread.daemon = True
        browser_thread.start()

    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
        server.shutdown()

if __name__ == '__main__':
    main()
