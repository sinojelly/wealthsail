#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Start', 'Stop', 'Status', 'RefreshMacro', 'RefreshResearch')][string]$Action = 'Start',
    [Guid]$ResearchRequestKey,
    [string]$PostgresBin,
    [ValidateRange(1024, 65535)][int]$DbPort = 55432,
    [ValidateRange(1024, 65535)][int]$ApiPort = 8000,
    [ValidateRange(1024, 65535)][int]$WebPort = 5173,
    [switch]$SkipInstall,
    [switch]$NoBrowser,
    [switch]$SkipMacro
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# No activation or global execution-policy change is needed. Use Python 3.12.
$candidates = @(
    @{ Command = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'); Args = @() },
    @{ Command = (Join-Path $PSScriptRoot '.venv-local\Scripts\python.exe'); Args = @() },
    @{ Command = 'py.exe'; Args = @('-3.12') },
    @{ Command = 'python.exe'; Args = @() }
)
$selected = $null
foreach ($candidate in $candidates) {
    if (-not (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) { continue }
    $version = & $candidate.Command @($candidate.Args) -c 'import sys; print(sys.version_info.major,sys.version_info.minor,sep=chr(46))' 2>$null
    if ($LASTEXITCODE -eq 0 -and $version -eq '3.12') {
        $selected = $candidate
        break
    }
}
if ($null -eq $selected) {
    Write-Error 'Install Python 3.12 (with the Python launcher / PATH), then rerun. See docs/26.Windows-local-development.md.'
    exit 1
}

$launcherArgs = @(
    (Join-Path $PSScriptRoot 'scripts\local_dev.py'), '--action', $Action.ToLower(), '--db-port', "$DbPort",
    '--api-port', "$ApiPort", '--web-port', "$WebPort"
)
if ($SkipInstall) { $launcherArgs += '--skip-install' }
if ($NoBrowser) { $launcherArgs += '--no-browser' }
if ($SkipMacro) { $launcherArgs += '--skip-macro' }
if ($PSBoundParameters.ContainsKey('ResearchRequestKey')) { $launcherArgs += @('--research-request-key', "$ResearchRequestKey") }
if ($PostgresBin) { $launcherArgs += @('--postgres-bin', $PostgresBin) }
& $selected.Command @($selected.Args) @launcherArgs
exit $LASTEXITCODE
