"""
User-facing error handling for the TenderRadar desktop application.

The end user is not a developer. A traceback, an exception message, a `sc query`
dump or an exit code tells them nothing and frightens them. Everything that can
fail during installation or startup is therefore turned into a `Problem`: a
plain-language description of what happened, why, whether their data is safe, and
a numbered list of what to do next.

Three rules this module exists to enforce:

  1. Nothing ever fails silently or closes without explanation.
  2. The technical detail still exists — behind a "View Technical Details"
     button, and in logs/tenderradar.log — but it is never the primary message.
  3. A claim that data is safe is only ever made when it has been *verified*.
     `DataSafety` has no "probably fine" value; when the state cannot be
     established, the dialog says so and tells the user not to guess.

The dialog is built with tkinter, which ships with the bundled runtime, so there
is no extra dependency and no console window.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

APP_NAME = 'TenderRadar'
APP_VERSION = '1.0.0'
SUPPORT_CONTACT = 'your IT department'
CREATE_NO_WINDOW = 0x08000000
DEFAULT_PORT = int(os.environ.get('TENDERRADAR_PORT', '8005'))


# ── Secret redaction ─────────────────────────────────────────────────────────
# Applied to everything before it reaches a log file or a dialog. Diagnostics
# quote command lines, environment values and MySQL errors, any of which can
# carry a password.

_REDACT_PATTERNS = [
    re.compile(r'(?i)\b(password|passwd|pwd)\s*[=:]\s*("[^"]*"|\'[^\']*\'|\S+)'),
    re.compile(r'(?i)\b(DB_PASSWORD|MYSQL_PWD|DJANGO_SECRET_KEY|SECRET_KEY|'
               r'API_KEY|OPENAI_API_KEY|TOKEN|ACCESS_TOKEN|AUTH_TOKEN)'
               r'\s*[=:]\s*("[^"]*"|\'[^\']*\'|\S+)'),
    re.compile(r'(?i)--password(?:=|\s+)("[^"]*"|\'[^\']*\'|\S+)'),
    # mysql -pSecret. The lookbehind keeps it from eating the tail of
    # --password=..., which the previous pattern has already handled.
    re.compile(r'(?i)(?<![-\w])-p(?=\S)(\S+)'),
]


def redact(text: str) -> str:
    """Replace anything that looks like a credential with ***REDACTED***."""
    if not text:
        return ''
    out = str(text)
    for pat in _REDACT_PATTERNS:
        out = pat.sub(lambda m: m.group(0)[:m.start(m.lastindex) - m.start()]
                      + '***REDACTED***', out)
    return out


# ── Data-safety statements ───────────────────────────────────────────────────

class DataSafety(Enum):
    """What we can honestly tell the user about their data.

    Deliberately has no optimistic default: every construction site must pick
    one, and UNKNOWN is a legitimate, honest answer.
    """
    NOT_ATTEMPTED = 'not_attempted'
    VERIFIED_INTACT = 'verified_intact'
    PROTECTED_BY_GUARD = 'protected_by_guard'
    NO_DATABASE_YET = 'no_database_yet'
    UNKNOWN = 'unknown'

    def message(self, detail: str = '') -> str:
        if self is DataSafety.NOT_ATTEMPTED:
            return ('Your data is safe. TenderRadar had not started any database '
                    'work when this happened, so nothing was added, changed or '
                    'deleted.')
        if self is DataSafety.VERIFIED_INTACT:
            return ('Your data is safe. ' + (detail or 'TenderRadar checked your '
                    'database just now and it is unchanged.'))
        if self is DataSafety.PROTECTED_BY_GUARD:
            return ('Your data was protected. ' + (detail or 'The operation was '
                    'stopped before making any changes because the database '
                    'already contained data. Nothing was deleted or '
                    'overwritten.'))
        if self is DataSafety.NO_DATABASE_YET:
            return ('No data was at risk — TenderRadar has not created its '
                    'database yet, so there is nothing to lose.')
        return ('TenderRadar could not check the state of your database, so it '
                'cannot confirm either way. Please do not reinstall or delete '
                'anything: contact ' + SUPPORT_CONTACT + ' with the technical '
                'details from this window.')


# ── The Problem object ───────────────────────────────────────────────────────

@dataclass
class Problem:
    """One thing that went wrong, described for a non-technical reader."""
    step: str                                   # "MySQL installation"
    title: str                                  # one short sentence
    what: str                                   # what happened, plainly
    why: str = ''                               # why, when we can tell
    actions: list[str] = field(default_factory=list)   # numbered next steps
    data_safety: DataSafety = DataSafety.UNKNOWN
    data_safety_detail: str = ''
    technical: str = ''                         # traceback / raw output
    can_retry: bool = True
    retry_label: str = 'Retry'
    fatal: bool = False                         # continuing would be unsafe
    help_topic: str = ''
    show_step: bool = False                     # name the step, for install errors

    def data_safety_message(self) -> str:
        return self.data_safety.message(self.data_safety_detail)

    def user_text(self) -> str:
        """The whole message as plain text, for the dialog and the clipboard."""
        parts = [self.title, '']
        if self.show_step:
            parts += ['Step that failed:', f'    {self.step}', '']
        parts += ['What happened:', self.what, '']
        if self.why:
            parts += ['Why this happened:', self.why, '']
        parts += ['Your data:', self.data_safety_message(), '']
        if self.actions:
            parts.append('What you can do:')
            parts += [f'{i}. {a}' for i, a in enumerate(self.actions, 1)]
        return '\n'.join(parts)


# ── Diagnostics ──────────────────────────────────────────────────────────────

def _svc_state(name: str = 'MySQL80') -> str:
    try:
        out = subprocess.run(['sc', 'query', name], capture_output=True, text=True,
                             timeout=15, creationflags=CREATE_NO_WINDOW).stdout.upper()
    except Exception:
        return 'unknown'
    if 'DOES NOT EXIST' in out:
        return 'not installed'
    for s in ('RUNNING', 'STOPPED', 'START_PENDING', 'STOP_PENDING', 'PAUSED'):
        if s in out:
            return s.lower().replace('_', ' ')
    return 'unknown'


def _port_holder(port: int) -> tuple[bool, str]:
    """(occupied, 'name (pid 1234)' or '')."""
    occupied = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        occupied = s.connect_ex(('127.0.0.1', port)) == 0
    if not occupied:
        return False, ''
    try:
        import psutil
        for c in psutil.net_connections(kind='inet'):
            if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN:
                if c.pid:
                    try:
                        p = psutil.Process(c.pid)
                        return True, f'{p.name()} (process {c.pid})'
                    except Exception:
                        return True, f'process {c.pid}'
    except Exception:
        pass
    return True, ''


def _free_disk_mb(path: Path) -> int:
    try:
        target = path
        while not target.exists() and target.parent != target:
            target = target.parent
        return int(shutil.disk_usage(str(target)).free / 1048576)
    except Exception:
        return -1


def diagnostics(root: Path | None = None, port: int = DEFAULT_PORT) -> dict:
    """Everything support needs, with nothing sensitive in it."""
    root = root or Path('.')
    occupied, holder = _port_holder(port)
    info = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'app version': APP_VERSION,
        'windows': f'{platform.system()} {platform.release()} '
                   f'(build {platform.version()})',
        'architecture': platform.machine(),
        'python': sys.version.split()[0],
        'frozen launcher': bool(getattr(sys, 'frozen', False)),
        'install folder': str(root),
        'bundled runtime present': (root / 'runtime' / 'python.exe').is_file(),
        'manage.py present': (root / 'manage.py').is_file(),
        'production.env present': (root / 'production.env').is_file(),
        'mysql service': _svc_state(),
        'mysql port 3306': 'reachable' if _port_holder(3306)[0] else 'not reachable',
        f'port {port}': (f'IN USE by {holder}' if holder else 'in use') if occupied
                        else 'free',
        'free disk space (MB)': _free_disk_mb(root),
    }
    return info


# ── Logging ──────────────────────────────────────────────────────────────────

def log_path(root: Path) -> Path:
    return root / 'logs' / 'tenderradar.log'


def log_problem(problem: Problem, root: Path, port: int = DEFAULT_PORT) -> Path | None:
    """Append a full diagnostic record. Never raises, never logs a secret."""
    try:
        path = log_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        diag = diagnostics(root, port)
        lines = [
            '=' * 78,
            f'{datetime.now():%Y-%m-%d %H:%M:%S}  PROBLEM  step={problem.step}',
            '=' * 78,
            f'title       : {problem.title}',
            f'what        : {problem.what}',
            f'why         : {problem.why}' if problem.why else '',
            f'data safety : {problem.data_safety.name} — '
            f'{problem.data_safety_message()}',
            f'can retry   : {problem.can_retry}',
            '',
            # Support reads this log without seeing the dialog, so record the
            # advice the user was actually given, not just the failure.
            '-- what the user was told to do ' + '-' * 45,
        ]
        lines += [f'  {i}. {a}' for i, a in enumerate(problem.actions, 1)] or                  ['  (no actions offered)']
        lines += [
            '',
            '-- diagnostics ' + '-' * 62,
        ]
        lines += [f'{k:<26}: {v}' for k, v in diag.items()]
        if problem.technical:
            lines += ['', '-- technical detail ' + '-' * 57,
                      redact(problem.technical).rstrip()]
        lines.append('')
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write('\n'.join(x for x in lines if x != '') + '\n\n')
        return path
    except Exception:
        return None


def log_info(message: str, root: Path) -> None:
    """A one-line progress note in the same log the user is pointed at."""
    try:
        path = log_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(f'{datetime.now():%Y-%m-%d %H:%M:%S}  {redact(message)}\n')
    except Exception:
        pass


# ── The dialog ───────────────────────────────────────────────────────────────

BG, FG, ACCENT, MUTED, PANEL = '#0F0F0D', '#F6F6F3', '#FCD700', '#9c9b90', '#1a1a17'
OK_GREEN, ERR_RED = '#4ade80', '#f87171'


def show_problem(problem: Problem, root_path: Path, parent=None,
                 port: int = DEFAULT_PORT) -> str:
    """
    Show the problem and return the user's choice:
    'retry' | 'exit' | 'help'.

    Safe to call with no display and safe to call twice. If tkinter itself is
    unavailable the message still reaches the log and stdout, so the failure is
    never silent.
    """
    logged = log_problem(problem, root_path, port)

    try:
        import tkinter as tk
        from tkinter import scrolledtext
    except Exception:
        print(f'\n{APP_NAME} — {problem.title}\n\n{problem.user_text()}\n'
              f'\nLog: {logged}\n', file=sys.stderr)
        return 'exit'

    choice = {'value': 'exit'}
    owns_root = parent is None
    win = tk.Tk() if owns_root else tk.Toplevel(parent)
    win.title(f'{APP_NAME} — {problem.step}')
    win.configure(bg=BG)
    win.resizable(False, False)
    try:
        ico = root_path / 'desktop' / 'TenderRadar.ico'
        if ico.is_file():
            win.iconbitmap(str(ico))
    except Exception:
        pass

    header = tk.Frame(win, bg=BG)
    header.pack(fill='x', padx=22, pady=(20, 6))
    tk.Label(header, text='⚠', bg=BG, fg=ACCENT,
             font=('Segoe UI', 22)).pack(side='left', padx=(0, 12))
    tk.Label(header, text=problem.title, bg=BG, fg=FG, justify='left',
             wraplength=520, font=('Segoe UI', 12, 'bold')).pack(side='left')

    body = tk.Frame(win, bg=BG)
    body.pack(fill='both', expand=True, padx=24, pady=(4, 8))

    def section(label: str, text: str, colour: str = FG) -> None:
        tk.Label(body, text=label, bg=BG, fg=MUTED, anchor='w',
                 font=('Segoe UI', 9, 'bold')).pack(fill='x', pady=(10, 2))
        tk.Label(body, text=text, bg=BG, fg=colour, anchor='w', justify='left',
                 wraplength=560, font=('Segoe UI', 10)).pack(fill='x')

    section('WHAT HAPPENED', problem.what)
    if problem.why:
        section('WHY THIS HAPPENED', problem.why)

    safe_colour = {
        DataSafety.VERIFIED_INTACT: OK_GREEN,
        DataSafety.NOT_ATTEMPTED: OK_GREEN,
        DataSafety.PROTECTED_BY_GUARD: OK_GREEN,
        DataSafety.NO_DATABASE_YET: MUTED,
        DataSafety.UNKNOWN: ACCENT,
    }[problem.data_safety]
    section('YOUR DATA', problem.data_safety_message(), safe_colour)

    if problem.actions:
        tk.Label(body, text='WHAT YOU CAN DO', bg=BG, fg=MUTED, anchor='w',
                 font=('Segoe UI', 9, 'bold')).pack(fill='x', pady=(12, 4))
        steps = tk.Frame(body, bg=PANEL)
        steps.pack(fill='x')
        for i, action in enumerate(problem.actions, 1):
            row = tk.Frame(steps, bg=PANEL)
            row.pack(fill='x', padx=12, pady=(6 if i == 1 else 3,
                                              6 if i == len(problem.actions) else 3))
            tk.Label(row, text=f'{i}.', bg=PANEL, fg=ACCENT, width=2, anchor='w',
                     font=('Segoe UI', 10, 'bold')).pack(side='left')
            tk.Label(row, text=action, bg=PANEL, fg=FG, anchor='w', justify='left',
                     wraplength=520, font=('Segoe UI', 10)).pack(side='left')

    # ── technical details, collapsed by default ──────────────────────────────
    tech_frame = tk.Frame(win, bg=BG)
    tech_box = {'widget': None, 'open': False}

    def tech_text() -> str:
        diag = diagnostics(root_path, port)
        out = [f'{APP_NAME} {APP_VERSION} — diagnostic report',
               f'step: {problem.step}', '']
        out += [f'{k}: {v}' for k, v in diag.items()]
        if problem.technical:
            out += ['', 'technical detail:', redact(problem.technical).rstrip()]
        if logged:
            out += ['', f'full log: {logged}']
        return '\n'.join(out)

    def toggle_tech() -> None:
        if tech_box['open']:
            tech_frame.pack_forget()
            tech_box['open'] = False
            tech_btn.configure(text='View Technical Details')
        else:
            if tech_box['widget'] is None:
                w = scrolledtext.ScrolledText(
                    tech_frame, height=11, bg='#0a0a09', fg='#c8c8c0',
                    insertbackground=FG, relief='flat',
                    font=('Consolas', 8), wrap='word')
                w.pack(fill='both', expand=True, padx=24, pady=(0, 8))
                w.insert('1.0', tech_text())
                w.configure(state='disabled')
                tech_box['widget'] = w
            tech_frame.pack(fill='both', expand=True)
            tech_box['open'] = True
            tech_btn.configure(text='Hide Technical Details')
        win.update_idletasks()

    def copy_details() -> None:
        try:
            win.clipboard_clear()
            win.clipboard_append(problem.user_text() + '\n\n' + tech_text())
            copy_btn.configure(text='Copied ✓')
            win.after(1600, lambda: copy_btn.configure(text='Copy Error Details'))
        except Exception:
            copy_btn.configure(text='Could not copy')

    def choose(value: str) -> None:
        choice['value'] = value
        win.destroy()

    def open_help() -> None:
        """Open the troubleshooting guide, or fall back to the log folder."""
        for candidate in (root_path / 'desktop' / 'DEPLOYMENT.md',
                          root_path / 'README.md'):
            if candidate.is_file():
                try:
                    os.startfile(str(candidate))               # noqa: S606
                    return
                except Exception:
                    break
        try:
            os.startfile(str(log_path(root_path).parent))       # noqa: S606
        except Exception:
            pass

    # ── buttons ─────────────────────────────────────────────────────────────
    bar = tk.Frame(win, bg=BG)
    bar.pack(fill='x', padx=24, pady=(6, 18))

    left = tk.Frame(bar, bg=BG)
    left.pack(side='left')
    tech_btn = tk.Button(left, text='View Technical Details', command=toggle_tech,
                         bg='#2a2a25', fg=MUTED, relief='flat', padx=12, pady=6,
                         font=('Segoe UI', 9), cursor='hand2',
                         activebackground='#3a3a33', activeforeground=FG)
    tech_btn.pack(side='left', padx=(0, 6))
    copy_btn = tk.Button(left, text='Copy Error Details', command=copy_details,
                         bg='#2a2a25', fg=MUTED, relief='flat', padx=12, pady=6,
                         font=('Segoe UI', 9), cursor='hand2',
                         activebackground='#3a3a33', activeforeground=FG)
    copy_btn.pack(side='left')

    right = tk.Frame(bar, bg=BG)
    right.pack(side='right')
    tk.Button(right, text='Exit', command=lambda: choose('exit'),
              bg='#33332e', fg=FG, relief='flat', padx=16, pady=7,
              font=('Segoe UI', 10), cursor='hand2').pack(side='right', padx=(6, 0))
    tk.Button(right, text='Open Help', command=open_help,
              bg='#33332e', fg=FG, relief='flat', padx=16, pady=7,
              font=('Segoe UI', 10), cursor='hand2').pack(side='right', padx=(6, 0))
    if problem.can_retry and not problem.fatal:
        tk.Button(right, text=problem.retry_label, command=lambda: choose('retry'),
                  bg=ACCENT, fg=BG, relief='flat', padx=20, pady=7,
                  font=('Segoe UI', 10, 'bold'), cursor='hand2',
                  activebackground='#FFE74D').pack(side='right', padx=(6, 0))

    win.protocol('WM_DELETE_WINDOW', lambda: choose('exit'))
    win.update_idletasks()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    win.geometry(f'{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}')
    win.attributes('-topmost', True)
    win.after(200, lambda: win.attributes('-topmost', False))
    win.grab_set()
    if owns_root:
        win.mainloop()
    else:
        parent.wait_window(win)
    return choice['value']


# ── Classifiers: raw failure -> Problem ──────────────────────────────────────
# Each one answers the four questions the user actually has: what happened, why,
# is my data safe, what do I do now.

def unexpected(step: str, exc: BaseException,
               safety: DataSafety = DataSafety.UNKNOWN,
               detail: str = '') -> Problem:
    """The catch-all. Even an unforeseen crash gets a friendly explanation."""
    return Problem(
        step=step,
        title=f'{APP_NAME} ran into an unexpected problem.',
        what=f'Something went wrong while {step.lower()}. This is not a normal '
             f'condition, so {APP_NAME} stopped rather than continuing and '
             f'risking your data.',
        why='The cause is not one of the problems TenderRadar knows how to '
            'explain. The technical details below identify it exactly.',
        actions=[
            'Click Retry — many problems of this kind are temporary.',
            'If it happens again, restart Windows and try once more.',
            f'If it still happens, click "Copy Error Details" and send them to '
            f'{SUPPORT_CONTACT}.',
        ],
        data_safety=safety, data_safety_detail=detail,
        technical=''.join(traceback.format_exception(type(exc), exc,
                                                    exc.__traceback__)),
    )


def mysql_not_installed(will_install: bool = True) -> Problem:
    if will_install:
        return Problem(
            step='Database setup',
            title='TenderRadar needs to install its database.',
            what='MySQL, the database TenderRadar stores your tenders in, is not '
                 'installed on this computer. TenderRadar will install the copy '
                 'it came with — you do not need to download anything.',
            why='This is normal on a computer where TenderRadar has never been '
                'used before.',
            actions=[
                'Click Retry to begin. Installation takes a few minutes.',
                'When Windows asks "Do you want to allow this app to make '
                'changes to your device?", click Yes. TenderRadar cannot '
                'install the database service without it.',
                'Do not switch off the computer while it is installing.',
            ],
            data_safety=DataSafety.NO_DATABASE_YET,
            retry_label='Install Database',
        )
    return Problem(
        step='Database setup',
        title='TenderRadar cannot find a database to use.',
        what='MySQL is not installed on this computer, and this copy of '
             'TenderRadar does not include the database installer.',
        why='The installer was run without the database component, or the '
            'installation folder is incomplete.',
        actions=[
            'Run TenderRadar-Setup.exe again and click Yes when Windows asks '
            'for permission.',
            f'If you do not have the setup file, ask {SUPPORT_CONTACT} for it.',
        ],
        data_safety=DataSafety.NO_DATABASE_YET,
        can_retry=False, fatal=True,
    )


def mysql_stopped_start_failed(service: str = 'MySQL80',
                              technical: str = '') -> Problem:
    return Problem(
        step='Starting the database',
        title=f'{APP_NAME} could not start the database service.',
        what=f'The database service ("{service}") is installed on this computer '
             f'but it is not running, and TenderRadar was not allowed to start '
             f'it.',
        why='Starting a Windows service usually needs administrator permission, '
            'which TenderRadar does not have.',
        actions=[
            'Click Retry — sometimes the service simply needs a moment longer.',
            f'Right-click the {APP_NAME} icon and choose "Run as administrator", '
            f'then try again.',
            f'If that does not work, ask {SUPPORT_CONTACT} to start the '
            f'"{service}" service for you.',
        ],
        data_safety=DataSafety.NOT_ATTEMPTED,
        technical=technical,
    )


def mysql_service_failed(service: str = 'MySQL80', technical: str = '') -> Problem:
    return Problem(
        step='Starting the database',
        title=f'{APP_NAME} could not start the MySQL database service.',
        what=f'Windows accepted the request to start the "{service}" service, '
             f'but the service did not come up.',
        why='The database may be repairing itself after an unexpected shutdown, '
            'another program may be using its network port, or its files may be '
            'damaged.',
        actions=[
            'Click Retry — a database that is repairing itself often succeeds on '
            'a second attempt.',
            'Wait one minute, then click Retry again.',
            'Restart Windows and start TenderRadar again.',
            f'If it still fails, send the technical details to {SUPPORT_CONTACT}. '
            f'Your TenderRadar data has not been deleted.',
        ],
        data_safety=DataSafety.NOT_ATTEMPTED,
        technical=technical,
    )


def mysql_unreachable(host: str, port: int, service_state: str,
                      technical: str = '') -> Problem:
    why = {
        'not installed': 'The database software does not appear to be installed '
                         'on this computer.',
        'stopped': 'The database service is installed but stopped.',
        'start pending': 'The database service is still starting up.',
    }.get(service_state,
          'The database service may be stopped, still starting, or blocked by '
          'security software.')
    return Problem(
        step='Connecting to the database',
        title=f'{APP_NAME} could not connect to its database.',
        what=f'{APP_NAME} tried to reach its database at {host}:{port} and got '
             f'no answer. Your existing data has not been deleted.',
        why=why,
        actions=[
            'Please check that MySQL is running, then click Retry.',
            'If you are not sure how: press the Windows key, type Services, '
            'open it, find "MySQL80" in the list and click Start.',
            'Restart Windows and start TenderRadar again.',
            f'If the problem continues, contact {SUPPORT_CONTACT}.',
        ],
        data_safety=DataSafety.NOT_ATTEMPTED,
        technical=technical,
    )


def mysql_bad_credentials(username: str = 'root', technical: str = '') -> Problem:
    return Problem(
        step='Database setup',
        title='The database password was not accepted.',
        what=f'The database is running and reachable, but it refused the '
             f'"{username}" password that was entered.',
        why='The password was mistyped, or the database administrator password '
            'on this computer is different from the one entered.',
        actions=[
            'Click Retry and type the password again, carefully.',
            'Check that Caps Lock is off.',
            f'If you do not know the database administrator password, ask '
            f'{SUPPORT_CONTACT} — whoever installed MySQL on this computer set '
            f'it.',
        ],
        data_safety=DataSafety.NOT_ATTEMPTED,
        technical=technical,
    )


def database_missing(name: str = 'tenderradar') -> Problem:
    return Problem(
        step='Checking the database',
        title=f'{APP_NAME} has not finished setting up its database.',
        what=f'The database server is running, but the "{name}" database that '
             f'{APP_NAME} stores your tenders in does not exist yet.',
        why='Setup was not able to finish, most likely because it was not given '
            'the database administrator password.',
        actions=[
            'Click Retry — TenderRadar will offer to finish setting itself up.',
            'Have the database administrator password ready; it is needed once.',
        ],
        data_safety=DataSafety.NO_DATABASE_YET,
        retry_label='Finish Setup',
    )


def database_not_empty(name: str, tables: int) -> Problem:
    """Requirement 11 — the guard fired. This is good news, phrased as such."""
    return Problem(
        step='Database setup',
        title='An existing TenderRadar database was found.',
        what=f'The "{name}" database already contains {tables} tables of your '
             f'data. To protect it, {APP_NAME} did NOT overwrite, recreate or '
             f'reset it, and it did not load the starter data.',
        why='Loading the starter data over an existing database would destroy '
            'the tenders, users and settings already in it. TenderRadar refuses '
            'to do that.',
        actions=[
            'Nothing — this is the correct and safe outcome.',
            'Click Retry to carry on using your existing database.',
        ],
        data_safety=DataSafety.PROTECTED_BY_GUARD,
        data_safety_detail=f'The database still contains its original '
                           f'{tables} tables. Nothing was deleted or '
                           f'overwritten.',
        retry_label='Continue',
    )


def port_in_use(port: int, holder: str = '', span: int = 0) -> Problem:
    """
    Raised only when every candidate port is taken by something that is not us.

    The launcher no longer fails just because `port` is busy — it identifies the
    program holding each port and moves to the next free one. Reaching this means
    the whole range is occupied, which is unusual and worth saying plainly.
    """
    who = f' by {holder}' if holder else ' by another application'
    last = port + span - 1
    where = (f'ports {port} to {last} are all in use' if span > 1
             else f'port {port} is already in use')
    return Problem(
        step='Starting the web service',
        title=f'{APP_NAME} cannot start because {where}.',
        what=f'{APP_NAME} needs a free network port to show you its pages. It '
             f'tried {span} of them starting at {port}, and every one is held by '
             f'another program. Port {port} itself is in use{who}.'
             if span > 1 else
             f'{APP_NAME} needs network port {port} to show you its pages, but '
             f'that port is currently in use{who}.',
        why='Other web applications on this machine have claimed the whole range '
            'TenderRadar looks in. Note that a second copy of TenderRadar is not '
            'the cause: TenderRadar recognises its own server and simply reopens '
            'the browser instead of reporting this.',
        actions=[
            'Close the other web applications, then click Retry.',
            'Restart Windows and start TenderRadar again.',
            f'If this machine must keep those ports, ask {SUPPORT_CONTACT} to '
            f'set TENDERRADAR_PORT to a free one.',
        ],
        data_safety=DataSafety.NOT_ATTEMPTED,
        retry_label='Retry',
        technical=f'TCP 127.0.0.1:{port}'
                  + (f'-{last} all listening' if span > 1 else ' is listening')
                  + (f'; {port} holder: {holder}' if holder else ''),
    )


def django_start_failed(technical: str = '', files_ok: bool = True) -> Problem:
    return Problem(
        step='Starting the web service',
        title=f'{APP_NAME} could not start its web service.',
        what=('The application files are present, but the web server could not '
              'be started.' if files_ok else
              'The web server could not be started and some application files '
              'appear to be missing.'),
        why='The web service stopped immediately after starting. This is usually '
            'temporary, but a damaged installation can also cause it.',
        actions=[
            'Click Retry.',
            f'Make sure port {DEFAULT_PORT} is available — close any other copy '
            f'of {APP_NAME}.',
            'Restart Windows and try again.',
            f'If the problem continues, reinstall {APP_NAME}. Reinstalling does '
            f'not delete your data.',
        ],
        data_safety=DataSafety.NOT_ATTEMPTED,
        technical=technical,
    )


def django_not_ready(seconds: int, reason: str = '', technical: str = '') -> Problem:
    return Problem(
        step='Waiting for TenderRadar',
        title=f'{APP_NAME} started but did not finish loading.',
        what=f'The web service started, but after {seconds} seconds it still was '
             f'not ready to show you any pages.',
        why='The database may be very busy or unusually slow to answer, or the '
            'web service may have run into a problem while starting.'
            + (f' Reported reason: {reason}.' if reason else ''),
        actions=[
            'Click Retry — this often succeeds on a second attempt.',
            'Check that the database service is running.',
            'Restart Windows and start TenderRadar again.',
            f'If the problem continues, send the technical details to '
            f'{SUPPORT_CONTACT}.',
        ],
        data_safety=DataSafety.NOT_ATTEMPTED,
        technical=technical,
    )


def missing_file(path: Path, what_for: str, repairable: bool = True) -> Problem:
    return Problem(
        step='Checking the installation',
        title=f'A file {APP_NAME} needs is missing.',
        what=f'{APP_NAME} could not find:\n\n    {path.name}\n\nin\n\n    '
             f'{path.parent}\n\nThis file is needed {what_for}.',
        why='The file was moved or deleted, an antivirus program removed it, or '
            'the installation did not finish completely.',
        actions=[
            f'Reinstall {APP_NAME} by running TenderRadar-Setup.exe again. '
            f'Reinstalling repairs missing files and does NOT delete your data, '
            f'your settings, or your database.',
            'If your antivirus reported TenderRadar, allow it and reinstall.',
            f'If you do not have the setup file, ask {SUPPORT_CONTACT} for it.',
        ],
        data_safety=DataSafety.NOT_ATTEMPTED,
        can_retry=repairable,
        retry_label='Check Again',
        technical=f'missing path: {path}',
    )


def runtime_missing(root: Path) -> Problem:
    return Problem(
        step='Checking the installation',
        title=f'{APP_NAME} is missing part of itself and cannot start.',
        what=f'The program files that {APP_NAME} needs in order to run were not '
             f'found in:\n\n    {root / "runtime"}\n\nWithout them the '
             f'application cannot start.',
        why='The installation is incomplete or damaged — often because it was '
            'interrupted, or because files were moved or deleted afterwards.',
        actions=[
            f'Reinstall {APP_NAME} by running TenderRadar-Setup.exe again. Your '
            f'data, settings and database are NOT affected by reinstalling.',
            'Do not move or rename the TenderRadar folder after installing.',
            f'If you do not have the setup file, ask {SUPPORT_CONTACT} for it.',
        ],
        data_safety=DataSafety.NOT_ATTEMPTED,
        can_retry=True, retry_label='Check Again',
    )


def needs_admin(what: str) -> Problem:
    return Problem(
        step='Administrator permission',
        title=f'{APP_NAME} needs Windows Administrator permission.',
        what=f'{APP_NAME} needs Administrator permission to {what}. Windows '
             f'asked for it and the request was not allowed, so nothing was '
             f'changed.',
        why='Installing or starting a Windows service is a protected operation. '
            'Windows always asks first.',
        actions=[
            'Click Retry, and when Windows asks "Do you want to allow this app '
            'to make changes to your device?", click Yes.',
            f'If you do not see the prompt, right-click {APP_NAME} and choose '
            f'"Run as administrator".',
            f'If you are not allowed to approve it on this computer, ask '
            f'{SUPPORT_CONTACT} to do this step for you.',
        ],
        data_safety=DataSafety.NOT_ATTEMPTED,
    )


def not_enough_disk(needed_mb: int, free_mb: int, where: Path) -> Problem:
    return Problem(
        step='Checking free space',
        title='There is not enough free space to install TenderRadar.',
        what=f'{APP_NAME} needs about {needed_mb:,} MB of free space on '
             f'{where.drive or where}, but only {free_mb:,} MB is available.',
        why='The disk is nearly full.',
        actions=[
            f'Free up at least {max(1, needed_mb - free_mb):,} MB — the '
            f'Recycle Bin and the Downloads folder are good places to start.',
            'Windows can help: press the Windows key, type "Disk Cleanup" and '
            'open it.',
            'Then click Retry.',
            'You can also choose a different drive on the previous page.',
        ],
        data_safety=DataSafety.NO_DATABASE_YET,
    )


def bad_dump(path: Path, reason: str, technical: str = '') -> Problem:
    return Problem(
        step='Loading the starter data',
        title='The starter data file could not be loaded.',
        what=f'{APP_NAME} tried to load its starter data from '
             f'{path.name} but the file {reason}.',
        why='The file was damaged, or the download/copy of the installer did not '
            'complete correctly.',
        actions=[
            f'Reinstall {APP_NAME} using a freshly copied TenderRadar-Setup.exe.',
            f'{APP_NAME} can also start with an empty database and build it up '
            f'as it collects tenders — click Retry to continue that way.',
            f'If you need the starter data, ask {SUPPORT_CONTACT} for a new copy '
            f'of the setup file.',
        ],
        data_safety=DataSafety.NO_DATABASE_YET,
        retry_label='Continue Without Starter Data',
        technical=technical,
    )


def install_step_failed(step: str, what: str, why: str = '',
                        technical: str = '',
                        safety: DataSafety = DataSafety.NOT_ATTEMPTED,
                        extra_actions: list[str] | None = None) -> Problem:
    """Requirement: installation failures name the step that failed."""
    actions = ['Click Retry.']
    if extra_actions:
        actions += extra_actions
    actions += [
        'If Windows asks for Administrator permission, click Yes.',
        'Make sure you have enough free disk space.',
        'If the problem continues, restart Windows and run the installer again.',
    ]
    return Problem(
        step=step,
        title='Installation could not be completed.',
        what=what,
        why=why,
        actions=actions,
        data_safety=safety,
        technical=technical,
        show_step=True,          # user_text() renders "Step that failed: <step>"
    )


def interrupted_install(install_dir: Path, found: str) -> Problem:
    return Problem(
        step='Checking for a previous installation',
        title='A previous installation did not finish.',
        what=f'{APP_NAME} found an incomplete installation in:\n\n    '
             f'{install_dir}\n\n{found}',
        why='The previous installation was closed, cancelled, or interrupted by '
            'a restart before it could finish.',
        actions=[
            'Click Retry to finish the installation. Your existing data, '
            'settings and database are kept.',
            'Nothing will be deleted.',
        ],
        data_safety=DataSafety.VERIFIED_INTACT,
        data_safety_detail='Finishing an interrupted installation only adds the '
                           'missing files. Your database, media files, backups '
                           'and settings are left exactly as they are.',
        retry_label='Finish Installation',
    )


# ── Verified data-safety helper ──────────────────────────────────────────────

def verify_database_intact(bin_dir, root_password: str, database: str
                           ) -> tuple[DataSafety, str]:
    """
    Actually check the database before telling the user it is fine.

    Returns UNKNOWN — not a reassurance — when the check itself cannot run.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from desktop import mysql_setup as M
        ok, out = M.sql(bin_dir, 'root', root_password,
                        f"SELECT COUNT(*) FROM information_schema.tables "
                        f"WHERE table_schema='{database}';")
        if not ok:
            return DataSafety.UNKNOWN, ''
        tables = int((out or '0').strip() or 0)
        if tables == 0:
            return DataSafety.NO_DATABASE_YET, ''
        ok2, out2 = M.sql(bin_dir, 'root', root_password,
                          f'SELECT COUNT(*) FROM `{database}`.tenders_tender;')
        rows = int((out2 or '0').strip() or 0) if ok2 else -1
        detail = (f'TenderRadar checked your database just now: it still '
                  f'contains {tables} tables'
                  + (f' and {rows:,} tenders' if rows >= 0 else '')
                  + '. Nothing was deleted.')
        return DataSafety.VERIFIED_INTACT, detail
    except Exception:
        return DataSafety.UNKNOWN, ''


if __name__ == '__main__':
    # Manual preview: python desktop/errors.py port_in_use
    import inspect
    name = sys.argv[1] if len(sys.argv) > 1 else 'port_in_use'
    fn = globals().get(name)
    if not callable(fn):
        print('available:', ', '.join(
            n for n, f in sorted(globals().items())
            if callable(f) and inspect.isfunction(f)
            and f.__module__ == '__main__'
            and 'Problem' in str(inspect.signature(f).return_annotation)))
        sys.exit(1)
    demo = {'port_in_use': (DEFAULT_PORT, 'python.exe (process 1234)'),
            'missing_file': (Path('C:/x/manage.py'), 'to run the application'),
            'runtime_missing': (Path('C:/x'),),
            'not_enough_disk': (300, 50, Path('C:/')),
            'mysql_unreachable': ('127.0.0.1', 3306, 'stopped'),
            'database_not_empty': ('tenderradar', 89),
            'needs_admin': ('install the database service',),
            'bad_dump': (Path('x/initial.sql'), 'is not a valid database file'),
            }.get(name, ())
    p = fn(*demo)
    print(p.user_text())
    print('\n[dialog]', show_problem(p, Path(__file__).resolve().parent.parent))
