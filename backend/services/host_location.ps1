# Prints this computer's position from the Windows Location API as one JSON line.
# GeoCoordinateWatcher (System.Device) is the managed wrapper over the Windows Location API. Waits up to
# 15 s for a fix; reports permission/status so a disabled location switch gives a precise reason.
# Run by backend/services/host_location.py; the Windows location privacy switch applies.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Device
$watcher = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::High)
try {
  $started = $watcher.TryStart($true, [TimeSpan]::FromSeconds(10))
  $deadline = (Get-Date).AddSeconds(15)
  while ((Get-Date) -lt $deadline -and ($watcher.Status -ne 'Ready' -or $watcher.Position.Location.IsUnknown)) {
    if ($watcher.Permission -eq 'Denied' -or $watcher.Status -eq 'Disabled') { break }
    Start-Sleep -Milliseconds 250
  }
  $location = $watcher.Position.Location
  $known = -not $location.IsUnknown
  [pscustomobject]@{
    started    = [bool]$started
    status     = [string]$watcher.Status
    permission = [string]$watcher.Permission
    known      = [bool]$known
    lat        = $(if ($known) { $location.Latitude } else { $null })
    lng        = $(if ($known) { $location.Longitude } else { $null })
    accuracy   = $(if ($known -and -not [double]::IsNaN($location.HorizontalAccuracy)) { $location.HorizontalAccuracy } else { $null })
  } | ConvertTo-Json -Compress
} finally {
  $watcher.Stop()
  $watcher.Dispose()
}
