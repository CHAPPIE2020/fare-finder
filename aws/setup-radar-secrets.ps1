# Viral Radar - store API keys in Secrets Manager (keys are typed here, never pasted into chat).
#   powershell -ExecutionPolicy Bypass -File C:\Users\User\fare-finder-m1\aws\setup-radar-secrets.ps1
# flight/youtube   = {"api_key": "..."}   (required)
# flight/anthropic = {"api_key": "..."}   (optional - AI breakdown in the alert email)

$env:AWS_PAGER = ""
$region = "us-east-1"

function Save-Secret([string]$name, [string]$json) {
  $tmp = Join-Path $env:TEMP (($name -replace "/", "-") + ".json")
  $json | Out-File -Encoding ascii $tmp
  aws secretsmanager describe-secret --secret-id $name --region $region --query Name --output text 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) {
    aws secretsmanager put-secret-value --secret-id $name --secret-string "file://$tmp" --region $region --query Name --output text | Out-Null
  } else {
    aws secretsmanager create-secret --name $name --secret-string "file://$tmp" --region $region --query Name --output text | Out-Null
  }
  $ok = ($LASTEXITCODE -eq 0)
  Remove-Item $tmp -Force
  if ($ok) { Write-Host "  $name saved" -ForegroundColor Green } else { Write-Host "  $name FAILED" -ForegroundColor Red }
}

$yt = (Read-Host "Paste your YouTube Data API key").Trim()
if ($yt) {
  try {
    $probe = Invoke-RestMethod -UseBasicParsing -Uri ("https://www.googleapis.com/youtube/v3/videos?part=id&chart=mostPopular&regionCode=TW&maxResults=1&key=" + $yt)
    Write-Host "  YouTube key works (test call OK)" -ForegroundColor Green
    Save-Secret "flight/youtube" (@{ api_key = $yt } | ConvertTo-Json -Compress)
  } catch {
    Write-Host "  YouTube key test FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Check: YouTube Data API v3 is ENABLED in the same Google Cloud project, and the key is not restricted to other APIs." -ForegroundColor Yellow
  }
}

$an = (Read-Host "Paste your Anthropic API key (optional - press Enter to skip)").Trim()
if ($an) { Save-Secret "flight/anthropic" (@{ api_key = $an } | ConvertTo-Json -Compress) }

Write-Host "DONE" -ForegroundColor Cyan
