param(
    [string]$EnvironmentName = "ticket-agent",
    [int]$Port = 8000,
    [switch]$UseMimo
)

$ErrorActionPreference = "Stop"

# Always run from the repository root, even when launched from another folder.
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda was not found. Open Anaconda Prompt and run this script again."
}

$env:PYTHON_DOTENV_DISABLED = "1"
$env:APP_ENV = "dev"
$env:EMBEDDING_PROVIDER = "disabled"
if ($UseMimo) {
    $env:LLM_PROVIDER = "mimo"
}
else {
    $env:LLM_PROVIDER = "disabled"
    # A whitespace value overrides any key in .env, while the app treats it as disabled.
    $env:LLM_API_KEY = " "
}
$env:DATABASE_URL = "data/local-verify.db"
$env:SESSION_SECRET = "local-verification-secret-32-bytes-minimum"

$plainPassword = $env:ATLAS_LOCAL_ADMIN_PASSWORD
if (-not $plainPassword) {
    $securePassword = Read-Host "Set a local admin password" -AsSecureString
    $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    try {
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
}

if (-not $plainPassword) {
    throw "The local admin password cannot be empty."
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

Write-Host ""
Write-Host "Atlas Desk is starting..." -ForegroundColor Cyan
Write-Host "Public page: http://127.0.0.1:$Port"
Write-Host "Admin page:  http://127.0.0.1:$Port/admin"
Write-Host "LLM mode:    $(if ($UseMimo) { 'MiMo' } else { 'offline' })"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

& conda run --no-capture-output -n $EnvironmentName python -m uvicorn app.main:app `
    --host 127.0.0.1 --port $Port
