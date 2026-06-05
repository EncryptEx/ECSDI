<#
.SYNOPSIS
Accelerates ECSDI demo timers.
.DESCRIPTION
Equivalent to the bash script, adapted for Windows PowerShell.
Accepts the host address as an optional parameter (defaults to 127.0.0.1).
#>

param (
    # This replaces $1 in bash. It defaults to 127.0.0.1
    [string]$TargetHost = "127.0.0.1"
)

# Read environment variables, assigning defaults if they don't exist
$WaitForFeedbackSeconds = if ($env:WAIT_FOR_FEEDBACK_SECONDS) { [int]$env:WAIT_FOR_FEEDBACK_SECONDS } else { 0 }
$RegisterExternalProducts = if ($env:REGISTER_EXTERNAL_PRODUCTS) { $env:REGISTER_EXTERNAL_PRODUCTS } else { "0" }

Write-Host "Accelerating ECSDI demo timers on $TargetHost" -ForegroundColor Cyan

if ($RegisterExternalProducts -eq "1") {
    foreach ($port in 9090, 9091, 9092, 9093) {
        Write-Host "`nEmpresaVendedora ${port}: Recepcion nuevo producto"
        # -ErrorAction SilentlyContinue mimics curl -s (silent), hiding errors if the agent isn't running
        Invoke-RestMethod -Uri "http://${TargetHost}:${port}/tick/nuevo-producto" -ErrorAction SilentlyContinue
        Write-Host ""
    }
}

foreach ($port in 9030, 9031, 9032, 9033) {
    Write-Host "`nCentroLogistico ${port}: Enviar lotes timer up"
    Invoke-RestMethod -Uri "http://${TargetHost}:${port}/tick/envios" -ErrorAction SilentlyContinue
    Write-Host ""
}

if ($WaitForFeedbackSeconds -gt 0) {
    Write-Host "`nWaiting ${WaitForFeedbackSeconds}s so delivered purchases become feedback candidates" -ForegroundColor Yellow
    Start-Sleep -Seconds $WaitForFeedbackSeconds
}

Write-Host "`nValorador 9050: Feedback timer up"
Invoke-RestMethod -Uri "http://${TargetHost}:9050/tick/feedback" -ErrorAction SilentlyContinue
Write-Host ""

Write-Host "`nValorador 9050: Recomendacion timer up"
Invoke-RestMethod -Uri "http://${TargetHost}:9050/tick/recomendaciones" -ErrorAction SilentlyContinue
Write-Host ""