# Installs MeetingWorker for the current user.
#   .\install.ps1 -Server https://nas.example:9443 -Key <worker key> -PyannoteDir C:\path\to\pyannote-c1
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$Key,
    [Parameter(Mandatory = $true)][string]$PyannoteDir
)
$ErrorActionPreference = 'Stop'

# %USERPROFILE%\MeetingWorker (not %LOCALAPPDATA%: MSIX-packaged shells virtualize it); worker.py uses Path.home()
$base = Join-Path $env:USERPROFILE 'MeetingWorker'
$venv = Join-Path $base 'venv'
$app = Join-Path $base 'app'
$models = Join-Path $base 'models'
$src = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path (Join-Path $PyannoteDir 'config.yaml'))) {
    throw "PyannoteDir '$PyannoteDir' does not look like a pyannote pipeline folder (config.yaml missing)."
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw 'ffmpeg not found on PATH.' }

New-Item -ItemType Directory -Force $base, $app, $models | Out-Null

# re-install: stop the running worker first (IgnoreNew would otherwise keep the old process/code)
$task = Get-ScheduledTask -TaskName 'MeetingWorker' -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName 'MeetingWorker' -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30 -and (Get-ScheduledTask -TaskName 'MeetingWorker').State -eq 'Running'; $i++) {
        Start-Sleep -Seconds 1
    }
}

# 1. venv (python 3.12)
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    & py -3.12 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'python 3.12 venv creation failed (is Python 3.12 installed?)' }
}
$py = Join-Path $venv 'Scripts\python.exe'
& $py -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed' }
& $py -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'torch install failed' }
& $py -m pip install faster-whisper==1.2.1 opencc-python-reimplemented requests pyannote.audio==4.0.7 nvidia-cublas-cu12 nvidia-cudnn-cu12
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

# 2. app files + model folder
Copy-Item (Join-Path $src '*.py') $app -Force
Copy-Item (Join-Path $src 'requirements.txt') $app -Force
$dest = Join-Path $models 'pyannote-c1'
$srcFull = [System.IO.Path]::GetFullPath((Resolve-Path $PyannoteDir).Path).TrimEnd('\')
if ($srcFull -ne [System.IO.Path]::GetFullPath($dest).TrimEnd('\')) {
    # copy the folder CONTENTS: Copy-Item <dir> <existing dir> would nest pyannote-c1\pyannote-c1
    New-Item -ItemType Directory -Force $dest | Out-Null
    Copy-Item (Join-Path $srcFull '*') $dest -Recurse -Force
}

# 3. config
$cfg = [ordered]@{ server = $Server.TrimEnd('/'); key = $Key; poll = 10 } | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $base 'config.json'), $cfg, (New-Object System.Text.UTF8Encoding($false)))

# 4. scheduled task: at logon, hidden (pythonw), restart on failure
$pyw = Join-Path $venv 'Scripts\pythonw.exe'
$action = New-ScheduledTaskAction -Execute $pyw -Argument 'worker.py' -WorkingDirectory $app
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'MeetingWorker' -Action $action -Trigger $trigger -Settings $settings `
    -Description 'DD meeting GPU transcription worker' -Force | Out-Null
Start-ScheduledTask -TaskName 'MeetingWorker'
Write-Host "Installed. Log: $base\worker.log"
