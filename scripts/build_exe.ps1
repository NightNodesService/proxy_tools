$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot ".venv")
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -e "$ProjectRoot[build]"
& $VenvPython -m playwright install chromium

$EntryPoint = Join-Path $ProjectRoot "scripts\pyinstaller_entry.py"
$ConfigSource = Join-Path $ProjectRoot "config"
$AssetsSource = Join-Path $ProjectRoot "assets"
$IconSource = Join-Path $AssetsSource "nightnodes_logo.ico"
$BrowserSource = Join-Path $env:LOCALAPPDATA "ms-playwright"
$DistPath = Join-Path $ProjectRoot "dist"
$WorkPath = Join-Path $ProjectRoot "build"
$AppExeName = "proxy_tools_v0.2.0-beta.exe"

if (-not (Test-Path -LiteralPath $BrowserSource)) {
    throw "Playwright browsers were not found: $BrowserSource"
}

$ProcessNames = @("proxy_tools", "proxy_tools_v0.2.0-beta", "proxy_tools_v0.2.0-beta.1")
foreach ($ProcessName in $ProcessNames) {
    $RunningApps = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue
    if ($RunningApps) {
        $RunningApps | Stop-Process -Force
    }
}
$DistExePattern = (Join-Path $DistPath "proxy_tools").Replace("\", "\\")
Get-CimInstance Win32_Process |
    Where-Object { $_.ExecutablePath -like "$DistPath\proxy_tools\*" -or $_.CommandLine -like "*$DistExePattern*" } |
    ForEach-Object {
        try {
            Invoke-CimMethod -InputObject $_ -MethodName Terminate -ErrorAction Stop | Out-Null
        } catch {
            # The process may have already exited.
        }
    }
Start-Sleep -Seconds 1

$ChromeProfilePaths = @(
    (Join-Path $ProjectRoot "config\chrome-profiles"),
    (Join-Path $DistPath "proxy_tools\config\chrome-profiles")
)
foreach ($ProfilePath in $ChromeProfilePaths) {
    if (Test-Path -LiteralPath $ProfilePath) {
        $ResolvedProfilePath = (Resolve-Path -LiteralPath $ProfilePath).Path
        Get-CimInstance Win32_Process -Filter "name = 'chrome.exe'" |
            Where-Object { $_.CommandLine -like "*$ResolvedProfilePath*" } |
            ForEach-Object {
                try {
                    Invoke-CimMethod -InputObject $_ -MethodName Terminate -ErrorAction Stop | Out-Null
                } catch {
                    # Chrome child processes may disappear after their parent exits.
                }
            }
        Start-Sleep -Milliseconds 500
        Remove-Item -LiteralPath $ResolvedProfilePath -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$IconArgs = @()
if (Test-Path -LiteralPath $IconSource) {
    $IconArgs = @("--icon", $IconSource)
}

& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name proxy_tools `
    --distpath $DistPath `
    --workpath $WorkPath `
    @IconArgs `
    --add-data "$ConfigSource;config" `
    --add-data "$AssetsSource;assets" `
    --add-data "$BrowserSource;ms-playwright" `
    $EntryPoint

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$DefaultExePath = Join-Path $DistPath "proxy_tools\proxy_tools.exe"
$VersionedExePath = Join-Path $DistPath "proxy_tools\$AppExeName"
if (-not (Test-Path -LiteralPath $DefaultExePath)) {
    throw "Build finished but exe was not found: $DefaultExePath"
}

if (Test-Path -LiteralPath $VersionedExePath) {
    Remove-Item -LiteralPath $VersionedExePath -Force
}
Rename-Item -LiteralPath $DefaultExePath -NewName $AppExeName

Write-Host "Built: $VersionedExePath"
