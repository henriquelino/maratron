# Builds the Maratron SteamVR driver into maratron/bin/win64/driver_maratron.dll
# Requires: Visual Studio 2022 (or Build Tools) with the "Desktop development with C++"
# workload, which provides MSVC (cl.exe), CMake, and Ninja.
#
# Usage (from a normal PowerShell):
#   .\build.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# Locate a VS install and its bundled CMake/Ninja via vcvars.
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "vswhere not found; install Visual Studio 2022 with the C++ workload." }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "No VS install with the C++ toolset. Install 'Desktop development with C++'." }

$cmake = Join-Path $vs "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if (-not (Test-Path $cmake)) { $cmake = "cmake" }  # fall back to PATH

Write-Host "Using VS at: $vs"
Write-Host "Using cmake: $cmake"

# Configure + build in a VS dev environment so MSVC is on PATH.
$devShell = Join-Path $vs "Common7\Tools\Launch-VsDevShell.ps1"
if (Test-Path $devShell) { & $devShell -Arch amd64 -HostArch amd64 | Out-Null }

& $cmake -S $root -B "$root\build" -G "Ninja" -DCMAKE_BUILD_TYPE=Release
& $cmake --build "$root\build" --config Release

$dll = Join-Path $root "maratron\bin\win64\driver_maratron.dll"
if (Test-Path $dll) { Write-Host "`nBuilt: $dll" } else { throw "Build did not produce $dll" }
