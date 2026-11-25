[CmdletBinding(DefaultParameterSetName = 'Hardware')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Hardware')]
    [ValidatePattern('^COM[1-9][0-9]*$')]
    [string]$SerialPort,

    [Parameter(Mandatory, ParameterSetName = 'Collect')]
    [switch]$CollectOnly,

    [string]$Testbed,
    [string]$Filter
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$python = Join-Path $root '.venv-zephyr\Scripts\python.exe'
$build = Join-Path $root 'zephyr_wifi_examples\build'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python environment not found: $python"
}
if (-not $Testbed) {
    $Testbed = Join-Path $PSScriptRoot 'testbed.mercusys.local.yaml'
}
$testbedPath = (Resolve-Path -LiteralPath $Testbed).Path
$oldPath = $env:PATH
$oldZephyrBase = $env:ZEPHYR_BASE
$oldPythonPath = $env:PYTHONPATH
$env:PATH = "$(Split-Path -Parent $python);$oldPath"
$env:ZEPHYR_BASE = Join-Path $root 'zephyr'
$env:PYTHONPATH = "$env:ZEPHYR_BASE\scripts\pylib\pytest-twister-harness\src;$env:ZEPHYR_BASE\scripts\pylib\twister"
if ($oldPythonPath) {
    $env:PYTHONPATH += ";$oldPythonPath"
}

$overrides = @('PNET_SSID', 'PNET_PSK', 'PNET_BSSID', 'PNET_SECONDARY_PSK') |
    Where-Object { [Environment]::GetEnvironmentVariable($_, 'Process') }
if ($overrides) {
    Write-Warning ("These environment variables override the YAML: " + ($overrides -join ', '))
}

# Load the workspace plugin even when pytest autoload is off; block its
# package entry point so pytest cannot register the same plugin twice.
$pytestArgs = @('-m', 'pytest', '-p', 'no:twister_harness', '-p', 'twister_harness.plugin',
                '-v', "--testbed=$testbedPath", 'tests\test_02_wifi.py')
if ($Filter) {
    $pytestArgs += @('-k', $Filter)
}
if ($CollectOnly) {
    $pytestArgs += @('--collect-only', '-p', 'no:cacheprovider', '-o', 'log_file=')
} else {
    $pytestArgs += @('--twister-harness', '--device-type=hardware', "--device-serial=$SerialPort",
                     '--platform=nrf54lm20dk/nrf54lm20a/cpuapp', "--build-dir=$build")
}

Push-Location -LiteralPath $PSScriptRoot
try {
    & $python @pytestArgs
    $result = $LASTEXITCODE
} finally {
    Pop-Location
    $env:PATH = $oldPath
    $env:ZEPHYR_BASE = $oldZephyrBase
    $env:PYTHONPATH = $oldPythonPath
}
exit $result
