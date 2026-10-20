# PixeliRemaster – asennuspaketin sisällön kokoaminen (GitHub Actions / Windows)
#
#   build\python   upotettava Python 3.12 + numpy + pywebview (PyTorch ladataan ensimmäisellä käynnistyksellä)
#   build\app      sovellus, käyttöliittymä ja AI-mallit
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
trap {
  # virhe GitHubin merkinnäksi, jotta sen näkee ajon yhteenvedosta
  $viesti = "$($_.Exception.Message) (rivi $($_.InvocationInfo.ScriptLineNumber): $($_.InvocationInfo.Line.Trim()))"
  Write-Host "::error title=rakenna.ps1::$viesti"
  exit 1
}
function Vaihe($teksti) { Write-Host "::notice title=Vaihe::$teksti"; Write-Host $teksti }

$juuri = Split-Path -Parent $PSScriptRoot
$build = Join-Path $juuri "build"
$pyver = "3.12.10"

if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Force (Join-Path $build "python") | Out-Null

Vaihe "Ladataan Python $pyver (embeddable)"
$zip = Join-Path $build "python.zip"
Invoke-WebRequest "https://www.python.org/ftp/python/$pyver/python-$pyver-embed-amd64.zip" -OutFile $zip
Expand-Archive $zip (Join-Path $build "python")
Remove-Item $zip

# site-packages käyttöön ja sovelluskansio polulle
$pth = Get-ChildItem (Join-Path $build "python\python3*._pth") | Select-Object -First 1
$stdzip = (Get-ChildItem (Join-Path $build "python\python3*.zip") | Select-Object -First 1).Name
Set-Content $pth.FullName -Value @($stdzip, ".", "Lib\site-packages", "..\app", "import site") -Encoding ascii

Vaihe "Asennetaan pip, numpy ja pywebview"
$py = Join-Path $build "python\python.exe"
Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile (Join-Path $build "get-pip.py")
& $py (Join-Path $build "get-pip.py") --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "get-pip epäonnistui" }
Remove-Item (Join-Path $build "get-pip.py")
function Pip([string[]]$paketit, [string[]]$lisa = @()) {
  $tuloste = & $py -m pip install --no-warn-script-location --disable-pip-version-check @lisa @paketit 2>&1
  $tuloste | Write-Host
  if ($LASTEXITCODE -ne 0) {
    throw ("pip install " + ($paketit -join " ") + ": " + (($tuloste | Select-Object -Last 6) -join " | "))
  }
}
# Upotettu Python ohittaa PYTHONPATHin (._pth), joten pipin eristetty rakennus ei toimi:
# lähdekoodipaketit (esim. proxy_tools) rakennetaan ilman eristystä.
Pip @("setuptools<82", "wheel")   # PyTorch vaatii setuptools<82; päivitys ei saa korvata sitä uudemmalla
Pip @("numpy==2.2.6", "pywebview==5.4") @("--no-build-isolation")
$tuonti = & $py -c "import numpy, webview; print('numpy', numpy.__version__)" 2>&1
Vaihe "Kirjastot: $tuonti"
if ($LASTEXITCODE -ne 0) { throw "kirjastojen tuonti epäonnistui" }

Vaihe "Kopioidaan sovellus"
$app = Join-Path $build "app"
Copy-Item -Recurse (Join-Path $juuri "app") $app
Get-ChildItem $app -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem (Join-Path $build "python") -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# versio sovelluksen lähdekoodista
$versio = (Select-String -Path (Join-Path $juuri "app\pixeli\moottori.py") -Pattern '^VERSIO = "([^"]+)"').Matches[0].Groups[1].Value
Write-Host "Versio $versio"
if ($env:GITHUB_ENV) { "VERSIO=$versio" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8 }
if ($env:GITHUB_REF_NAME -and $env:GITHUB_REF_NAME.StartsWith("v") -and $env:GITHUB_REF_NAME -ne "v$versio") {
  throw "Tagi $($env:GITHUB_REF_NAME) ei vastaa ohjelman versiota $versio (app\pixeli\moottori.py)"
}
