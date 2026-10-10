"""
Uninstall.exe — removes an installed TenderRadar.

Deletes the application folder, the shortcuts and the Programs-and-Features
entry. It deliberately leaves alone everything that is not ours to delete:

  * the MySQL service and the `tenderradar` database
  * any backup archives the user chose to keep elsewhere

The user is asked whether to keep the configuration (production.env) and the
data folders (media, backups, logs).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

APP_NAME = 'TenderRadar'
PUBLISHER = 'AJi Group'
APP_ID = 'TenderRadar'
EXE_NAME = 'TenderRadar.exe'
CREATE_NO_WINDOW = 0x08000000
KEEPABLE = ['production.env', 'media', 'backups', 'logs']


def install_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            rf'Software\{PUBLISHER}\{APP_NAME}') as k:
            return Path(winreg.QueryValueEx(k, 'InstallDir')[0])
    except OSError:
        return Path(__file__).resolve().parent.parent


def stop_running(root: Path) -> None:
    """Stop the launcher and its server before deleting files."""
    try:
        subprocess.run(['taskkill', '/IM', EXE_NAME, '/F', '/T'],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass
    # any python from our own runtime folder
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "
        f"'{root}\\runtime\\*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"
    )
    try:
        subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                       capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60)
    except Exception:
        pass


def remove_shortcuts() -> list[str]:
    removed = []
    desktop = Path(os.environ['USERPROFILE']) / 'Desktop' / f'{APP_NAME}.lnk'
    if desktop.exists():
        desktop.unlink(missing_ok=True); removed.append('Desktop shortcut')
    folder = (Path(os.environ['APPDATA']) /
              r'Microsoft\Windows\Start Menu\Programs' / APP_NAME)
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True); removed.append('Start Menu folder')
    return removed


def remove_registry() -> None:
    for hive, path in (
        (winreg.HKEY_CURRENT_USER,
         rf'Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}'),
        (winreg.HKEY_CURRENT_USER, rf'Software\{PUBLISHER}\{APP_NAME}'),
    ):
        try:
            winreg.DeleteKey(hive, path)
        except OSError:
            pass


def remove_windows_service(root: Path, log) -> None:
    """
    Stop and unregister the Windows service, and stop the tray starting again.

    Removing a service needs administrator rights. If they are missing, say so
    with the exact command rather than leaving a stale service pointing at a
    folder that is about to disappear.
    """
    script = root / 'desktop' / 'service.py'
    python = root / 'runtime' / 'python.exe'
    if script.is_file() and python.is_file():
        try:
            r = subprocess.run([str(python), str(script), 'remove'],
                               capture_output=True, text=True, timeout=120,
                               creationflags=0x08000000)
            if r.returncode == 0:
                log('  Windows Service removed')
            else:
                log('  Windows Service NOT removed — administrator rights are '
                    'required')
                log(f'  Run: "{python}" "{script}" remove')
        except Exception as exc:
            log(f'  Windows Service could not be removed: {type(exc).__name__}')

    # the tray autostart is per-user, so it never needs elevation
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Run', 0,
                winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, 'TenderRadar Tray')
        log('  tray removed from Windows startup')
    except FileNotFoundError:
        pass
    except OSError:
        pass
    # and stop it if it is running now
    try:
        subprocess.run(['taskkill', '/IM', 'pythonw.exe', '/FI',
                        'WINDOWTITLE eq TenderRadar*', '/F'],
                       capture_output=True, creationflags=0x08000000)
    except Exception:
        pass


def forget_stored_credentials(root: Path, keep_data: bool, log) -> None:
    """
    Drop the encrypted production.env copy kept outside the install folder.

    Only on a full removal. A normal uninstall keeps production.env (it is in
    KEEPABLE), so the backup must survive too — otherwise reinstalling would find
    the file but no way to recover it if it were lost later.
    """
    if keep_data:
        return
    try:
        sys.path.insert(0, str(root))
        from desktop import env_vault
        if env_vault.forget(log):
            log('  removed the stored credential backup')
    except Exception:
        pass                      # older install, or already gone — not an error


def delete_app(root: Path, keep_data: bool, log) -> None:
    # the service must go before the files it points at
    remove_windows_service(root, log)
    forget_stored_credentials(root, keep_data, log)
    keep = set(KEEPABLE) if keep_data else set()
    for item in sorted(root.iterdir()):
        if item.name in keep:
            log(f'  kept: {item.name}')
            continue
        if item.name.lower() == 'uninstall.exe':
            continue                      # deleted last, by the batch stub
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        except Exception as exc:
            log(f'  could not remove {item.name}: {exc}')
    # self-delete after we exit
    stub = root / '_finish_uninstall.cmd'
    try:
        stub.write_text(
            '@echo off\r\n'
            'ping -n 3 127.0.0.1 >nul\r\n'
            f'del /q "{root / "Uninstall.exe"}" 2>nul\r\n'
            f'rmdir "{root}" 2>nul\r\n'
            f'del /q "%~f0" 2>nul\r\n', encoding='utf-8')
        subprocess.Popen(['cmd', '/c', str(stub)], cwd=str(root.parent),
                         creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


def main() -> int:
    root = install_dir()
    silent = any(a.upper() in ('/SILENT', '/VERYSILENT') for a in sys.argv[1:])
    keep_data = not any(a.upper() == '/PURGE' for a in sys.argv[1:])

    if silent:
        out: list[str] = []
        stop_running(root)
        out += [f'removed: {x}' for x in remove_shortcuts()]
        remove_registry()
        delete_app(root, keep_data, out.append)
        print('\n'.join(out))
        print('RESULT SUCCESS', root)
        return 0

    import tkinter as tk
    from tkinter import messagebox, scrolledtext

    BG, FG, ACCENT, PANEL = '#0F0F0D', '#F6F6F3', '#FCD700', '#1a1a17'
    win = tk.Tk()
    win.title(f'Uninstall {APP_NAME}')
    win.geometry('600x420'); win.configure(bg=BG); win.resizable(False, False)

    tk.Label(win, text=f'Uninstall {APP_NAME}', bg=BG, fg=ACCENT,
             font=('Segoe UI', 16, 'bold')).pack(pady=(18, 4))
    tk.Label(win, text=str(root), bg=BG, fg='#9c9b90',
             font=('Consolas', 9)).pack()

    tk.Label(win, text='Your MySQL database is NOT removed.\n'
                       'The tenderradar database and the MySQL service are left untouched.',
             bg=BG, fg=FG, font=('Segoe UI', 10), justify='left').pack(pady=12)

    keep = tk.BooleanVar(value=True)
    tk.Checkbutton(win, text='Keep configuration and data (production.env, media, backups, logs)',
                   variable=keep, bg=BG, fg=FG, selectcolor=PANEL,
                   activebackground=BG, activeforeground=FG,
                   font=('Segoe UI', 9)).pack()

    box = scrolledtext.ScrolledText(win, height=9, bg=PANEL, fg='#d9d8cf',
                                    font=('Consolas', 9), relief='flat')
    box.pack(fill='both', expand=True, padx=18, pady=12)
    box.configure(state='disabled')

    def log(msg):
        box.configure(state='normal'); box.insert('end', msg + '\n')
        box.see('end'); box.configure(state='disabled'); win.update_idletasks()

    btns = tk.Frame(win, bg=BG); btns.pack(pady=(0, 16))

    def do_uninstall():
        if not messagebox.askyesno('Uninstall', f'Remove {APP_NAME} from this computer?'):
            return
        go.configure(state='disabled'); cancel.configure(state='disabled')
        log('Stopping TenderRadar...'); stop_running(root)
        for x in remove_shortcuts():
            log(f'Removed {x}')
        remove_registry(); log('Removed Programs and Features entry')
        log('Deleting application files...')
        delete_app(root, keep.get(), log)
        log('Done.')
        messagebox.showinfo('Uninstall', f'{APP_NAME} has been removed.')
        win.destroy()

    go = tk.Button(btns, text='Uninstall', command=do_uninstall, bg=ACCENT, fg=BG,
                   relief='flat', font=('Segoe UI', 10, 'bold'), padx=20, pady=7,
                   cursor='hand2')
    go.pack(side='left', padx=6)
    cancel = tk.Button(btns, text='Cancel', command=win.destroy, bg='#33332e', fg=FG,
                       relief='flat', font=('Segoe UI', 10), padx=20, pady=7,
                       cursor='hand2')
    cancel.pack(side='left', padx=6)

    win.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
