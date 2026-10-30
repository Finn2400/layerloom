# Requires only the PowerShell included in Windows; no Python, Git, or PATH edits.
$ErrorActionPreference = 'Stop'
# Use the same flags in all three launchers. No param block: PowerShell 5 passes
# these literal command-line arguments through $args without named-parameter rules.
$repair = $false
$doctorOnly = $false
foreach ($argument in $args) {
    switch ($argument) {
        '--repair' { $repair = $true }
        '-Repair' { $repair = $true }
        '--doctor-only' { $doctorOnly = $true }
        '-DoctorOnly' { $doctorOnly = $true }
        default { Write-Host 'Usage: Launch LayerLoom.bat [--repair] [--doctor-only]'; exit 1 }
    }
}
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$appDirectory = Split-Path -Parent $PSScriptRoot
foreach ($required in @('.python-version','pyproject.toml','uv.lock','scripts/runtime-pins.txt','scripts/bootstrap.py','src/layerloom/gui_app.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $appDirectory $required) -PathType Leaf)) {
        Write-Host 'The LayerLoom folder is incomplete. Right-click the ZIP and choose Extract All, then open the launcher inside the extracted folder. Keep its files together.'
        Write-Host ('Missing: ' + $required)
        exit 1
    }
}
$pins = @{}
$assets = @{}
Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-pins.txt') | ForEach-Object {
    $row = $_.Trim() -split '\s+'
    if ($row.Count -eq 2 -and $row[0] -ne '#') { $pins[$row[0]] = $row[1] }
    if ($row.Count -eq 3 -and $row[0] -eq 'asset') { $assets[$row[1]] = $row[2] }
}
$architecture = $env:PROCESSOR_ARCHITECTURE
if ($env:PROCESSOR_ARCHITEW6432) { $architecture = $env:PROCESSOR_ARCHITEW6432 }
if ($architecture -ne 'AMD64') { Write-Error 'This launcher currently supports Windows x64.'; exit 1 }
$target = 'x86_64-pc-windows-msvc'
$appHome = if ($env:LAYERLOOM_HOME) { $env:LAYERLOOM_HOME } else { Join-Path $env:LOCALAPPDATA 'LayerLoom' }
$toolDirectory = Join-Path $appHome ('tools\uv-' + $pins.uv + '-' + $target)
$uv = Join-Path $toolDirectory 'uv.exe'
$logDirectory = Join-Path $appHome 'logs'
$logPath = Join-Path $logDirectory ('bootstrap-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID + '.log')
$transcriptStarted = $false
try {
    try {
        foreach ($directory in @($appHome, $logDirectory)) {
            if ((Test-Path -LiteralPath $directory) -and -not (Test-Path -LiteralPath $directory -PathType Container)) {
                throw ('A file already occupies the required folder: ' + $directory)
            }
        }
        # Directory.CreateDirectory throws for file collisions. Do not use
        # New-Item -Force, which can mask provider errors on Windows PowerShell.
        [System.IO.Directory]::CreateDirectory($logDirectory) | Out-Null
        Start-Transcript -Path $logPath | Out-Null
        $transcriptStarted = $true
    } catch {
        throw ('Cannot write setup logs in ' + $logDirectory + '. Check folder permissions and free disk space. ' + $_.Exception.Message)
    }
    if (-not $pins.python -or -not $pins.uv -or $assets[$target].Length -ne 64) {
        throw 'The runtime manifest is incomplete. Download a fresh LayerLoom ZIP.'
    }
    Write-Host ('LayerLoom: preparing its private Python ' + $pins.python)
    $validUv = $false
    if (Test-Path -LiteralPath $uv) {
        try {
            $version = & $uv --version
            $validUv = ($LASTEXITCODE -eq 0 -and $version -match ('^uv ' + [regex]::Escape($pins.uv) + '(\s|$)'))
        } catch {
            # An interrupted/invalid tool installation is repaired by downloading
            # and verifying a fresh archive below.
            $validUv = $false
        }
    }
    if (-not $validUv) {
        if ($env:LAYERLOOM_OFFLINE -eq '1') { throw 'First setup needs an internet connection.' }
        $download = Join-Path $appHome ('tools\download-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $download | Out-Null
        $archive = Join-Path $download 'uv.zip'
        $url = 'https://github.com/astral-sh/uv/releases/download/' + $pins.uv + '/uv-' + $target + '.zip'
        Write-Host 'Downloading the setup tool...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $assets[$target]) {
            throw 'The setup download failed its integrity check. Reopen LayerLoom to retry.'
        }
        Expand-Archive -LiteralPath $archive -DestinationPath $download
        $binary = Get-ChildItem -LiteralPath $download -Filter uv.exe -Recurse | Select-Object -First 1
        if (-not $binary) { throw 'The setup archive does not contain uv.exe.' }
        New-Item -ItemType Directory -Force -Path $toolDirectory | Out-Null
        Copy-Item -LiteralPath $binary.FullName -Destination ($uv + '.new.' + $PID)
        try {
            Move-Item -LiteralPath ($uv + '.new.' + $PID) -Destination $uv -Force
        } catch {
            # Another simultaneous first launch may already be executing uv.exe,
            # which Windows will not replace. Reuse that complete, matching tool.
            $concurrentVersion = if (Test-Path -LiteralPath $uv) { & $uv --version } else { '' }
            if ($LASTEXITCODE -ne 0 -or $concurrentVersion -notmatch ('^uv ' + [regex]::Escape($pins.uv) + '(\s|$)')) { throw }
        }
    }
    # These changes apply only to this process and its children.
    Get-ChildItem Env: | Where-Object { $_.Name -like 'UV_*' -or $_.Name -in @('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','CONDA_PREFIX') } | ForEach-Object {
        Remove-Item -LiteralPath ('Env:' + $_.Name)
    }
    $env:UV_CACHE_DIR = Join-Path $appHome 'cache'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $appHome 'python'
    $env:UV_PYTHON_INSTALL_BIN = 'false'
    $env:UV_PYTHON_INSTALL_REGISTRY = 'false'
    $env:UV_PYTHON_NO_REGISTRY = 'true'
    if ($env:LAYERLOOM_OFFLINE -eq '1') { $env:UV_OFFLINE = 'true' }
    # Native stderr is expected when Python has not been installed yet.
    $ErrorActionPreference = 'Continue'
    $python = & $uv python find --system --managed-python --no-config --no-python-downloads $pins.python 2>$null
    $found = $LASTEXITCODE -eq 0
    $ErrorActionPreference = 'Stop'
    if (-not $found) {
        & $uv python install --no-config --no-bin --no-registry $pins.python
        if ($LASTEXITCODE -ne 0) { throw 'Private Python installation failed. Check your internet connection and retry.' }
        $ErrorActionPreference = 'Continue'
        $python = & $uv python find --system --managed-python --no-config --no-python-downloads $pins.python 2>$null
        $found = $LASTEXITCODE -eq 0
        $ErrorActionPreference = 'Stop'
        if (-not $found) {
            Write-Host 'Repairing a damaged private Python installation...'
            & $uv python install --no-config --no-bin --no-registry --reinstall $pins.python
            if ($LASTEXITCODE -ne 0) { throw 'Private Python repair failed. Check your internet connection and retry.' }
        }
        $python = & $uv python find --system --managed-python --no-config --no-python-downloads $pins.python
        if ($LASTEXITCODE -ne 0) { throw 'The installed Python could not start.' }
    }
    $arguments = @('-I', '-X', 'utf8', '-u', (Join-Path $PSScriptRoot 'bootstrap.py'), '--home', $appHome, '--uv', $uv, '--source', $appDirectory)
    if ($Repair) { $arguments += '--repair' }
    if ($DoctorOnly) { $arguments += '--doctor-only' }
    & $python @arguments
    $result = $LASTEXITCODE
} catch {
    Write-Host ('LayerLoom setup stopped: ' + $_.Exception.Message)
    Write-Host 'Reopen the launcher to retry.'
    if ($transcriptStarted) { Write-Host ('Log: ' + $logPath) }
    $result = 1
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
}
exit $result
