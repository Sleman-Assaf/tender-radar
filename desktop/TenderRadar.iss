; TenderRadar.iss — Inno Setup script
;
; The shipped TenderRadar_Setup.exe was NOT built with Inno Setup: this network's
; proxy replaces the jrsoftware.org download with an HTML block page, so the
; toolchain could not be obtained here (verified: HEAD returns 200 but GET
; returns a 10 KB XHTML block page; the versioned URL 404s).
;
; This script reproduces the same package with Inno Setup on a machine that can
; reach jrsoftware.org. Build with:
;
;     desktop\build_runtime.py       (once — produces desktop\payload\runtime)
;     desktop\build_exe.py           (produces TenderRadar.exe)
;     ISCC.exe desktop\TenderRadar.iss
;
; Output: desktop\dist\TenderRadar_Setup.exe

#define MyAppName "TenderRadar"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "AJi Group"
#define MyAppExeName "TenderRadar.exe"

[Setup]
AppId={{8F3A1C42-7B6E-4D19-9E5A-TENDERRADAR01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=TenderRadar_Setup
SetupIconFile=TenderRadar.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user install: no administrator rights needed, and the application folder
; stays writable — required because the Scraper Factory generates .py modules
; into tenders\scrapers at runtime.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &Desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "startmenuicon"; Description: "Create a &Start Menu shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; The Django application, exactly as it runs — NOT frozen, so it stays writable.
Source: "payload\app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Self-contained CPython + every dependency: the user needs no Python installed.
Source: "payload\runtime\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
; The launcher.
Source: "..\TenderRadar.exe"; DestDir: "{app}"; Flags: ignoreversion
; Configuration: never overwrite an existing production.env.
Source: "payload\app\production.env"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall

[Dirs]
; Must exist and stay writable at runtime.
Name: "{app}\logs"
Name: "{app}\backups"
Name: "{app}\media"
Name: "{app}\media\feedback_attachments"
Name: "{app}\staticfiles"
Name: "{app}\scraper_factory\snapshots"
Name: "{app}\tenders\scrapers"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: startmenuicon
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"; Tasks: startmenuicon
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Generated at runtime — remove so no empty folders are left behind.
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\staticfiles"
Type: filesandordirs; Name: "{app}\__pycache__"
; NOTE: media, backups and production.env are deliberately preserved.
; The MySQL service and the `tenderradar` database are never touched.

[Code]
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
  MySQLOk: Boolean;
begin
  { Warn — do not block — if the MySQL service is missing. The database may be
    on another host, configured through DB_HOST in production.env. }
  MySQLOk := Exec('sc', 'query MySQL80', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
             and (ResultCode = 0);
  if not MySQLOk then
    if MsgBox('MySQL 8.0 (service MySQL80) was not detected on this computer.' #13#10#13#10
              'TenderRadar needs a MySQL 8.0 server. If your database is on another '
              'machine you can continue and set DB_HOST in production.env after '
              'installation.' #13#10#13#10 'Continue anyway?',
              mbConfirmation, MB_YESNO) = IDNO then
    begin
      Result := False;
      Exit;
    end;
  Result := True;
end;
