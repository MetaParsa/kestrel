# Laptop AI box, started by Task Scheduler at logon (see install_task.ps1).
# Runs the pull → Claude → push loop against the server with the $15 spend cap.
# The Anthropic key comes from the user-level ANTHROPIC_API_KEY variable; it is never written anywhere.
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
$logDir = Join-Path $repo "run\ai_box"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "loop.log"
if (-not $env:ANTHROPIC_API_KEY) {
    $env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
}
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" }
"$(Get-Date -Format s) starting ai_box loop (paid, cap 15 USD)" | Out-File -Append -Encoding utf8 $log
& $uv run python -u -m ai_box.loop --host wallex-iran --remote-dir /opt/wallex-agent/run/main --paid --cap 15 --gap-min 10 2>&1 |
    ForEach-Object { "$(Get-Date -Format s) $_" } | Out-File -Append -Encoding utf8 $log
"$(Get-Date -Format s) ai_box loop exited with code $LASTEXITCODE" | Out-File -Append -Encoding utf8 $log
exit 1   # make Task Scheduler restart it
