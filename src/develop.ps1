<#
.SYNOPSIS
Spins up the directory service, catalogador agent, valorador agent, etc.
Equivalent to the bash script, adapted for Windows PowerShell.
#>

$ErrorActionPreference = "Stop"

$DIR_HOST = "127.0.0.1"
$DIR_PORT = "9000"
$DIR_URL = "http://${DIR_HOST}:${DIR_PORT}"
$HOSTADDR = "127.0.0.1"

# Get current script directory
$SCRIPT_DIR = $PSScriptRoot
$CONFIG_FILE = Join-Path $SCRIPT_DIR "external_agents_config.json"

# Read from environment variables with fallbacks
$FEEDBACK_DELAY_SECONDS = if ($env:ECSDI_FEEDBACK_DELAY_SECONDS) { $env:ECSDI_FEEDBACK_DELAY_SECONDS } else { "30" }
$DELIVERY_DELAY_SECONDS = if ($env:ECSDI_DELIVERY_DELAY_SECONDS) { $env:ECSDI_DELIVERY_DELAY_SECONDS } else { "0" }

# Prefer local env python (Windows uses \Scripts\ instead of /bin/), then system python
$PYTHON = ""
if (Test-Path (Join-Path $SCRIPT_DIR "env\Scripts\python.exe")) {
    $PYTHON = Join-Path $SCRIPT_DIR "env\Scripts\python.exe"
} elseif (Test-Path (Join-Path $SCRIPT_DIR ".venv\Scripts\python.exe")) {
    $PYTHON = Join-Path $SCRIPT_DIR ".venv\Scripts\python.exe"
} else {
    $PYTHON = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if ([string]::IsNullOrEmpty($PYTHON)) {
        $PYTHON = (Get-Command python3.exe -ErrorAction SilentlyContinue).Source
    }
}

if ([string]::IsNullOrEmpty($PYTHON)) {
    Write-Host "Python interpreter not found." -ForegroundColor Red
    exit 1
}

Write-Host "Using Python: $PYTHON" -ForegroundColor Cyan

# Array to keep track of running processes
$global:Processes = @()

function Start-Agent {
    param (
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments
    )

    Write-Host "Starting $Name..."
    
    # Start the process in the background without opening a new window
    $proc = Start-Process -FilePath $Executable -ArgumentList $Arguments -PassThru -NoNewWindow
    
    $global:Processes += [PSCustomObject]@{ Name = $Name; Process = $proc }
    Write-Host "  -> $Name PID: $($proc.Id)"
}

function Shutdown-All {
    Write-Host "`nShutting down agents..." -ForegroundColor Yellow

    foreach ($item in $global:Processes) {
        $p = $item.Process
        if (-not $p.HasExited) {
            Write-Host "Stopping $($item.Name) (PID $($p.Id))"
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host "All agents stopped." -ForegroundColor Green
}

# The try/finally block ensures that even if you press Ctrl+C, the finally block runs and kills the agents.
try {
    # Commented out agents kept for parity with your bash script
    Start-Agent "DirectoryService" $PYTHON @("DirectoryService.py", "--port", "9000", "--open", "--hostaddr", $HOSTADDR)
    Start-Agent "Logger" $PYTHON @("Logger.py", "--port", "9100", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR)
    Start-Agent "Client" $PYTHON @("Client.py", "--port", "9010", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR)
    
    Start-Agent "Catalogador" $PYTHON @("Catalogador.py", "--port", "9040", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR)
    Start-Agent "Valorador" $PYTHON @("Valorador.py", "--port", "9050", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--feedback-delay-seconds", $FEEDBACK_DELAY_SECONDS)
    Start-Agent "EntidadBancaria" $PYTHON @("EntidadBancaria.py", "--port", "9080", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--config", $CONFIG_FILE)
    
    Start-Agent "EmpresaVendedora HomePlus" $PYTHON @("EmpresaVendedora.py", "--port", "9090", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--config", $CONFIG_FILE, "--profile", "homeplus")
    Start-Agent "EmpresaVendedora BagStore" $PYTHON @("EmpresaVendedora.py", "--port", "9091", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--config", $CONFIG_FILE, "--profile", "bagstore")
    Start-Agent "EmpresaVendedora BookPlanet" $PYTHON @("EmpresaVendedora.py", "--port", "9092", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--config", $CONFIG_FILE, "--profile", "bookplanet")
    Start-Agent "EmpresaVendedora TechHub" $PYTHON @("EmpresaVendedora.py", "--port", "9093", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--config", $CONFIG_FILE, "--profile", "techhub")
    
    Start-Agent "Transportista RapidShip" $PYTHON @("Transportista.py", "--port", "9071", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--config", $CONFIG_FILE, "--profile", "rapidship")
    Start-Agent "Transportista CheapMove" $PYTHON @("Transportista.py", "--port", "9072", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--config", $CONFIG_FILE, "--profile", "cheapmove")
    Start-Agent "Transportista PremiumLog" $PYTHON @("Transportista.py", "--port", "9073", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--config", $CONFIG_FILE, "--profile", "premiumlog")
    Start-Agent "Tesorero" $PYTHON @("Tesorero.py", "--port", "9060", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR)
    Start-Agent "Ventas" $PYTHON @("Ventas.py", "--port", "9020", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR)
    Start-Agent "CentroLogistico0" $PYTHON @("CentroLogistico.py", "--port", "9030", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--delivery-delay-seconds", $DELIVERY_DELAY_SECONDS)
    Start-Agent "CentroLogistico1" $PYTHON @("CentroLogistico.py", "--port", "9031", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--delivery-delay-seconds", $DELIVERY_DELAY_SECONDS)
    Start-Agent "CentroLogistico2" $PYTHON @("CentroLogistico.py", "--port", "9032", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--delivery-delay-seconds", $DELIVERY_DELAY_SECONDS)
    Start-Agent "CentroLogistico3" $PYTHON @("CentroLogistico.py", "--port", "9033", "--dir", $DIR_URL, "--open", "--hostaddr", $HOSTADDR, "--delivery-delay-seconds", $DELIVERY_DELAY_SECONDS)

    Write-Host "`nAgents are running." -ForegroundColor Cyan
    Write-Host "Press any key to stop all agents..." -NoNewline
    
    # Wait for keypress silently
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    Write-Host ""
}
finally {
    # This triggers whether you press a key, hit Ctrl+C, or if the script crashes
    Shutdown-All
}