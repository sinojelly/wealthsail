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
    'obsolete short binding token example' = 'WS-8899'
    'obsolete mock delivery success' = 'MOCK_SENT'
    'incorrect RLS physical-isolation claim' = '(?:RLS\s*(?:\u884c\u7ea7)?\s*\u7269\u7406\u9694\u79bb|\u4e25\u683c\u7269\u7406\u9694\u79bb)'
    'obsolete Go worker requirement' = 'Go/Worker'
    'nonexistent outbox_events table' = '(?:workflow\.)?outbox_events'
    'invalid pending decision state' = 'decisions\s*\([^\r\n)]*PENDING'
    'decision acceptance modeled as status mutation' = '(?:update|\u66f4\u65b0)\s+decisions\s*\(\s*ACCEPTED\s*\)'
    'unsafe universal fund cutoff promise' = '(?:\u63d0\u793a|\u662f\u5426)[^\r\n]{0,40}15:00\s*\u524d[^\r\n]{0,30}(?:\u8865\u4ed3|\u5b9a\u6295|\u7533\u8d4e)'
    'unsupported personal WeChat MVP promise' = '\u5f00\u542f\u5fae\u4fe1\u63d2\u4ef6\u8ba9\u4e2a\u4eba\u666e\u901a\u5fae\u4fe1'
    'incorrect accounting-grade ledger name' = '\u53cc\u5f0f\u8d26\u672c'
    'unsupported complete crash-safety claim' = '\u5b8c\u5168\u5d29\u6e83\u5b89\u5168'
}

foreach ($file in $documentFiles) {
    $content = Get-Content -Raw -LiteralPath $file.FullName -Encoding UTF8
    foreach ($pattern in $forbiddenPatterns.GetEnumerator()) {
        if ($content -match $pattern.Value) {
            Add-Failure "$($file.Name): $($pattern.Key)."
        }
    }
}

# Security invariants span documentation, adapters, and executable migrations.
# These checks intentionally target unsafe architectural patterns rather than
# cosmetic wording so a future documentation rewrite cannot silently restore
# a previously rejected trust boundary.
$repositorySecurityChecks = @(
    @{
        Path = $requiredDocs['03']
        Pattern = 'SUPABASE_SERVICE_ROLE_KEY'
        Message = 'Hermes/quick-start documentation must not contain a Supabase service-role credential.'
    },
    @{
        Path = Join-Path $repoRoot 'adapters/messaging/wecom_adapter.py'
        Pattern = 'to_user\s*:\s*str\s*=\s*["'']@all["'']'
        Message = 'WeCom tenant-private delivery must not default to @all.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/005_rls_security_policies.sql'
        Pattern = 'CREATE POLICY p_delete_tenant'
        Message = 'RLS migration must not grant a generic member DELETE policy to every tenant table.'
    },
    @{
        Path = $requiredDocs['01']
        Pattern = 'FOR\s+ALL[\s\S]{0,160}identity\.is_tenant_member'
        Message = 'PRD RLS examples must not turn membership into blanket write authorization.'
    }
)

foreach ($check in $repositorySecurityChecks) {
    if (-not (Test-Path -LiteralPath $check.Path)) {
        Add-Failure "Security invariant target is missing: $($check.Path)."
        continue
    }
    $securityContent = Get-Content -Raw -LiteralPath $check.Path -Encoding UTF8
    if ($securityContent -match $check.Pattern) {
        Add-Failure $check.Message
    }
}

$repositoryRequiredChecks = @(
    @{
        Path = $requiredDocs['02']
        Pattern = '(?s)### 8\.3.*(?:Prompt|prompt).*feature flag'
        Message = 'Architecture must retain explicit prompt-injection and feature-flag controls for L5 multi-agent experiments.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/002_identity_and_tenancy.sql'
        Pattern = '128 bit'
        Message = 'Binding-token schema guidance must require at least 128 bits of random entropy.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/004_market_and_research.sql'
        Pattern = 'CREATE TABLE IF NOT EXISTS workflow\.interaction_requests'
        Message = 'Canonical schema must persist pending HITL interaction requests separately from immutable decisions.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/005_rls_security_policies.sql'
        Pattern = 'CREATE POLICY interaction_request_self_select'
        Message = 'RLS migration must restrict interaction requests to the bound user, not every tenant member.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/005_rls_security_policies.sql'
        Pattern = 'REVOKE SELECT ON workflow\.interaction_requests'
        Message = 'Authenticated users must not receive table-wide access to interaction token hashes.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/007_security_and_integrity_hardening.sql'
        Pattern = 'CREATE TABLE IF NOT EXISTS workflow\.interaction_requests'
        Message = 'Upgrade migration must add workflow.interaction_requests to existing installations.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/007_security_and_integrity_hardening.sql'
        Pattern = 'protect_interaction_request'
        Message = 'Upgrade migration must protect interaction request identity and terminal states.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/007_security_and_integrity_hardening.sql'
        Pattern = 'CREATE POLICY interaction_request_self_select'
        Message = 'Upgrade migration must retain per-user RLS for interaction requests.'
    }
)

foreach ($check in $repositoryRequiredChecks) {
    if (-not (Test-Path -LiteralPath $check.Path)) {
        Add-Failure "Required architecture target is missing: $($check.Path)."
        continue
    }
    $requiredContent = Get-Content -Raw -LiteralPath $check.Path -Encoding UTF8
    if ($requiredContent -notmatch $check.Pattern) {
        Add-Failure $check.Message
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

    foreach ($id in ($definitionIds | Sort-Object -Unique)) {
        if ($traceContent -notmatch [regex]::Escape($id)) {
            Add-Failure "PRD requirement missing from traceability: $id."
        }
    }

    Write-Output "Requirement definitions: $($definitionIds.Count)"
    Write-Output "L0/L1 Must requirements: $(($mustIds | Sort-Object -Unique).Count)"
}

# Every test ID referenced by the traceability matrix must have one explicit
# definition in document 04 or 05. Compact 001~006 and 001/002 notation is
# expanded so a range cannot hide missing abnormal branches.
if ($requiredDocs.ContainsKey('04') -and $requiredDocs.ContainsKey('05') -and $requiredDocs.ContainsKey('08')) {
    $testDefinitionPattern = '^\|\s*((?:UT|CT|IT|WF|WEC|E2E|AI|NFR|L2|L3|L4|L5|FULL)-[A-Z0-9]+-\d{3})\s*\|'
    $testDefinitions = [System.Collections.Generic.List[string]]::new()

    foreach ($testDocNumber in @('04', '05')) {
        foreach ($line in (Get-Content -LiteralPath $requiredDocs[$testDocNumber] -Encoding UTF8)) {
            if ($line -match $testDefinitionPattern) {
                $testDefinitions.Add($Matches[1])
            }
        }
    }

    foreach ($duplicate in @($testDefinitions | Group-Object | Where-Object Count -gt 1)) {
        Add-Failure "Duplicate test definition: $($duplicate.Name) ($($duplicate.Count) occurrences)."
    }

    $traceContent = Get-Content -Raw -LiteralPath $requiredDocs['08'] -Encoding UTF8
    $testReferencePattern = '(?<prefix>(?:UT|CT|IT|WF|WEC|E2E|AI|NFR|L2|L3|L4|L5|FULL)-[A-Z0-9]+-)(?<start>\d{3})(?:(?<separator>[~/])(?<end>\d{3}))?'
    $testReferences = [System.Collections.Generic.HashSet[string]]::new()

    foreach ($match in [regex]::Matches($traceContent, $testReferencePattern)) {
        $prefix = $match.Groups['prefix'].Value
        $start = [int]$match.Groups['start'].Value
        $endGroup = $match.Groups['end']
        $end = if ($endGroup.Success) { [int]$endGroup.Value } else { $start }

        if ($end -lt $start) {
            Add-Failure "Descending test range in traceability: $($match.Value)."
            continue
        }

        foreach ($number in $start..$end) {
            [void]$testReferences.Add(('{0}{1:D3}' -f $prefix, $number))
        }
    }

    $definitionSet = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($id in $testDefinitions) {
        [void]$definitionSet.Add($id)
    }
    foreach ($id in ($testReferences | Sort-Object)) {
        # Requirement IDs with AI-/NFR- prefixes can resemble test IDs in the
        # first column of the matrix. They are not test references unless a
        # matching test definition also exists.
        if (-not $definitionSet.Contains($id) -and -not ($definitionIds -contains $id)) {
            Add-Failure "Traceability references an undefined test: $id."
        }
    }

    Write-Output "Test definitions: $($definitionSet.Count)"
    Write-Output "Traceability test references: $($testReferences.Count)"
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
    'interaction_requests',
    'NFR-LLM-001',
    'NFR-HITL-001',
    'SEC-CBK-001',
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
