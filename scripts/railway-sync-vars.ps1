param(
    [string]$FilePath = ".env.railway",
    [string]$Service = "",
    [string]$Environment = "",
    [switch]$SkipDeploys,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Assert-RailwayCli {
    if (-not (Get-Command railway -ErrorAction SilentlyContinue)) {
        throw "Railway CLI is not installed. Install it first: npm i -g @railway/cli"
    }

    & railway whoami *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Railway CLI is not logged in. Run: railway login"
    }

    & railway status *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "This folder is not linked to a Railway project/service. Run: railway link"
    }
}

function Get-EnvPairs {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Env file not found: $Path"
    }

    $kv = @{}
    $lineNumber = 0

    Get-Content -LiteralPath $Path | ForEach-Object {
        $lineNumber += 1
        $line = $_.Trim()

        if ([string]::IsNullOrWhiteSpace($line)) { return }
        if ($line.StartsWith('#')) { return }

        if ($line.StartsWith('export ')) {
            $line = $line.Substring(7).Trim()
        }

        $idx = $line.IndexOf('=')
        if ($idx -lt 1) {
            Write-Warning "Skipping invalid line ${lineNumber}: $line"
            return
        }

        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()

        if ($key -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            Write-Warning "Skipping invalid key '${key}' on line ${lineNumber}."
            return
        }

        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            if ($value.Length -ge 2) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        # Keep the last value when duplicated.
        $kv[$key] = $value
    }

    $pairs = New-Object System.Collections.Generic.List[System.Collections.DictionaryEntry]
    foreach ($entry in $kv.GetEnumerator()) {
        [void]$pairs.Add($entry)
    }

    return $pairs
}

function Invoke-RailwaySetChunk {
    param(
        [System.Collections.Generic.List[System.Collections.DictionaryEntry]]$Chunk,
        [string]$ServiceName,
        [string]$EnvironmentName,
        [switch]$NoDeploy,
        [switch]$PreviewOnly
    )

    $args = @('variables')

    if (-not [string]::IsNullOrWhiteSpace($ServiceName)) {
        $args += @('--service', $ServiceName)
    }

    if (-not [string]::IsNullOrWhiteSpace($EnvironmentName)) {
        $args += @('--environment', $EnvironmentName)
    }

    if ($NoDeploy) {
        $args += '--skip-deploys'
    }

    foreach ($entry in $Chunk) {
        $pair = "{0}={1}" -f $entry.Key, $entry.Value
        $args += @('--set', $pair)
    }

    if ($PreviewOnly) {
        $keys = ($Chunk | ForEach-Object { $_.Key } | Sort-Object) -join ', '
        Write-Host "[DRY RUN] Would set variables: $keys"
        return
    }

    & railway @args
    if ($LASTEXITCODE -ne 0) {
        throw "railway variables command failed."
    }
}

Assert-RailwayCli
$pairs = Get-EnvPairs -Path $FilePath

if ($pairs.Count -eq 0) {
    throw "No variables found in $FilePath"
}

Write-Host "Found $($pairs.Count) variables in $FilePath"

# Chunk updates to avoid very long command lines.
$chunkSize = 20
for ($i = 0; $i -lt $pairs.Count; $i += $chunkSize) {
    $end = [Math]::Min($i + $chunkSize - 1, $pairs.Count - 1)
    $chunk = New-Object System.Collections.Generic.List[System.Collections.DictionaryEntry]
    for ($j = $i; $j -le $end; $j++) {
        [void]$chunk.Add($pairs[$j])
    }

    Invoke-RailwaySetChunk -Chunk $chunk -ServiceName $Service -EnvironmentName $Environment -NoDeploy:$SkipDeploys -PreviewOnly:$DryRun
}

if ($DryRun) {
    Write-Host "Dry run complete. No changes were sent to Railway."
    exit 0
}

Write-Host "Variable sync complete."
if ($SkipDeploys) {
    Write-Host "You used --SkipDeploys. Trigger deploy manually with: railway redeploy"
} else {
    Write-Host "Railway should trigger a deploy automatically."
}
