; Compile only via scripts/build_windows.py, after the full app is assembled.
[Setup]
AppId=org.layerloom.LayerLoom
AppName=LayerLoom
AppVersion={#AppVersion}
AppPublisher=LayerLoom contributors
AppPublisherURL=https://github.com/Finn2400/layerloom
DefaultDirName={localappdata}\Programs\LayerLoom
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.19041
DisableProgramGroupPage=yes
DisableDirPage=yes
UninstallDisplayIcon={app}\LayerLoom.exe
SetupIconFile={#IconFile}
OutputDir={#OutputDir}
OutputBaseFilename=LayerLoom-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
LicenseFile=..\..\LICENSE

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\LayerLoom"; Filename: "{app}\LayerLoom.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\LayerLoom.exe"; Description: "Open LayerLoom"; Flags: nowait postinstall skipifsilent
