# setup_vb_cable.ps1
# Sets VB-Audio Cable as the default playback device (so call/system audio is
# routed into the virtual cable) and your USB headset mic as default recording.
# Then prints the detected device names. Run from PowerShell:
#   powershell -ExecutionPolicy Bypass -File setup_vb_cable.ps1

$ErrorActionPreference = "Stop"

function Ensure-Module {
    if (-not (Get-Module -ListAvailable -Name AudioDeviceCmdlets)) {
        Write-Host "[*] Installing AudioDeviceCmdlets module..."
        Install-Module -Name AudioDeviceCmdlets -Force -AllowClobber -Scope CurrentUser `
            -Repository PSGallery -Confirm:$false
    }
    Import-Module AudioDeviceCmdlets -Force
}

try {
    Ensure-Module
} catch {
    Write-Host "[!] Could not load AudioDeviceCmdlets: $_"
    Write-Host "[!] Tip: run 'Install-Module -Name AudioDeviceCmdlets -Force' manually, or set defaults in Sound settings."
    exit 1
}

Write-Host "`n=== Playback devices ==="
Get-AudioDevice -List | Where-Object { $_.Type -eq "Playback" } | ForEach-Object { "  [$($_.Index)] $($_.Name)  (Default: $($_.Default))" }

Write-Host "`n=== Recording devices ==="
Get-AudioDevice -List | Where-Object { $_.Type -eq "Recording" } | ForEach-Object { "  [$($_.Index)] $($_.Name)  (Default: $($_.Default))" }

# Set CABLE Input as default playback
$cableIn = Get-AudioDevice -List | Where-Object { $_.Type -eq "Playback" -and $_.Name -like "*CABLE Input*" } | Select-Object -First 1
if ($cableIn) {
    Write-Host "`n>> Setting default PLAYBACK -> $($cableIn.Name)"
    $cableIn | Set-AudioDevice -Default
} else {
    Write-Host "`n[!] 'CABLE Input' not found among playback devices."
}

# Set USB ENC mic as default recording
$mic = Get-AudioDevice -List | Where-Object { $_.Type -eq "Recording" -and $_.Name -like "*USB ENC*" -and $_.Name -like "*Microphone*" } | Select-Object -First 1
if (-not $mic) {
    $mic = Get-AudioDevice -List | Where-Object { $_.Type -eq "Recording" -and $_.Name -like "*Microphone*" } | Select-Object -First 1
}
if ($mic) {
    Write-Host ">> Setting default RECORDING -> $($mic.Name)"
    $mic | Set-AudioDevice -Default
}

Write-Host "`n=== New defaults ==="
Get-AudioDevice -Playback | ForEach-Object { "  Playback default : $($_.Name)" }
Get-AudioDevice -Recording | ForEach-Object { "  Recording default: $($_.Name)" }

Write-Host "`n[OK] Done. In main.py your devices are: MIC=<USB ENC mic idx>, SYS=<CABLE Output idx>."
Write-Host "[NOTE] To still HEAR the call, enable 'Listen to this device' on CABLE Input"
Write-Host "       (Sound > Recording > CABLE Input > Properties > Listen > Play through USB headset)."
