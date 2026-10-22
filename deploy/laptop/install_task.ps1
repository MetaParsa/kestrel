# Registers the "WallexAiBox" scheduled task for the current user:
# starts at logon and is re-launched every 5 minutes if it isn't running (restart-on-failure alone
# doesn't cover a process that died after starting); IgnoreNew keeps it to one copy; never times out.
# After sleep the process simply resumes; scp failures while offline are logged and retried.
#   powershell -ExecutionPolicy Bypass -File deploy\laptop\install_task.ps1
# Remove with: Unregister-ScheduledTask -TaskName WallexAiBox -Confirm:$false
$script = Join-Path $PSScriptRoot "run_ai_box.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = @(
    (New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "WallexAiBox" -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "wallex-agent laptop AI box (Claude manager loop)" -Force | Out-Null
"registered WallexAiBox"
