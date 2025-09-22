# 🧹 Dust - Linux Package Usage Tracker

A single-file web application that tracks which packages and applications you actually use on your Linux system. Over time, unused packages accumulate "dust" - making it easy to identify what's safe to remove and keep your system lean.

## Why Dust?

Most Linux package managers can tell you what's installed, but not what you actually *use*. Dust solves this by monitoring your system and tracking when packages were last active. Think of it like `lastlog` but for your entire software collection.

Unlike typical system cleaners that only remove orphaned dependencies, Dust identifies explicitly-installed packages that you simply don't use anymore - the real source of system bloat.

## Features

- **Visual dust accumulation** - unused packages literally look dusty in the interface
- **Smart tracking** - monitors running processes and maps them back to packages
- **Auto-scanning** - runs background scans every 15 minutes when active
- **Safe removal guidance** - identifies explicitly-installed packages that haven't been used
- **Single file** - entire application in one Python script, no dependencies
- **Self-managing** - can install itself as a systemd service
- **Browser-based UI** - modern, responsive interface that works in any browser

## Screenshots

<img width="1211" height="739" alt="dust Screenshot_20250921_205310" src="https://github.com/user-attachments/assets/5395e2f9-b035-4f5f-b19a-87975c64ec2c" />

*The main interface showing packages with their "dust levels" - redder bars indicate longer periods without use*

## Quick Start

1. **Download the script** (make it executable):
   ```bash
   chmod +x dust_tracker.py
   ```

2. **Run it**:
   ```bash
   ./dust_tracker.py
   ```
   
3. **Click "Scan System"** to start tracking
4. **Pin the browser tab** and let it run

That's it! Dust will auto-scan every 15 minutes and build up usage patterns over time.

## Installation Options

### Web Interface (Recommended)
```bash
./dust_tracker.py                    # Opens browser, pin the tab
```

### Background Service
```bash
./dust_tracker.py --install-service  # Creates systemd user service
systemctl --user start dust_tracker.service
```

### One-time Scan
```bash
./dust_tracker.py --scan-only        # Quick scan and exit
```

### All Options
```bash
./dust_tracker.py --help             # Full usage guide
```

## How It Works

Dust uses multiple detection methods to track package usage:

1. **Process Scanning** - Checks what's currently running and maps processes to packages
2. **Persistent Tracking** - Stores usage history in a local SQLite database
3. **Smart Defaults** - New packages start as "recently used" until proven otherwise
4. **Dust Accumulation** - Unused packages become "dustier" over days/weeks

The "dust" metaphor reflects real-world behavior - items you don't use literally collect dust. The interface visualizes this with color-coded bars that get redder as packages go unused longer.

## Safety Features

- **Conservative removal suggestions** - only flags explicitly-installed packages
- **Dependency awareness** - won't suggest removing packages that others depend on
- **Multiple filtering options** - view all, dusty (30+ days), unused (7+ days), or safe-to-remove
- **No automatic removal** - Dust only identifies, you decide what to remove

## Requirements

- Linux system with `pacman` (Arch, Manjaro, Garuda, etc.)
- Python 3.6+ (usually pre-installed)
- Web browser for the interface

## Data Storage

- **Database**: `~/.dust_tracker.db` (SQLite)
- **Settings**: Browser localStorage (auto-scan preferences)
- **No external dependencies** - uses only Python standard library

## Development

This project was created collaboratively between a human user and Claude (Anthropic's AI assistant). The user provided the concept and requirements, while Claude handled the implementation details and technical design.

The core idea - tracking package usage with a "dust accumulation" metaphor - came from the user's frustration with existing Linux system cleaners that only handle orphaned dependencies rather than identifying truly unused applications.

## Contributing

Since this is a single-file application, contributions are straightforward:

1. Fork the repository
2. Make your changes to `dust_tracker.py`
3. Test thoroughly (it handles system-level operations)
4. Submit a pull request

Areas for potential improvement:
- Support for other package managers (apt, dnf, etc.)
- Additional usage detection methods
- Export/import functionality for usage data
- Integration with package removal tools

## License

MIT License - use it however you'd like.

## Limitations

- **Arch-based only** - currently requires `pacman` (could be extended)
- **Process-based tracking** - might miss very short-lived applications
- **Local only** - no cloud sync or multi-machine tracking
- **Manual removal** - identifies candidates but doesn't remove packages automatically

## Troubleshooting

**Port already in use?**
Dust automatically finds alternative ports or detects existing instances.

**Permission errors?**
Make sure the script is executable: `chmod +x dust_tracker.py`

**Service won't start?**
Check the logs: `journalctl --user -u dust_tracker.service -f`

**No packages showing up?**
Run a manual scan first: click "Scan System" in the web interface.

---

*Built with the philosophy that your system should get leaner over time, not bloated.*
