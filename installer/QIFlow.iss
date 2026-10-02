; Per-user installer for QI Flow. It intentionally does not remove AppData on uninstall.

#define MyAppName "QI Flow"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.1"
#endif
#ifndef MyAppBundleDir
  #define MyAppBundleDir "..\dist\QI Flow"
#endif
#ifndef MyAppLauncher
  #define MyAppLauncher "..\dist\QI Flow Launcher.exe"
#endif
#ifndef MyAppOutputDir
  #define MyAppOutputDir "..\dist\installer"
#endif
#define MyAppPublisher "QI Flow"
#define MyLauncherExeName "QI Flow Launcher.exe"

[Setup]
AppId={{80AA9854-A6DC-49CA-B507-893AB75DC74B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\QI Flow
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
CloseApplications=yes
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#MyAppOutputDir}
OutputBaseFilename=QI-Flow-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyLauncherExeName}

[InstallDelete]
Name: "{app}\current"; Type: filesandordirs
Name: "{app}\update-work"; Type: filesandordirs
Name: "{app}\update-transaction.json"; Type: files
Name: "{app}\update.lock"; Type: files

[Files]
Source: "{#MyAppBundleDir}\*"; DestDir: "{app}\current"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MyAppLauncher}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\QI Flow"; Filename: "{app}\{#MyLauncherExeName}"
Name: "{autodesktop}\QI Flow"; Filename: "{app}\{#MyLauncherExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\{#MyLauncherExeName}"; Description: "Launch QI Flow"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Name: "{app}\current"; Type: filesandordirs
Name: "{app}\update-work"; Type: filesandordirs
Name: "{app}\update-transaction.json"; Type: files
Name: "{app}\update.lock"; Type: files
Name: "{app}\QI Flow.exe"; Type: files
Name: "{app}\QI Flow Updater.exe"; Type: files
Name: "{app}\_internal"; Type: filesandordirs

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

procedure RegisterExtraCloseApplicationsResources;
begin
  RegisterExtraCloseApplicationsResource(False, ExpandConstant('{app}\QI Flow.exe'));
  RegisterExtraCloseApplicationsResource(False, ExpandConstant('{app}\{#MyLauncherExeName}'));
end;

function LegacyStartupCommand: String;
begin
  Result := '"' + ExpandConstant('{app}\QI Flow.exe') + '" --start-minimized';
end;

function LauncherStartupCommand: String;
begin
  Result := '"' + ExpandConstant('{app}\{#MyLauncherExeName}') + '" --start-minimized';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExistingCommand: String;
begin
  if CurStep = ssPostInstall then
  begin
    if RegQueryStringValue(HKCU, RunKey, 'QI Flow', ExistingCommand) and
      (Lowercase(ExistingCommand) = Lowercase(LegacyStartupCommand)) then
      RegWriteStringValue(HKCU, RunKey, 'QI Flow', LauncherStartupCommand);

    { An older updater replaced the whole install folder and could leave this copy behind. }
    DelTree(ExtractFileDir(ExpandConstant('{app}')) + '\QI Flow.previous', True, True, True);
    { Remove known legacy app files after the stable launch path has been installed. }
    DeleteFile(ExpandConstant('{app}\QI Flow.exe'));
    DeleteFile(ExpandConstant('{app}\QI Flow Updater.exe'));
    DelTree(ExpandConstant('{app}\_internal'), True, True, True);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ExistingCommand: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    if RegQueryStringValue(HKCU, RunKey, 'QI Flow', ExistingCommand) and
      ((Lowercase(ExistingCommand) = Lowercase(LauncherStartupCommand)) or
       (Lowercase(ExistingCommand) = Lowercase(LegacyStartupCommand))) then
      RegDeleteValue(HKCU, RunKey, 'QI Flow');
  end;
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
  begin
    MsgBox(
      'QI Flow was removed. Your time records, settings, backups, and logs remain in your private Windows application-data folder.',
      mbInformation,
      MB_OK);
  end;
end;
