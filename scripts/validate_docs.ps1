[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$docsRoot = Join-Path $repoRoot 'docs'
$failures = [System.Collections.Generic.List[string]]::new()

function Add-Failure {
    param([Parameter(Mandatory)][string]$Message)
    $failures.Add($Message)
}

# Keep this script ASCII-only so Windows PowerShell 5 can parse it without a BOM.
$requiredNumbers = @('01', '02', '03', '04', '05', '06', '07', '08')
$requiredDocs = @{}

foreach ($number in $requiredNumbers) {
    $matches = @(Get-ChildItem -LiteralPath $docsRoot -Filter "$number.*.md" -File)
    if ($matches.Count -ne 1) {
        Add-Failure "Document prefix $number must resolve to exactly one Markdown file; found $($matches.Count)."
        continue
    }
    $requiredDocs[$number] = $matches[0].FullName
}

$documentFiles = @(Get-ChildItem -LiteralPath $docsRoot -Filter '*.md' -File)
$forbiddenPatterns = [ordered]@{
    'unsupported PASS table status' = '\|\s*PASS\s*\|'
    'unsafe shell interpolation example' = 'shell=True'
    'unsupported 100 percent completion claim' = '100%\s*\S{0,4}'
    'hard-coded Brent production value' = 'brent_price\s*='
    'hard-coded WTI production value' = 'wti\s*='
    'hard-coded gold production value' = 'gold_spot\s*='
}

foreach ($file in $documentFiles) {
    $content = Get-Content -Raw -LiteralPath $file.FullName -Encoding UTF8
    foreach ($pattern in $forbiddenPatterns.GetEnumerator()) {
        if ($content -match $pattern.Value) {
            Add-Failure "$($file.Name): $($pattern.Key)."
        }
    }
}

if ($requiredDocs.ContainsKey('01') -and $requiredDocs.ContainsKey('08')) {
    $prdLines = Get-Content -LiteralPath $requiredDocs['01'] -Encoding UTF8
    $traceContent = Get-Content -Raw -LiteralPath $requiredDocs['08'] -Encoding UTF8
    $definitionIds = [System.Collections.Generic.List[string]]::new()
    $mustIds = [System.Collections.Generic.List[string]]::new()

    foreach ($line in $prdLines) {
        # Only the first cell in PRD definition tables counts as a definition.
        if ($line -match '^\|\s*((?:FR|DATA|SEC|NFR|SC|AI)-[A-Z0-9-]+)\s*\|') {
            $id = $Matches[1]
            $definitionIds.Add($id)
            if ($line -match '^\|\s*[^|]+\|\s*L[01]\s*\|\s*Must\s*\|') {
                $mustIds.Add($id)
            }
        }
    }

    $duplicates = @($definitionIds | Group-Object | Where-Object Count -gt 1)
    foreach ($duplicate in $duplicates) {
        Add-Failure "Duplicate PRD requirement definition: $($duplicate.Name) ($($duplicate.Count) occurrences)."
    }

    foreach ($id in ($mustIds | Sort-Object -Unique)) {
        if ($traceContent -notmatch [regex]::Escape($id)) {
            Add-Failure "L0/L1 Must requirement missing from traceability: $id."
        }
    }

    Write-Output "Requirement definitions: $($definitionIds.Count)"
    Write-Output "L0/L1 Must requirements: $(($mustIds | Sort-Object -Unique).Count)"
}

$localLinkCount = 0
foreach ($file in $documentFiles) {
    $content = Get-Content -Raw -LiteralPath $file.FullName -Encoding UTF8
    $matches = [regex]::Matches($content, '\]\((?!https?://|mailto:|#)([^)#]+)(?:#[^)]*)?\)')
    foreach ($match in $matches) {
        $rawTarget = $match.Groups[1].Value.Trim('<', '>')
        if ($rawTarget -match '^[a-zA-Z]+:') {
            continue
        }

        $localLinkCount++
        $decodedTarget = [System.Uri]::UnescapeDataString($rawTarget)
        $candidate = Join-Path $file.DirectoryName $decodedTarget
        if (-not (Test-Path -LiteralPath $candidate)) {
            Add-Failure "$($file.Name): missing local link target $rawTarget."
        }
    }
}

$canonicalTokens = @(
    'FR-POR-001',
    'FR-RPT-001',
    'FR-RPT-002',
    'FR-RPT-003',
    'FR-RPT-004',
    'FR-WEC-001',
    'SEC-TEN-001',
    'L0',
    'L4',
    'tenant_id',
    'instrument_id',
    'ledger_events',
    'source_registry',
    'expand'
)

if ($requiredDocs.ContainsKey('01') -and $requiredDocs.ContainsKey('02') -and $requiredDocs.ContainsKey('06')) {
    $canonicalContent = @(
        Get-Content -Raw -LiteralPath $requiredDocs['01'] -Encoding UTF8
        Get-Content -Raw -LiteralPath $requiredDocs['02'] -Encoding UTF8
        Get-Content -Raw -LiteralPath $requiredDocs['06'] -Encoding UTF8
    ) -join "`n"

    foreach ($token in $canonicalTokens) {
        if (-not $canonicalContent.Contains($token)) {
            Add-Failure "Canonical PRD/architecture/data documents are missing token: $token."
        }
    }
}

Write-Output "Required documents: $($requiredNumbers.Count)"
Write-Output "Top-level Markdown files scanned: $($documentFiles.Count)"
Write-Output "Local links checked: $localLinkCount"
Write-Output "Failures: $($failures.Count)"

if ($failures.Count -gt 0) {
    foreach ($failure in $failures) {
        Write-Error $failure
    }
    exit 1
}

Write-Output 'Documentation validation passed.'
