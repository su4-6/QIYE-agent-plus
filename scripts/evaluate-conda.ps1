param(
    [string]$EnvironmentName = "ticket-agent",
    [switch]$LiveMimo,
    [switch]$Performance
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$env:PYTHONUTF8 = "1"
$env:PYTHON_DOTENV_DISABLED = "1"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$arguments = @("run", "--no-capture-output", "-n", $EnvironmentName, "python", "-m", "evaluation.run", "--publish-policy", "--output", "evaluation/results/$stamp")
if ($LiveMimo) { $arguments += "--live-mimo" }
if ($Performance) { $arguments += "--performance" }
& conda @arguments
exit $LASTEXITCODE
