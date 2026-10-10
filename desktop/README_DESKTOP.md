# TenderRadar Desktop Launcher

`TenderRadar.exe` turns TenderRadar into a double-click desktop application: it
starts the Django production server hidden, waits until it is genuinely ready,
opens the browser, and shuts the server down again when it closes.

---

## Running as a Windows Service

`TenderRadar.exe` is the manual launcher. There is now also a Windows Service
that starts TenderRadar with Windows, plus a system-tray icon to control it, and
a development mode with automatic reload:

    desktop/service.py     the Windows Service (pywin32)
    desktop/tray.py        the system-tray application (pystray)
    desktop/devserver.py   development mode — automatic reload

All three run the same production Waitress worker, `serve_desktop.py`. See
**WINDOWS_SERVICE.md** in the project root for how to install, control and
develop against them.

---

## For the end user

1. Double-click **`TenderRadar.exe`** (in the TenderRadar folder).
2. A small status window appears and reports each startup step.
3. The browser opens at <http://localhost:8005> after ~5–10 seconds. If another
   program already holds 8005, TenderRadar says so and uses the next free port —
   the status window shows the address it actually opened.
4. Use TenderRadar normally.
5. To finish, click **Stop and Exit** (or close the window and confirm).

No terminal window ever appears. Leave the status window open while using the app —
closing it stops the server.

---

## Architecture, and why it is built this way

```
TenderRadar.exe   (PyInstaller, --windowed, 11.7 MB — launcher only)
      │
      ├── preflight: app folder, .venv, production.env, MySQL service + port
      ├── picks a port: probes 8005..8024, identifying each listener
      ├── spawns  .venv\Scripts\python.exe desktop\serve_desktop.py
      │           (CREATE_NO_WINDOW → no console; inside a Windows job object)
      ├── polls  http://127.0.0.1:<port>/login/  until HTTP 200
      ├── opens the default browser
      └── on exit: terminates the child process tree
                   (job object also kills it if the launcher is force-killed)
```

### Port selection

Port 8005 is not reserved for TenderRadar. The launcher used to treat anything
answering on 8005 as "TenderRadar is already running" and send the browser
there — so if another program owned the port, double-clicking TenderRadar opened
*that program*.

A listening port is now identified rather than assumed. `serve_desktop.py` starts
Waitress with `ident='TenderRadar'`, so our own server always replies with a
`Server: TenderRadar` header and nothing else on the machine does:

| Probe result | Meaning | Launcher does |
|---|---|---|
| `Server: TenderRadar` | our own instance | reuses it, reopens the browser |
| any other response, or not HTTP | somebody else's program | skips that port |
| nothing listening | free | starts here |

"Free" is decided by `bind()`, not by connecting. A closed loopback port here does
not refuse the connection, it drops it — so a connect-based check burned its full
timeout on every free port and added ~20 s to startup. `bind()` answers instantly
either way, and the default port is tried first so the normal start costs one
check (~1 ms). See DEPLOYMENT.md § 6a for the measurements.

Ports 8005–8024 are tried in order, and a running instance anywhere in that range
wins over an unused port — so launching twice never starts a second server. The
"all ports in use" error appears only if all twenty are held by other programs.

**The Django application is *not* frozen into the EXE.** That is a deliberate
decision, not a shortcut:

| Reason | Detail |
|---|---|
| **Scraper Factory writes Python at runtime** | `scraper_factory/installation/installer.py` writes new `.py` modules into `tenders/scrapers/` and rewrites that package's `__init__.py` registry. A PyInstaller bundle is read-only and its import system cannot load modules written to disk afterwards — freezing would permanently break an entire Django app (28 models, 8 migrations). |
| **Backup & Restore shells out** | `tenders/backup.py` invokes `mysqldump` / `mysql`. External tools are required regardless of packaging. |
| **Migrations need real files** | `manage.py migrate` reads the 81 migration files on disk. |
| **Data must stay writable** | `media/`, `backups/`, `logs/`, `db.sqlite3`. |
| **Bundle fragility** | 78 scraper modules plus lxml, cryptography, playwright — large and prone to hidden-import breakage. |

A thin launcher keeps every one of those working while still giving the user a
single EXE to double-click.

### MySQL stays a separate Windows service

**Recommended: leave MySQL as the `MySQL80` Windows service.** It should *not* be
bundled into the EXE:

- The data directory must persist independently of the application folder.
- The service already starts automatically at boot, before any user logs in.
- `mysqldump`/`mysql` from the MySQL installation are what Backup & Restore uses.
- Multiple clients (the app, backups, admin tools) share one database.
- Embedding a database server in a single-file EXE would extract it to a temp
  folder on every launch — data would be lost on exit.

The launcher *checks* MySQL and reports clearly if it is not running; it does not
try to own it.

---

## Files

| File | Purpose |
|---|---|
| `desktop/launcher.py` | Launcher source (the EXE) |
| `desktop/serve_desktop.py` | Waitress entry point — Django with `DEBUG=False` |
| `desktop/build_exe.py` | Build script |
| `desktop/TenderRadar.ico` | Icon |
| `TenderRadar.exe` | Built launcher, in the project root |
| `logs/desktop_launcher.log` | Startup log — first place to look on failure |

Nothing in the existing application was modified. `settings.py` still says
`DEBUG = True` for development; the packaged server sets `DEBUG = False` at
runtime in `serve_desktop.py` only.

---

## Rebuilding

```bash
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe desktop\build_exe.py
```

Produces `TenderRadar.exe` in the project root.

---

## Configuration

Environment variables, all optional:

| Variable | Default | Meaning |
|---|---|---|
| `TENDERRADAR_PORT` | `8005` | *First* port tried; 19 more above it are used as fallbacks |
| `TENDERRADAR_HOME` | folder of the EXE | Application folder override |
| `TENDERRADAR_THREADS` | `16` | Waitress worker threads |
| `TENDERRADAR_MYSQL_SERVICE` | `MySQL80` | Service name to check |

Database settings come from `production.env` (`DB_ENGINE`, `DB_NAME`, `DB_USER`,
`DB_PASSWORD`, `DB_HOST`, `DB_PORT`) exactly as before.

---

## Giving it to another Windows user

Ship the **whole TenderRadar folder**. On the target machine:

1. **MySQL 8.0** installed, with the `tenderradar` database and its user.
2. **Python 3.14 virtual environment** present at `.venv\`:
   ```bash
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
3. **`production.env`** with the correct `DB_*` values for that machine.
4. Apply migrations once: `.venv\Scripts\python.exe manage.py migrate`
5. Double-click `TenderRadar.exe`.

The launcher validates 1–3 and shows a specific error for whatever is missing,
rather than failing silently.

> **Not a zero-dependency installer.** It removes the terminal, not the need for
> MySQL and the Python environment. A true single-file installer would mean
> bundling Python + MySQL via Inno Setup or MSIX — a separate piece of work, and
> it still could not freeze the Django app for the reasons above.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "MySQL service: stopped" | `net start MySQL80` (needs admin) |
| "MySQL reachable … CONNECTION REFUSED" | Wrong `DB_HOST`/`DB_PORT`, or MySQL down |
| "Python environment: MISSING" | `.venv` not created — see step 2 above |
| "Port 8005 is used by … — will use port 8006" | Normal. Another program holds 8005; TenderRadar moved up and the browser opens on the new port |
| "ports 8005 to 8024 are all in use" | Twenty other servers are running. Close some, or set `TENDERRADAR_PORT` to a free port |
| Window says "Already running" | An instance is live; it just reopens the browser |
| Nothing happens on double-click | Check `logs\desktop_launcher.log` |
