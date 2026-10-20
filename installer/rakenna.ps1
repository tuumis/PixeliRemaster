# PixeliRemaster – asennuspaketin sisällön kokoaminen (GitHub Actions / Windows)
#
#   build\python   upotettava Python 3.12 + numpy + pywebview (PyTorch ladataan ensimmäisellä käynnistyksellä)
#   build\app      sovellus, käyttöliittymä ja AI-mallit
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$juuri = Split-Path -Parent $PSScriptRoot
$build = Join-Path $juuri "build"
$pyver = "3.12.10"

if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Force (Join-Path $build "python") | Out-Null

Write-Host "Ladataan Python $pyver (embeddable)"
$zip = Join-Path $build "python.zip"
Invoke-WebRequest "https://www.python.org/ftp/python/$pyver/python-$pyver-embed-amd64.zip" -OutFile $zip
Expand-Archive $zip (Join-Path $build "python")
Remove-Item $zip

# site-packages käyttöön ja sovelluskansio polulle
$pth = Get-ChildItem (Join-Path $build "python\python3*._pth") | Select-Object -First 1
$stdzip = (Get-ChildItem (Join-Path $build "python\python3*.zip") | Select-Object -First 1).Name
Set-Content $pth.FullName -Value @($stdzip, ".", "Lib\site-packages", "..\app", "import site") -Encoding ascii

Write-Host "Asennetaan pip, numpy ja pywebview"
$py = Join-Path $build "python\python.exe"
Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile (Join-Path $build "get-pip.py")
& $py (Join-Path $build "get-pip.py") --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "get-pip epäonnistui" }
Remove-Item (Join-Path $build "get-pip.py")
& $py -m pip install --no-warn-script-location --disable-pip-version-check "numpy==2.2.6" "pywebview==5.4"
if ($LASTEXITCODE -ne 0) { throw "pip install epäonnistui" }
& $py -c "import numpy, webview, clr_loader; print('numpy', numpy.__version__, 'pywebview', webview.__version__ if hasattr(webview, '__version__') else '?')"
if ($LASTEXITCODE -ne 0) { throw "kirjastojen tuonti epäonnistui" }

Write-Host "Kopioidaan sovellus"
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
