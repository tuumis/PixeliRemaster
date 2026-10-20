; PixeliRemaster – Inno Setup -asennusohjelma
; Rakennetaan GitHub Actionsissa: ISCC.exe /DVersio=1.0.0 installer\pixeliremaster.iss

#ifndef Versio
  #define Versio "1.0.0"
#endif

[Setup]
AppId={{6C1E2B57-3A3E-4C55-9C1B-5E0A4F3C8D21}
AppName=PixeliRemaster
AppVersion={#Versio}
AppVerName=PixeliRemaster {#Versio}
AppPublisher=PixeliRemaster
AppPublisherURL=https://github.com/tuumis/PixeliRemaster
AppSupportURL=https://github.com/tuumis/PixeliRemaster/issues
DefaultDirName={localappdata}\Programs\PixeliRemaster
DefaultGroupName=PixeliRemaster
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=PixeliRemaster-{#Versio}-Setup
SetupIconFile=..\app\web\pixeli.ico
UninstallDisplayIcon={app}\app\web\pixeli.ico
UninstallDisplayName=PixeliRemaster
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "fi"; MessagesFile: "compiler:Languages\Finnish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
fi.Kontekstivalikko=Lisää video- ja kuvatiedostojen hiiren oikean painikkeen valikkoon "Paranna PixeliRemasterilla"
en.Kontekstivalikko=Add "Enhance with PixeliRemaster" to the right-click menu of video and image files
fi.Paranna=Paranna PixeliRemasterilla
en.Paranna=Enhance with PixeliRemaster
fi.Kaynnista=Käynnistä PixeliRemaster
en.Kaynnista=Launch PixeliRemaster
fi.PoistaData=Poistetaanko myös ladatut komponentit (tekoälykirjasto, videotyökalut) ja työtiedostot?%n%n%1%n%nValmiit videot ja kuvat eivät poistu.
en.PoistaData=Also remove downloaded components (AI library, video tools) and working files?%n%n%1%n%nFinished videos and images are not removed.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "kontekstivalikko"; Description: "{cm:Kontekstivalikko}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\build\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; vanhan version sovelluskoodi pois (python-kansio ja ladattu PyTorch säilyvät päivityksessä)
Type: filesandordirs; Name: "{app}\app\pixeli"
Type: filesandordirs; Name: "{app}\app\web"

[Icons]
Name: "{autoprograms}\PixeliRemaster"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\kaynnista.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\web\pixeli.ico"; AppUserModelID: "PixeliRemaster.Sovellus"
Name: "{autodesktop}\PixeliRemaster"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\kaynnista.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\app\web\pixeli.ico"; AppUserModelID: "PixeliRemaster.Sovellus"; Tasks: desktopicon

[Registry]
; "Paranna PixeliRemasterilla" video- ja kuvatiedostojen hiiren oikean painikkeen valikkoon
#dim Paatteet[18] {".mkv", ".mp4", ".avi", ".vob", ".mpg", ".mpeg", ".m2v", ".ts", ".m4v", ".mov", ".wmv", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
#define i 0
#sub RekisteriRivit
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#Paatteet[i]}\shell\PixeliRemaster"; ValueType: string; ValueName: ""; ValueData: "{cm:Paranna}"; Flags: uninsdeletekey; Tasks: kontekstivalikko
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#Paatteet[i]}\shell\PixeliRemaster"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\app\web\pixeli.ico"; Tasks: kontekstivalikko
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\{#Paatteet[i]}\shell\PixeliRemaster\command"; ValueType: string; ValueName: ""; ValueData: """{app}\python\pythonw.exe"" ""{app}\app\kaynnista.py"" ""%1"""; Tasks: kontekstivalikko
#endsub
#for {i = 0; i < DimOf(Paatteet); i++} RekisteriRivit

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\app\kaynnista.py"""; WorkingDir: "{app}\app"; Description: "{cm:Kaynnista}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; ensimmäisellä käynnistyksellä ladattu PyTorch on python-kansiossa
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{localappdata}\PixeliRemaster');
    if DirExists(Data) and not UninstallSilent then
      if MsgBox(FmtMessage(CustomMessage('PoistaData'), [Data]), mbConfirmation, MB_YESNO) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
