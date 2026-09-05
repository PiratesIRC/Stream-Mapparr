<#
Wrapper that Task Scheduler runs to refresh the README "streams matched" badge.

WHY A WRAPPER. A scheduled task starts with a minimal environment, so anything
resolved from PATH in an interactive shell may not resolve here. Both tools this
needs are pinned by absolute path below. Docker's directory is prepended to PATH
because update_streams_matched_badge.py calls `docker` by name.

WHAT IT DOES. Runs scripts/update_streams_matched_badge.py, appends a
timestamped record of the run to dist/badge-update.log, and exits with the
script's own exit code so that Task Scheduler's "Last Run Result" reflects a
failure instead of reporting success for a run that did nothing.

IT ONLY WORKS WHILE THE USER IS LOGGED ON. The task is registered to run in the
interactive user session on purpose. Docker Desktop runs in that session, and the
GitHub CLI reads its token from the user keyring, so a run with nobody logged on
would fail rather than silently publish a stale number.

THE NUMBER ONLY EVER RISES, so a missed run costs nothing but freshness. There
is no need to run this often; once or twice a day is plenty.
#>
$ErrorActionPreference = 'Stop'

$Python = 'C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe'
$DockerBin = 'C:\Program Files\Docker\Docker\resources\bin'
$Repo = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $PSScriptRoot 'update_streams_matched_badge.py'
$LogDir = Join-Path $Repo 'dist'
$Log = Join-Path $LogDir 'badge-update.log'
$MaxLogBytes = 1MB

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory $LogDir | Out-Null }

# Rotate before writing, keeping one previous file. An unbounded log on a task
# that runs daily is a slow disk leak nobody would notice.
if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt $MaxLogBytes)) {
    Move-Item $Log "$Log.1" -Force
}

$stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')
Add-Content -Path $Log -Encoding utf8 -Value "=== $stamp run start ==="

$env:PATH = "$DockerBin;$env:PATH"

# The invocation itself can fail before the script produces any output, for
# example when the pinned python path is wrong after an interpreter upgrade.
# Without this catch the wrapper dies with the reason on a console nobody is
# watching, leaving a log that ends mid-run and says nothing about why.
try {
    $output = & $Python $Script 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $output) { Add-Content -Path $Log -Encoding utf8 -Value $line }
} catch {
    Add-Content -Path $Log -Encoding utf8 -Value "wrapper FAILED before the script ran: $($_.Exception.Message)"
    $code = 1
}

Add-Content -Path $Log -Encoding utf8 -Value "=== exit $code ==="

exit $code
