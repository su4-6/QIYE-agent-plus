param(
    [string]$EnvironmentName = "ticket-agent",
    [int]$Port = 8000,
    [switch]$UseMimo,
    [switch]$UseVectors,
    [switch]$ImportSimulatedKnowledge
)

$ErrorActionPreference = "Stop"

# Always run from the repository root, even when launched from another folder.
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

function Test-LocalPortAvailable([int]$CandidatePort) {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $CandidatePort)
    try {
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        $listener.Stop()
    }
}

if (-not (Test-LocalPortAvailable $Port)) {
    Write-Host "Port $Port is already in use, so this launch was stopped." -ForegroundColor Red
    Write-Host "Close the old Atlas Desk window with Ctrl+C, or choose another unused port, for example -Port 8010."
    Write-Host "The password only belongs to the service address printed by that launch."
    exit 2
}

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda was not found. Open Anaconda Prompt and run this script again."
}

$env:PYTHON_DOTENV_DISABLED = "1"
$env:APP_ENV = "dev"
$env:EMBEDDING_PROVIDER = $(if ($UseVectors) { "local" } else { "disabled" })
$env:PYTHONUTF8 = "1"
if ($UseMimo) {
    if (-not $env:MIMO_API_KEY -or -not $env:MIMO_API_KEY.Trim()) {
        throw "-UseMimo requires MIMO_API_KEY in the current process environment. The project .env is not loaded."
    }
    $env:LLM_PROVIDER = "mimo"
    $env:LOW_RISK_ASSISTANCE = "true"
}
else {
    $env:LLM_PROVIDER = "disabled"
    $env:LOW_RISK_ASSISTANCE = "false"
    # A whitespace value overrides any key in .env, while the app treats it as disabled.
    $env:LLM_API_KEY = " "
}
$env:DATABASE_URL = "data/local-verify.db"
$env:ADMIN_USERNAME = "admin"
$env:SESSION_SECRET = "local-verification-secret-32-bytes-minimum"

$plainPassword = $env:ATLAS_LOCAL_ADMIN_PASSWORD
while (-not $plainPassword -or $plainPassword.Length -lt 8) {
    if ($plainPassword) {
        Write-Host "The local admin password must contain at least 8 characters." -ForegroundColor Yellow
    }
    $securePassword = Read-Host "Set a local admin password (at least 8 characters)" -AsSecureString
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    try {
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
}

$env:ATLAS_PASSWORD_FOR_HASH = $plainPassword
try {
    $hashLines = & conda run -n $EnvironmentName python -c `
        "import os; from app.security import password_hash; print(password_hash(os.environ['ATLAS_PASSWORD_FOR_HASH']))"
    if ($LASTEXITCODE -ne 0) {
        throw "Password setup failed. Check the $EnvironmentName Conda environment."
    }
    $env:ADMIN_PASSWORD_HASH = ([string]::Join("", $hashLines)).Trim()
}
finally {
    Remove-Item Env:ATLAS_PASSWORD_FOR_HASH -ErrorAction SilentlyContinue
    Remove-Variable plainPassword -ErrorAction SilentlyContinue
}

if ($ImportSimulatedKnowledge) {
    Write-Host "Importing the explicitly requested simulated SOP corpus..."
    $importArguments = @("run", "--no-capture-output", "-n", $EnvironmentName, "python", "scripts/import-demo.py")
    if (-not $UseVectors) { $importArguments += "--without-vectors" }
    & conda @importArguments
    if ($LASTEXITCODE -ne 0) { throw "Simulated knowledge import failed." }
    if ($UseVectors) {
        & conda run --no-capture-output -n $EnvironmentName python -m app.knowledge reindex
        if ($LASTEXITCODE -ne 0) { throw "Vector reindex failed." }
    }
}

Write-Host ""
Write-Host "Atlas Desk is starting..." -ForegroundColor Cyan
Write-Host "Public page: http://127.0.0.1:$Port"
Write-Host "Admin page:  http://127.0.0.1:$Port/admin"
Write-Host "LLM mode:    $(if ($UseMimo) { 'MiMo' } else { 'offline' })"
Write-Host "Vector mode: $(if ($UseVectors) { 'local BGE' } else { 'disabled' })"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

& conda run --no-capture-output -n $EnvironmentName python -m uvicorn app.main:app `
    --host 127.0.0.1 --port $Port
