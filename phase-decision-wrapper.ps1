param(
    [string]$BaseUrl = "http://localhost:8002",
    [int]$CutoffHours = 72,
    [string]$ArchivePath = ""
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ArchivePath)) {
    $ArchivePath = Join-Path $ScriptDir "logs\phase-decision-archive.log"
}

$ErrorActionPreference = "Stop"

function Get-DecisionColor {
    param([string]$Decision)
    switch ($Decision) {
        "CONTINUE" { return "Green" }
        "WATCH" { return "Yellow" }
        "ROLLBACK_CANDIDATE" { return "Red" }
        default { return "White" }
    }
}

function Format-Num {
    param(
        $Value,
        [int]$Digits = 3
    )
    if ($null -eq $Value) { return "-" }
    try {
        return ([double]$Value).ToString("F$Digits", [System.Globalization.CultureInfo]::InvariantCulture)
    } catch {
        return "$Value"
    }
}

try {
    $uri = "$BaseUrl/api/analytics/phase-decision-script?cutoff_hours=$CutoffHours"
    $resp = Invoke-RestMethod -Uri $uri -Method Get

    $decision = "$($resp.phase_decision)"
    $decisionColor = Get-DecisionColor -Decision $decision
    $kpi = $resp.top_dashboard_metrics
    $veto = $resp.veto_flags

    $pf = Format-Num $kpi.trading_only_pf 2
    $avg = Format-Num $kpi.avg_pnl_per_trade 4
    $stale = Format-Num $kpi.stale_data_share 3
    $execPos = Format-Num $kpi.positive_executable_share 3

    Write-Host ""
    Write-Host "PHASE DECISION: $decision" -ForegroundColor $decisionColor
    Write-Host ("=" * 72) -ForegroundColor DarkGray
    Write-Host "Decision: $decision | PF $pf | AvgPnL $avg | Stale $stale | Exec+ $execPos" -ForegroundColor $decisionColor
    Write-Host ("=" * 72) -ForegroundColor DarkGray
    Write-Host ""

    Write-Host "VETO FLAGS" -ForegroundColor Cyan
    $vetoRows = @(
        [pscustomobject]@{ Flag = "stale_share_worse"; Value = [bool]$veto.stale_share_worse },
        [pscustomobject]@{ Flag = "trading_only_pf_not_improved"; Value = [bool]$veto.trading_only_pf_not_improved },
        [pscustomobject]@{ Flag = "avg_pnl_per_trade_not_improved"; Value = [bool]$veto.avg_pnl_per_trade_not_improved },
        [pscustomobject]@{ Flag = "open_positions_risk"; Value = [bool]$veto.open_positions_risk }
    )
    foreach ($row in $vetoRows) {
        $color = if ($row.Value) { "Red" } else { "Green" }
        Write-Host (" - {0,-34}: {1}" -f $row.Flag, $row.Value) -ForegroundColor $color
    }
    Write-Host ""

    Write-Host "TOP 9 KPI" -ForegroundColor Cyan
    $kpiRows = @(
        [pscustomobject]@{ Metric = "trading_only_pf"; Value = (Format-Num $kpi.trading_only_pf 4) },
        [pscustomobject]@{ Metric = "avg_pnl_per_trade"; Value = (Format-Num $kpi.avg_pnl_per_trade 6) },
        [pscustomobject]@{ Metric = "stale_data_share"; Value = (Format-Num $kpi.stale_data_share 4) },
        [pscustomobject]@{ Metric = "positive_executable_share"; Value = (Format-Num $kpi.positive_executable_share 4) },
        [pscustomobject]@{ Metric = "calibration_method"; Value = "$($kpi.calibration_method)" },
        [pscustomobject]@{ Metric = "calibration_sample_size"; Value = "$($kpi.calibration_sample_size)" },
        [pscustomobject]@{ Metric = "top_reject_reason"; Value = "$($kpi.top_reject_reason)" },
        [pscustomobject]@{ Metric = "direct_copy_pf"; Value = (Format-Num $kpi.direct_copy_pf 4) },
        [pscustomobject]@{ Metric = "high_conviction_pf"; Value = (Format-Num $kpi.high_conviction_pf 4) }
    )
    $kpiRows | Format-Table -AutoSize

    Write-Host "DECISION REASONS" -ForegroundColor Cyan
    if ($resp.decision_reasons -and $resp.decision_reasons.Count -gt 0) {
        foreach ($reason in $resp.decision_reasons) {
            Write-Host " - $reason" -ForegroundColor Gray
        }
    } else {
        Write-Host " - (none)" -ForegroundColor DarkGray
    }
    Write-Host ""

    Write-Host "NEXT SINGLE RECOMMENDATION" -ForegroundColor Cyan
    Write-Host "$($resp.next_single_recommendation)" -ForegroundColor White
    Write-Host ""

    # Append to decision archive
    $archiveDir = Split-Path -Parent $ArchivePath
    if (-not [string]::IsNullOrWhiteSpace($archiveDir) -and -not (Test-Path $archiveDir)) {
        New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null
    }
    $ts = $resp.generated_at
    if ([string]::IsNullOrWhiteSpace($ts)) { $ts = (Get-Date).ToUniversalTime().ToString("o") }
    $archiveBlock = @"

--- $ts
decision: $decision
veto_flags: stale_share_worse=$($veto.stale_share_worse) trading_only_pf_not_improved=$($veto.trading_only_pf_not_improved) avg_pnl_per_trade_not_improved=$($veto.avg_pnl_per_trade_not_improved) open_positions_risk=$($veto.open_positions_risk)
top_kpi: trading_only_pf=$($kpi.trading_only_pf) avg_pnl_per_trade=$($kpi.avg_pnl_per_trade) stale_data_share=$($kpi.stale_data_share) positive_executable_share=$($kpi.positive_executable_share) calibration_method=$($kpi.calibration_method) calibration_sample_size=$($kpi.calibration_sample_size) top_reject_reason=$($kpi.top_reject_reason) direct_copy_pf=$($kpi.direct_copy_pf) high_conviction_pf=$($kpi.high_conviction_pf)
next_recommendation: $($resp.next_single_recommendation)
"@
    Add-Content -Path $ArchivePath -Value $archiveBlock -Encoding UTF8
    Write-Host "Archived to: $ArchivePath" -ForegroundColor DarkGray
}
catch {
    Write-Host "Wrapper failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Tip: Ensure backend is running and endpoint exists: /api/analytics/phase-decision-script" -ForegroundColor Yellow
    exit 1
}
