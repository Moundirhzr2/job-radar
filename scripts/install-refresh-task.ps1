# Installs the nightly refresh (scripts\refresh.ps1) in the Windows Task Scheduler: every day at
# 4:00, or at the next start when the PC was off. Remove it with:
#   Unregister-ScheduledTask -TaskName "Job Radar refresh" -Confirm:$false
$script = Join-Path $PSScriptRoot "refresh.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At 4:00
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "Job Radar refresh" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Met a jour les offres de Job Radar et les indexe." -Force | Out-Null
"Tache installee. Prochain passage : $((Get-ScheduledTaskInfo -TaskName 'Job Radar refresh').NextRunTime)"
