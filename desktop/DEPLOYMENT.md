# TenderRadar — Deployment Guide

`TenderRadar_Setup.exe` installs TenderRadar as a normal Windows desktop
application. The user double-clicks an icon; the Django server starts hidden,
the browser opens, and the app is ready in about 5 seconds.

---

## 1. How to build the installer

Three steps, from the project root:

```bash
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe desktop\build_runtime.py
.venv\Scripts\python.exe desktop\build_installer.py
```

| Step | Produces |
|---|---|
| `build_runtime.py` | `desktop/payload/runtime/` — CPython 3.14.6 embeddable + all 131 dependencies (195 MB). Downloads the official embeddable zip once and caches it in `desktop/.cache/`. |
| `build_installer.py` | stages the app, builds `TenderRadar.exe` and `Uninstall.exe`, then packs everything into **`TenderRadar_Setup.exe`** (270 MB: app 29 MB + runtime 195 MB + MySQL MSI 124 MB + VC++ redistributable 24 MB + initial dataset 53 MB) in the project root. |

`build_runtime.py` only needs re-running when Python or `requirements.txt` changes.

### Building with Inno Setup instead

`desktop/TenderRadar.iss` is a complete Inno Setup script that produces the same
package. It is **not** what shipped: this network's proxy replaces the
jrsoftware.org download with an HTML block page, so Inno Setup could not be
installed here. On a machine that can reach jrsoftware.org:

```bash
.venv\Scripts\python.exe desktop\build_runtime.py
.venv\Scripts\python.exe desktop\build_exe.py
ISCC.exe desktop\TenderRadar.iss
```

---

## 1a. Windows Service, tray and development mode

Since 2026-08-11 the deployment includes a Windows Service that starts
TenderRadar automatically, a system-tray application, and a separate development
mode with automatic code reload. The installer registers the service when it is
run as administrator and configures the tray to start with Windows.

Full detail — architecture, commands, logs, troubleshooting, and which tests are
still blocked on administrator rights — is in **WINDOWS_SERVICE.md** and
**WINDOWS_SERVICE_FINAL_REPORT.md** in the project root.

Note for the packager: `desktop/build_runtime.py` must be re-run so the shipped
runtime contains pywin32, pystray and Pillow; until then the service and tray
work from a checkout but not from a fresh installation.

---

## 2. How to install TenderRadar

1. Copy `TenderRadar_Setup.exe` to the target machine.
2. Double-click it.
3. Follow the wizard: **Welcome → Required components → Install location → Installing → Finish**.
4. Tick *Launch TenderRadar* on the last page, or use the new shortcut.

Default location: `%LOCALAPPDATA%\Programs\TenderRadar` (~298 MB installed, including the 53 MB initial dataset staged in `config\` for first-run use).

**No administrator rights are required** for a per-user install, and the
application folder stays writable — which the Scraper Factory needs.

Silent install for deployment tooling:

```bash
TenderRadar_Setup.exe /SILENT /DIR="C:\Apps\TenderRadar"
```

---

## 3. MySQL handling

MySQL stays a **Windows service**, independent of `TenderRadar.exe`, deliberately:

- the data directory must persist independently of the application folder
- the service starts at boot, before any user logs in
- Backup & Restore uses `mysqldump`/`mysql` from the MySQL installation
- a single-file EXE would extract a DB server to temp and lose data on exit

### CASE A — MySQL already installed (reuse)

Setup detects the service, the client tools, the version and whether the
`tenderradar` database exists. It then **reuses** the installation:

- no reinstall
- `CREATE DATABASE IF NOT EXISTS` only — an existing database is never recreated
- if the database already has tables, the initial dump is **refused**
- migrations are checked, never forced

### CASE B — MySQL not installed (automatic install)

The installer bundles Oracle's signed `mysql-8.0.46-winx64.msi` (124 MB), the
Microsoft Visual C++ redistributable it depends on (24 MB), and:

0. installs the **Visual C++ 2015-2022 x64 runtime** if it is missing. This is not
   optional: the MySQL MSI carries a hard launch condition —

   ```
   Installed OR (VS14REDISTX64_64_MAJOR = 14 AND VS14REDISTX64_64_MINOR >= 20)
   -> "This application requires Visual Studio 2019 x64 Redistributable."
   ```

   Windows does not ship that runtime, so without it the MySQL install aborts on
   a clean PC. `vcredist_ok()` reads the same registry values the MSI checks, so
   its verdict matches the MSI's, and the step is skipped entirely on the many
   machines that already have it.
1. installs the server files (`msiexec /i /qn`)
2. writes `my.ini` with `utf8mb4` + `STRICT_TRANS_TABLES` + InnoDB
3. initialises the data directory (`mysqld --initialize-insecure`)
4. registers **`MySQL80`** as an auto-start Windows service
5. starts the service and waits for readiness
6. sets a generated root password
7. creates the `tenderradar` database
8. imports the verified initial dataset **only because the database is empty**
9. loads the MySQL timezone tables (otherwise all analytics date grouping breaks)
10. creates `tenderradar_app` with a 32-character generated password and
    least-privilege grants on that one schema
11. writes `production.env` and verifies the application can connect

Step 1 and 4 need administrator rights. If Setup is not elevated it stops with a
clear message asking you to run it as administrator — it never half-installs.

**MySQL is never uninstalled or stopped by TenderRadar**, because other
applications may share it.

### First run finishes whatever Setup could not

Setup provisions the database when it can. It cannot always — the user may
decline the UAC prompt, or install while the database server is unreachable.
Rather than leaving a permanently broken application, `desktop/first_run.py`
re-checks on **every** launch and finishes the job the first time it can.

`classify()` returns one of five states, and the launcher acts on each:

| State | Meaning | Launcher behaviour |
|---|---|---|
| `ready` | configured, `tenderradar_app` connects | starts Django immediately (the normal case; adds ~0.2 s) |
| `needs_provision` | MySQL is up, database/user/config are not | asks once for the MySQL root password, provisions, continues |
| `needs_server` | no MySQL server at all | offers to install the bundled MySQL through UAC |
| `unreachable` | configured but the server is not answering | explains that MySQL is down and how to start it |
| `sqlite` | deliberately not using MySQL | starts Django immediately |

When Setup could not configure the database, it leaves `mysql-server.msi` and the
initial dataset in `<install>\config\` so the launcher can complete the work
later. The elevated half runs as `TenderRadar.exe --provision-mysql` through the
standard Windows consent dialog and reports progress via
`config\first_run.log`; the unelevated parent waits for `PROVISION_OK` or
`PROVISION_FAILED`.

`first_run.py` is loaded **from disk**, not frozen into the launcher, so an
application update can ship new provisioning logic without rebuilding the EXE.

### Database protection — what can never happen

Two independent guards, both tested:

**1. `assert_safe()` — a runtime block on every statement.**
`desktop/mysql_setup.sql()` calls it before sending anything to MySQL, so this is
not a code-review promise that a later edit could quietly break. It raises
`UnsafeStatement` on `DROP DATABASE`, `DROP SCHEMA`, `DROP TABLE`, `TRUNCATE`,
`FLUSH TABLES` and `DELETE FROM <application table>`.

Two things are deliberately allowed, because neither destroys application data:

- `FLUSH PRIVILEGES` — re-reads MySQL's grant tables after creating the app user.
  This is **not** Django's `manage.py flush`, which would empty the application
  tables and is never invoked anywhere in the desktop code.
- `DELETE FROM mysql.time_zone*` — MySQL's own system schema. This is how a
  single timezone is idempotently reloaded; the regex requires the `mysql.`
  prefix, so it cannot reach `tenderradar`.

**2. `import_initial_dump()` re-counts tables immediately before writing** and
aborts if the count is not zero — independently of what the caller believed.

Note that `tenderradar_app` *is* granted `DROP`, `ALTER` and `INDEX` on
`tenderradar.*`: Django migrations need them. The grant is scoped to that one
schema, with no `SUPER` and no global privileges.

### Requirements on the target machine



| Requirement | Detail |
|---|---|
| MySQL **8.0** | Installed automatically if absent; otherwise reused |
| Character set | `utf8mb4` / `utf8mb4_0900_ai_ci` (MySQL 8 default) — required for Arabic and emoji |
| `sql_mode` | must include `STRICT_TRANS_TABLES` |
| Timezone tables | `mysql.time_zone_name` must be populated, or `CONVERT_TZ` returns NULL and all analytics date-grouping silently breaks |
| Database | `tenderradar`, already migrated |
| Account | `tenderradar_app@127.0.0.1` / `@localhost`, generated password, rights on `tenderradar.*` only |
| Client tools | `mysqldump.exe` / `mysql.exe` in `C:\Program Files\MySQL\MySQL Server 8.0\bin` |

The installer configures all of these on a fresh install and merely **verifies**
them on an existing one. It never flushes or overwrites an existing database.

> Moving to a new machine: back up the source database with
> `mysqldump`, create `tenderradar` on the target, and restore. The installer
> will not do this for you, by design.

---

## 4. Where configuration lives

`<install folder>\production.env`

```bash
DB_ENGINE=django.db.backends.mysql
DB_NAME=tenderradar
DB_USER=tenderradar
DB_PASSWORD=...
DB_HOST=127.0.0.1
DB_PORT=3306
```

- The installer **never overwrites an existing `production.env`** — upgrades keep
  the local configuration.
- `settings.py` reads only `DB_*` keys from this file, on purpose: it also holds
  `DJANGO_DEBUG` and `DJANGO_SECRET_KEY`, and importing those would change how a
  running server behaves.
- Nothing is hardcoded in the application. Comment out `DB_ENGINE` to fall back
  to SQLite.

Optional environment variables: `TENDERRADAR_PORT` (8005 — the *first* port
tried, see § 6a), `TENDERRADAR_THREADS`
(16), `TENDERRADAR_HOME`, `TENDERRADAR_MYSQL_SERVICE` (MySQL80), `MYSQL_BIN_DIR`.

### Credentials and the installer

`TenderRadar_Setup.exe` ships **`production.env.template` only** — it contains no
secrets. The real `production.env` is generated on the target machine, with:

- a freshly generated 32-character password for `tenderradar_app`
- a freshly generated `DJANGO_SECRET_KEY` (per installation)

Four layers keep secrets out of the package:

1. **Pattern rule** — `is_secret_file()` in `desktop/build_installer.py` drops
   anything matching `production.env*` (except `.template`), `.env*`, `*.pem`,
   `*.key`, `*secret*`, `*credential*`, `*password*`, `my.cnf`.
2. **Content scan** — `assert_no_secrets()` reads every staged byte and looks for
   the actual secret values from the build machine's `production.env`. If any is
   found, **the build aborts**. A filename rule only blocks what it was told
   about; this catches files nobody thought of. It was added after
   `production.env.pre-cutover` — which held both the MySQL password and the
   SECRET_KEY — shipped past the filename rule.
3. **Dataset sanitizing** — `desktop/sanitize_dump.py`, run on every build by
   `assert_dump_sanitized()`. Layers 1 and 2 only ever looked at `payload/app`,
   so `payload/mysql/initial_tenderradar.sql` was never examined — and it
   shipped with the live Microsoft Graph client secret, the mailbox password, a
   Graph access token and 162 live session rows in it. The dump is a mysqldump
   of a working system, so it picks those up again every time it is regenerated;
   the sanitizer therefore runs on every build, not once.

   It reads the dump's own `CREATE TABLE` blocks for column order, type and
   nullability, blanks every column whose name matches a credential pattern with
   a value that column can hold, drops `django_session` outright, then re-reads
   the result and **aborts the build** if anything survived. Currently cleared:

   | Table | Columns |
   |---|---|
   | `tenders_integrationsettings` | `graph_client_secret`, `graph_mailbox_password`, `graph_access_token`, `graph_refresh_token`, `ldap_bind_password`, `wa_api_key` |
   | `tenders_sitesettings` | `openai_api_key` |
   | `django_session` | every row |

   `auth_user.password` is deliberately **kept** — those are salted PBKDF2
   hashes, and the dump is the only source of the user accounts, so blanking
   them would lock everyone out of a fresh installation with no way back in.
   Non-secret identifiers (`graph_tenant_id`, `graph_client_id`, LDAP host and
   search base) are also kept, so the recipient only has to re-enter the secret
   in Settings → Integrations rather than reconstruct the whole configuration.

   To sanitize a newly regenerated dump by hand:

   ```bash
   .venv\Scripts\python.exe desktop\sanitize_dump.py
   ```

4. **Cleanup on upgrade** — Setup deletes `production.env.pre-cutover` and
   `production.env.bak` from any install an earlier build reached. These are the
   only files Setup ever deletes; they are credential files, not user data.

Verified: 7,987 files in a fresh install scanned, zero build-machine secrets.

Secrets are never passed on a command line. `mysql`/`mysqldump` receive them via
a temporary `--defaults-extra-file` written with mode 600 and deleted in a
`finally` block, so they never appear in `argv`, Task Manager, or any log.

If the elevated path generates a MySQL **root** password, it is written to
`config\mysql_root_password.txt` with an ACL restricted to the installing user,
and the file tells the user to move it to a password manager and delete it.
TenderRadar itself never needs it again — the application runs as
`tenderradar_app`.

---

## 4a. The application icon

`desktop/TenderRadar.ico` is generated from the TenderRadar radar mark — the same
artwork as `static/img/logo-icon.svg`, which the web app already uses for its
favicon and its header logo.

```bash
.venv\Scripts\python.exe desktop\make_icon.py
```

The build calls this automatically whenever `make_icon.py` is newer than the icon,
so a release cannot ship a stale one. **Pillow is required only to build the icon**
— it is not a runtime dependency and is not bundled into the shipped runtime.

The icon carries ten purpose-drawn sizes: **16, 20, 24, 32, 40, 48, 64, 96, 128,
256**. It previously held a single 16×16 image, which Windows then upscaled for the
desktop shortcut (48px), the taskbar (24–32px), Alt-Tab and the Start Menu — so the
application looked blurry everywhere except the smallest context.

Sizes up to 48 are stored as DIBs and 64 upward as PNG, which is the layout Windows
itself uses. All ten are embedded in `TenderRadar.exe`, `TenderRadar_Setup.exe` and
`Uninstall.exe`, and the same file is used for the desktop and Start Menu shortcuts,
the Programs and Features entry, and the launcher's own window.

Small sizes are simplified, which is ordinary icon practice rather than a second
logo: three hairline rings turn to mush at 16px, so ≤16px draws one ring, ≤32px
draws two, and 40px upward uses the exact SVG geometry. The sweep beam is scaled up
slightly at small sizes because the yellow is what identifies TenderRadar at a
glance.

`static/favicon.ico` is regenerated from the same artwork, so the browser tab, the
desktop shortcut and the taskbar all match.

---

## 4b. Error handling

`desktop/errors.py` is the single place where failures become messages. The user
never sees a traceback, an exception class, an exit code or console output.

### What the user sees

The startup window is a checklist, ticked off as each stage succeeds:

```
Starting TenderRadar…
  ✓ Checking installation
  ✓ Checking MySQL
  ✓ Checking database
  ✓ Starting web service
  ✓ Waiting for TenderRadar
  ✓ Opening browser
```

If a stage fails, the list stops there with a red ✗ against it and a dialog
explains, in this order: **what happened → why → what this means for your data →
what to do next** (numbered). Buttons: **Retry · Open Help · View Technical
Details · Copy Error Details · Exit**. Retry re-runs the whole sequence, which is
safe because every stage is idempotent.

### The data-safety rule

Every failure states what happened to the user's data. `DataSafety` has no
"probably fine" value — a reassurance is only ever printed when it has been
established:

| Value | Statement |
|---|---|
| `NOT_ATTEMPTED` | no database work had started, so nothing changed |
| `VERIFIED_INTACT` | the database was *queried just now*; the table and tender counts are quoted back |
| `PROTECTED_BY_GUARD` | the operation stopped because data was present; nothing was deleted or overwritten |
| `NO_DATABASE_YET` | there is no database to lose |
| `UNKNOWN` | TenderRadar could not check — do not reinstall, contact support |

`UNKNOWN` exists so the code never has to lie. `verify_database_intact()`
returns it rather than a reassurance when its own check cannot run.

### Problems TenderRadar recognises

`mysql_not_installed` · `mysql_stopped_start_failed` · `mysql_service_failed` ·
`mysql_unreachable` · `mysql_bad_credentials` · `database_missing` ·
`database_not_empty` · `port_in_use` · `django_start_failed` ·
`django_not_ready` · `missing_file` · `runtime_missing` · `needs_admin` ·
`not_enough_disk` · `bad_dump` · `install_step_failed` · `interrupted_install` ·
`unexpected`

Requirement 10 is met by five *distinct* MySQL messages, because the fix differs:
not installed, stopped, failed to start, database missing, connection refused,
and wrong credentials each lead somewhere different.

Installation failures name the stage — "Step that failed: MySQL installation" —
and never abandon the user: the application files are already in place, so the
launcher offers to finish the database setup on first run.

`unexpected()` is the catch-all. Even a failure nobody anticipated produces a
friendly dialog with the traceback filed behind the technical-details button.

### Fast failure instead of a silent wait

`start_service()` returns as soon as Windows *refuses* a start (access denied,
service disabled) instead of polling out its timeout. Polling only continues
while a start is genuinely under way. A silent 90-second wait on a request that
was rejected in 200 ms is indistinguishable from a hang.

### Automatic recovery

Attempted before any dialog is shown: start a stopped MySQL service, reuse an
already-running TenderRadar instead of reporting a port conflict, move to the
next free port when another program holds 8005, complete a
database setup Setup could not finish, finish an interrupted installation, and
retry any stage on request.

---

## 5. Where logs live

| Log | Path |
|---|---|
| **Problems and diagnostics** | `<install>\logs\tenderradar.log` |
| Launcher / startup detail | `<install>\logs\desktop_launcher.log` |
| Django application | `<install>\logs\*.log` |
| Scraper runs | IT Dashboard → System Logs → Scraper Runs |
| Admin actions | IT Dashboard → System Logs → Admin Actions |

`tenderradar.log` is what to send to support. Each problem records the timestamp,
application version, Windows version and build, architecture, install folder,
whether the bundled runtime and `production.env` are present, MySQL service state,
whether port 3306 answers, whether port 8005 is free (and which program holds it),
free disk space, the failing step, the data-safety verdict, **the advice the user
was given**, and the stack trace.

**No credential ever reaches it.** `redact()` runs over every line and replaces
`password=`, `DB_PASSWORD`, `DJANGO_SECRET_KEY`, `SECRET_KEY`, `API_KEY`, `TOKEN`,
`--password=` and `-p<secret>` with `***REDACTED***`. Verified by test.

`desktop_launcher.log` holds the verbose trace — each check, the server PID and
the server's own output.

---

## 6. Starting and stopping

**Start:** double-click the Desktop or Start Menu shortcut. The status window
shows each step; the browser opens at `http://127.0.0.1:8005` — or the next free
port if another program holds that one (§ 6a).

**Stop:** click **Stop and Exit** (or close the window and confirm). The Django
server is shut down with it.

A Windows **job object** guarantees the server can never outlive the launcher —
even if the launcher is killed from Task Manager or crashes, Windows tears the
server down and the port is released. Verified by test.

Only one instance runs at a time: launching again when it is already running just
reopens the browser.

---

## 6a. Port selection

Port 8005 is not reserved for TenderRadar, and on a developer's machine or a
shared server something else may already hold it.

The launcher used to treat *anything* answering HTTP 200 on 8005 as "TenderRadar
is already running" and point the browser at it. When another program owned the
port, double-clicking TenderRadar opened **that program** instead — with no error,
because from the launcher's point of view everything had worked.

A listening port is now **identified**, not assumed. `serve_desktop.py` starts
Waitress with `ident='TenderRadar'`, so our own server always answers with a
`Server: TenderRadar` header, and no other program on the machine does:

| Probe result | Meaning | Launcher behaviour |
|---|---|---|
| `Server: TenderRadar` | our own instance, even if it is booting or returning an error page | reuse it and reopen the browser |
| any other HTTP response | somebody else's web application | skip this port |
| listening but not readable HTTP | somebody else's service | skip this port |
| nothing listening | free | start here |

Ports **8005–8024** are probed in order. A running instance found anywhere in the
range wins over an unused port, so a free 8006 never shadows a live TenderRadar on
8007 and a second server is never started. The status window states the port when
it is not the default:

```
Port 8005 is used by node.exe (process 12345) — TenderRadar will use port 8006 instead.
```

`port_in_use` is now raised only when all twenty candidates are held by other
programs. `TENDERRADAR_PORT` changes where the search starts, not the count.

### Why the check binds rather than connects

"Is this port free?" is answered with `bind()`, not `connect()`, and the reason is
measured, not theoretical. The first version connected with a 1-second timeout and
assumed a closed port would be refused instantly. On this network it is not —
a closed loopback port silently drops the SYN, so every free port cost the **full
timeout**:

```
port 8010:  1001.7 ms  rc=10035
port 8011:  1000.0 ms  rc=10035
...
-> 10 closed ports cost 10041 ms
```

Scanning the range therefore added **20 seconds** to every start on a machine
where port 8005 was free. Worse, it was invisible: the stall happened after the
database step finished but before the web step set its own caption, so the window
sat on *"Checking database"* and looked like a database problem.

`bind()` answers instantly in both directions — success means free, `EADDRINUSE`
means taken — and needs no timeout at all. `choose_port()` also short-circuits:
if the default port binds, it returns immediately without probing anything else.
Measured after the change:

| Case | Before | After |
|---|---|---|
| nothing running (the normal start) | ~20 s | **1.2 ms** |
| foreign program on 8005 | ~20 s | 18 ms |
| our own server on 8005 (reuse) | 23 ms | 16 ms |
| foreign on 8005, ours on 8008 | ~4 s | 27 ms |

Only a port that is actually holding a socket costs an HTTP request, and that is
what identifies whose it is.

---

## 6b. Upgrading

Run `TenderRadar-Setup.exe` again over an existing installation. Setup detects it
from the registry and:

- stops the running instance before replacing files
- **preserves** `production.env`, `media/`, `backups/`, `logs/` and every
  Scraper-Factory-generated module in `tenders/scrapers/`
- replaces application files and the runtime
- leaves the MySQL database completely alone
- re-verifies migrations (`makemigrations --check`)

Verified by test: markers placed in `media/`, `backups/` and a generated scraper
module all survived, `production.env` md5 was unchanged, and the database still
held 89 tables / 66,144 tenders / 38 users afterwards.

## 7. Uninstalling

Any of:

- **Settings → Apps → TenderRadar → Uninstall**
- Start Menu → TenderRadar → *Uninstall TenderRadar*
- `<install>\Uninstall.exe`

The uninstaller stops the app, removes the shortcuts, removes the
Programs-and-Features entry, and deletes the application folder. It offers to
**keep** `production.env`, `media`, `backups` and `logs`.

**It never touches MySQL** — the service and the `tenderradar` database are left
exactly as they are. Database removal is not offered automatically; drop it by
hand only after taking a backup, and remember other applications may share the
same MySQL service. Silent removal: `Uninstall.exe /SILENT` (add `/PURGE` to
delete data too).

---

## 8. How Backup & Restore works

IT Dashboard → **Backup & Restore** (Super Admin or IT).

A backup is one `.zip` under `<install>\backups\`:

```
manifest.json      what the archive holds, who made it, when
database/dump.sql   mysqldump --single-transaction (consistent, non-blocking)
media/              uploaded files
config/             production.env, requirements.txt, web.config
```

- Credentials are passed to `mysqldump` through a temporary defaults file, never
  on the command line where the process list would expose them.
- **Restore is Super Admin only.** It takes a safety copy of the current state
  first, so a restore is itself reversible.
- Restoring a SQLite archive onto MySQL (or the reverse) is refused rather than
  silently corrupting the database.
- Automatic backups: IT Dashboard → Backup & Restore → *Automatic Backups*
  (needs Celery Beat: `python manage.py setup_schedule`).

`mysqldump`/`mysql` are located via `MYSQL_BIN_DIR`, then `PATH`, then the usual
install folders. If they are missing, backup fails with a clear message rather
than falling back to something destructive.

---

## 9. If MySQL is unavailable

TenderRadar will not start, and says why. The launcher shows one line per check:

```
[OK]   Application folder: C:\...\TenderRadar
[OK]   Python runtime: bundled runtime
[OK]   Configuration: production.env
[FAIL] MySQL service MySQL80: stopped
[FAIL] MySQL reachable at 127.0.0.1:3306 — CONNECTION REFUSED
```

followed by a dialog naming the log file. Nothing is started, no browser opens,
and no half-initialised state is left behind.

Fixes:

| Message | Fix |
|---|---|
| `service MySQL80: stopped` | `net start MySQL80` (administrator) |
| `service MySQL80: not installed` | Install MySQL 8.0, or point `DB_HOST` at your server |
| `CONNECTION REFUSED` | Check `DB_HOST`/`DB_PORT`, firewall, and that MySQL is listening |
| `mysqldump / mysql: not found` | Install the MySQL client tools or set `MYSQL_BIN_DIR` |

---

## 9b. Long installation paths

Setup uses Win32 extended-length paths (`\?\`) for every file it copies, so an
install folder that pushes the deepest project file past the 260-character
`MAX_PATH` limit works. Without this the copy failed with `WinError 3` partway
through — found by installing to a 158-character path.

---

## 10. Troubleshooting startup failures

**Read `<install>\logs\desktop_launcher.log` first.**

| Symptom | Cause / fix |
|---|---|
| Nothing happens on double-click | Check the log. If empty, the folder may not be writable. |
| "Python runtime: MISSING" | `runtime\python.exe` absent — reinstall |
| "Port 8005 is used by … — will use port 8006" | Normal, not an error. See § 6a |
| "ports 8005 to 8024 are all in use" | Twenty other servers are running; close some or set `TENDERRADAR_PORT` |
| Window says "Already running" | An instance is live; it just reopens the browser |
| "Django did not become ready" | Log shows the server's own output — usually a database or `production.env` problem |
| Server exits immediately | Log contains `TENDERRADAR_FATAL` and the traceback |
| Analytics charts empty | MySQL timezone tables not loaded (see §3) |
| Login page loads but has no styling | `staticfiles` unwritable; WhiteNoise serves via finders, so check folder permissions |
| Scraper Factory cannot create a scraper | Installation folder is read-only — do not install under `C:\Program Files` without granting write access |

**Do not** install into `C:\Program Files` unless you grant the user write access
to the application folder: the Scraper Factory writes `.py` modules into
`tenders\scrapers\` at runtime and would fail. The default
`%LOCALAPPDATA%\Programs` location avoids this entirely.

---

## Architecture summary

```
TenderRadar.exe                (11.7 MB launcher, compiled)
   ├── preflight: MySQL service, port, config, runtime
   ├── runtime\python.exe desktop\serve_desktop.py   (hidden, in a job object)
   │        └── Waitress → Django (DEBUG=False) → MySQL 8.0
   ├── polls /login/ until HTTP 200
   ├── opens the default browser
   └── on exit: kills the server tree
```

The Django application is **not** frozen. `scraper_factory/installation/installer.py`
writes new `.py` modules into `tenders/scrapers/` and rewrites that package's
`__init__.py` registry — a read-only PyInstaller bundle can never import those,
so freezing would permanently break the Scraper Factory. Only the launcher,
installer and uninstaller are compiled.
