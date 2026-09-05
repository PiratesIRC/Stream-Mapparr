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

# Built from the environment rather than written out in full: this repository is
# public, and a literal path names the Windows account it runs under.
$Python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
$DockerBin = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin'
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
# ErrorActionPreference is dropped to Continue for the invocation ONLY. In
# Windows PowerShell 5.1, redirecting a native executable's stderr with 2>&1
# wraps each line in a NativeCommandError record, and under 'Stop' that record is
# a TERMINATING error. Measured in this environment: a process that writes one
# line to stderr and exits 0 throws. So the first line Python wrote to stderr
# aborted the pipeline, the catch below logged "wrapper FAILED before the script
# ran" (which was untrue, it had already run), every line of real output was
# discarded, and Task Scheduler recorded a failure for a successful badge
# update. The script's own normal "nothing to publish yet" path prints to stderr,
# so this fired on an ordinary run rather than an exceptional one.
try {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = & $Python $Script 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    foreach ($line in $output) { Add-Content -Path $Log -Encoding utf8 -Value $line }
} catch {
    $ErrorActionPreference = 'Stop'
    Add-Content -Path $Log -Encoding utf8 -Value "wrapper FAILED: $($_.Exception.Message)"
    $code = 1
}

Add-Content -Path $Log -Encoding utf8 -Value "=== exit $code ==="

exit $code
