#ifndef AppVersion
  #error AppVersion must be supplied by build_release.py
#endif
[Setup]
AppId={{AAFF972D-60A9-4CF1-B8B1-AAC84E3558EC}
AppName=CCS 指控平台
AppVersion={#AppVersion}
AppPublisher=AADCL
DefaultDirName={localappdata}\Programs\CCS
DefaultGroupName=CCS
DisableDirPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=CCS-{#AppVersion}-windows-x64-setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\CCS.exe
CloseApplications=yes
RestartApplications=no
SetupIconFile={#AppIcon}
LicenseFile={#SourceDir}\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "config\*,data\*"
Source: "{#SourceDir}\config\*"; DestDir: "{app}\config"; Flags: onlyifdoesntexist uninsneveruninstall recursesubdirs createallsubdirs
Source: "{#SourceDir}\data\*"; DestDir: "{app}\data"; Flags: onlyifdoesntexist uninsneveruninstall recursesubdirs createallsubdirs
[Icons]
Name: "{group}\CCS 指控平台"; Filename: "{app}\CCS.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall CCS"; Filename: "{uninstallexe}"
Name: "{autodesktop}\CCS 指控平台"; Filename: "{app}\CCS.exe"; WorkingDir: "{app}"; Tasks: desktopicon
[Run]
Filename: "{app}\CCS.exe"; Description: "Launch CCS"; Flags: nowait postinstall skipifsilent
