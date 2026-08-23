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
$requiredNumbers = @('01', '02', '03', '04', '05', '06', '07', '08', '09', '10')
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
    },
    @{
        Path = $requiredDocs['09']
        Pattern = '(?:POST\s+/api/v1/identity/bindings/token|DELETE\s+/api/v1/identity/bindings|record_cash_in\s+\u4e0e\s+record_buy|\u652f\u6301\u591a\u79df\u6237\u5207\u6362)'
        Message = 'Web design must not restore the obsolete binding endpoint, cash-in/fill coupling, physical binding delete, or L1 in-page tenant switching.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = '(?:identity\.)?is_tenant_member\s+(?:\u7edf\u4e00\u63d0\u53d6|\u4ece\u53ef\u4fe1\u4f1a\u8bdd\u89e3\u6790)'
        Message = 'Web design must treat is_tenant_member as a predicate, not a tenant-context extractor.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = '(?:Equity Curve\s+vs\s+\u6caa\u6df1\s*300|\u624b\u5de5\u6539\u8d26)'
        Message = 'Web design must not hard-code CSI 300 as the active benchmark or imply direct manual ledger editing.'
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
    },
    @{
        Path = Join-Path $repoRoot 'migrations/008_web_workbench_foundation.sql'
        Pattern = 'CREATE TABLE identity\.binding_challenges'
        Message = 'Migration 008 must separate pending binding challenges from verified channel bindings.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/008_web_workbench_foundation.sql'
        Pattern = 'CREATE TABLE ledger\.portfolio_import_batches[\s\S]+CREATE TABLE ledger\.portfolio_import_items'
        Message = 'Migration 008 must persist atomic portfolio-import batches and items.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/008_web_workbench_foundation.sql'
        Pattern = 'ALTER TABLE identity\.binding_challenges ENABLE ROW LEVEL SECURITY[\s\S]+binding_challenge_self_select'
        Message = 'Migration 008 must enable RLS and self-scoped reads for binding challenges.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/008_web_workbench_foundation.sql'
        Pattern = 'REVOKE INSERT, UPDATE ON identity\.channel_bindings FROM authenticated'
        Message = 'Migration 008 must prevent browser JWT binding writes.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/008_web_workbench_foundation.sql'
        Pattern = 'REVOKE INSERT, UPDATE, DELETE ON risk\.investment_policies FROM authenticated'
        Message = 'Migration 008 must prevent browser JWT investment-policy writes.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/008_web_workbench_foundation.sql'
        Pattern = 'REVOKE SELECT ON identity\.channel_bindings FROM authenticated'
        Message = 'Migration 008 must hide verified binding hashes and deprecated token columns from browser JWTs.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/008_web_workbench_foundation.sql'
        Pattern = '(?s)REVOKE SELECT ON\s+identity\.secret_refs,\s+workflow\.jobs, workflow\.job_runs, workflow\.outbox,\s+audit\.audit_log\s+FROM authenticated'
        Message = 'Migration 008 must force sensitive operations and secret-reference reads through the Domain API.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/008_web_workbench_foundation.sql'
        Pattern = 'operations_owner_admin_select'
        Message = 'Migration 008 must restrict operational and audit reads to owner/admin.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/008_web_workbench_foundation.sql'
        Pattern = 'BEFORE INSERT OR UPDATE OR DELETE ON ledger\.portfolio_import_items'
        Message = 'Migration 008 must prevent items from being inserted after an import batch becomes terminal.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/008_web_workbench_foundation.sql'
        Pattern = '(?s)ADD COLUMN import_batch_id UUID.*ADD COLUMN import_item_id UUID.*uq_ledger_events_import_item_effect'
        Message = 'Migration 008 must link every opening-balance effect back to one reviewed import item.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/008_web_workbench_foundation.sql'
        Pattern = "confirmation_provenance = 'LEGACY_MIGRATED'"
        Message = 'Migration 008 must preserve unknown legacy policy confirmation provenance without inventing an actor.'
    },
    @{
        Path = Join-Path $repoRoot 'migrations/008_web_workbench_foundation.sql'
        Pattern = 'migration 008 found duplicate current CONFIRMED policies'
        Message = 'Migration 008 must stop before silently choosing among conflicting current policies.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = '(?s)(?=.*FR-WEB-001)(?=.*NFR-FIN-001)(?=.*Idempotency-Key)(?=.*binding_challenges)(?=.*portfolio_import_batches)(?=.*ChartNumberAdapter)'
        Message = 'Web design must retain requirement mapping, idempotency, separated workflow objects, and the chart precision boundary.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = '(?s)VIEWER.*EDITOR.*ADMIN.*OWNER.*L1\s+DTO\s+\u4e0d\u542b\u53ef\u7f16\u8f91'
        Message = 'Web design must retain the complete role matrix and reject editable L4 allocation targets in L1.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = '\u6295\u8d44\u671f\u9650[^\r\n]{0,160}investment_horizon_months'
        Message = 'Web risk-policy design must retain the investment horizon and its canonical field mapping.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = '\u6240\u6709\u767e\u5206\u6bd4\u5b57\u6bb5\u9ed8\u8ba4\u4e3a\u7a7a'
        Message = 'Web risk-policy design must keep percentage fields empty until the user enters a policy.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = 'LEGACY_MIGRATED'
        Message = 'Web risk-policy design must expose incomplete legacy confirmation provenance.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = '412 VERSION_CONFLICT'
        Message = 'Web risk-policy design must retain optimistic-concurrency conflict handling.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = 'report_item\s*/\s*evidence_ref'
        Message = 'Web evidence design must retain item-level evidence references.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = 'published_at\s*/\s*as_of\s*/\s*retrieved_at'
        Message = 'Web evidence design must distinguish source publication, fact, and retrieval times.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = 'Prompt/Skill/schema/method_version'
        Message = 'Web evidence design must retain model, Skill, schema, and method versions.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = '\u4e8b\u5b9e[^\r\n]{0,80}\u89e3\u91ca[^\r\n]{0,80}\u63a8\u65ad'
        Message = 'Web evidence design must distinguish facts, explanations, and inferences.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = 'L5[^\r\n]*feature flag[^\r\n]*read-only scoped tools'
        Message = 'Web multi-agent experiments must remain feature-flagged and read-only by default.'
    },
    @{
        Path = $requiredDocs['09']
        Pattern = 'Skill[^\r\n]*\u7981\u6b62\u5728\u7ebf\u81ea\u6539\u751f\u4ea7\u89c4\u5219'
        Message = 'Web Skill experiments must not modify production rules online.'
    },
    @{
        Path = $requiredDocs['04']
        Pattern = '(?s)(?=.*E2E-WEB-001)(?=.*E2E-WEB-005)(?=.*UT-FIN-001)(?=.*UT-FIN-006)(?=.*WEC-BND-005)'
        Message = 'MVP test design must cover the Web journey, financial calculations, and binding-token leakage.'
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
    'FR-WEB-001',
    'NFR-FIN-001',
    'binding_challenges',
    'portfolio_import_batches',
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
