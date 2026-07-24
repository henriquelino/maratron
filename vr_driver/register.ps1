# Registers (or unregisters) the Maratron SteamVR driver with SteamVR.
#
#   .\register.ps1            # add this driver folder to SteamVR
#   .\register.ps1 -Remove    # remove it
#
# No admin / signing needed — SteamVR drivers are user-mode plugins.

param([switch]$Remove)

$ErrorActionPreference = "Stop"
$driverDir = Join-Path $PSScriptRoot "maratron"

# Find vrpathreg.exe (SteamVR install can vary).
$candidates = @(
    "${env:ProgramFiles(x86)}\Steam\steamapps\common\SteamVR\bin\win64\vrpathreg.exe",
    "${env:ProgramFiles}\Steam\steamapps\common\SteamVR\bin\win64\vrpathreg.exe"
)
$vrpathreg = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $vrpathreg) { throw "vrpathreg.exe not found. Is SteamVR installed?" }

if ($Remove) {
    & $vrpathreg removedriver $driverDir
    Write-Host "Removed driver: $driverDir"
} else {
    if (-not (Test-Path (Join-Path $driverDir "bin\win64\driver_maratron.dll"))) {
        throw "driver_maratron.dll not built yet. Run .\build.ps1 first."
    }
    & $vrpathreg adddriver $driverDir
    Write-Host "Registered driver: $driverDir"
    Write-Host "Restart SteamVR, then set Maratron output to 'VR' in the dashboard Config tab."
}
& $vrpathreg show
