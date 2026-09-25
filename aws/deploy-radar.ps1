# Viral Radar - backend deploy. Safe to re-run.
#   powershell -ExecutionPolicy Bypass -File C:\Users\User\fare-finder-m1\aws\deploy-radar.ps1
# Updates the shared M2 paywall Lambdas (radar plan support) and creates radar-scan + radar-notification.

$env:AWS_PAGER = ""
$region      = "us-east-1"
$role        = "arn:aws:iam::767397924129:role/flight-lambda-role"
$root        = Split-Path -Parent $MyInvocation.MyCommand.Path    # ...\fare-finder-m1\aws
$work        = Join-Path $env:TEMP "flight-radar-deploy"
$statusQueue = "https://sqs.us-east-1.amazonaws.com/767397924129/flight-status-queue"
$alertQueue  = "https://sqs.us-east-1.amazonaws.com/767397924129/radar-alert-queue"
$site        = "https://fare-finder-two.vercel.app"
$sbUrl       = "https://xoeiypptsukfcbnidcmu.supabase.co"
$sbKey       = "sb_publishable_vbkQ71jR3WjYJd_jTt7NVw_CXnobmId"   # public (browser-safe) key

New-Item -ItemType Directory -Force $work | Out-Null

function Fail([string]$msg) {
  Write-Host "FAILED: $msg" -ForegroundColor Red
  exit 1
}

function Write-EnvFile([string]$name, [hashtable]$vars) {
  $path = Join-Path $work "$name-env.json"
  (@{ Variables = $vars } | ConvertTo-Json -Compress) | Out-File -Encoding ascii $path
  return $path
}

function Deploy-Fn([string]$fn, [string]$src, [int]$timeout, [hashtable]$vars) {
  $zip = Join-Path $work "$fn.zip"
  Compress-Archive -Path (Join-Path $root "$src\index.py") -DestinationPath $zip -Force
  $envFile = Write-EnvFile $fn $vars

  aws lambda get-function --function-name $fn --region $region --query Configuration.FunctionName --output text 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) {
    aws lambda update-function-code --function-name $fn --zip-file "fileb://$zip" --region $region --query LastUpdateStatus --output text | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "update-function-code $fn" }
    aws lambda wait function-updated-v2 --function-name $fn --region $region
    aws lambda update-function-configuration --function-name $fn --timeout $timeout --environment "file://$envFile" --region $region --query LastUpdateStatus --output text | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "update-function-configuration $fn" }
    aws lambda wait function-updated-v2 --function-name $fn --region $region
    $action = "updated"
  } else {
    aws lambda create-function --function-name $fn --runtime python3.12 --handler index.handler --role $role --timeout $timeout --zip-file "fileb://$zip" --environment "file://$envFile" --region $region --query FunctionName --output text | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "create-function $fn" }
    aws lambda wait function-active-v2 --function-name $fn --region $region
    $action = "created"
  }
  Write-Host ("  {0,-30} {1}" -f $fn, $action) -ForegroundColor Green
}

Write-Host "1/2 IAM: add radar_videos + radar-alert-queue to the flight-data policy" -ForegroundColor Cyan
aws iam put-role-policy --role-name flight-lambda-role --policy-name flight-data --policy-document "file://$root\iam\flight-data-policy.json"
if ($LASTEXITCODE -ne 0) { Fail "put-role-policy" }
Write-Host "  flight-data policy           updated" -ForegroundColor Green
Start-Sleep -Seconds 10

Write-Host "2/2 Lambdas" -ForegroundColor Cyan
# shared M2 paywall functions - now also understand plan "radar" (route RADAR)
Deploy-Fn "flight-save-subscription"   "flight-save-subscription"   10 @{ SUPABASE_URL = $sbUrl; SUPABASE_PUBLISHABLE_KEY = $sbKey; SITE_URL = $site }
Deploy-Fn "flight-cancel-subscription" "flight-cancel-subscription" 15 @{ SUPABASE_URL = $sbUrl; SUPABASE_PUBLISHABLE_KEY = $sbKey; STATUS_QUEUE_URL = $statusQueue }
Deploy-Fn "flight-ecpay-return"        "flight-ecpay-callback"      15 @{ CALLBACK_KIND = "return"; STATUS_QUEUE_URL = $statusQueue }
Deploy-Fn "flight-ecpay-period"        "flight-ecpay-callback"      15 @{ CALLBACK_KIND = "period"; STATUS_QUEUE_URL = $statusQueue }
Deploy-Fn "flight-status-notification" "flight-status-notification" 30 @{ SITE_URL = $site }
# new radar functions
Deploy-Fn "radar-scan"         "radar-scan"         120 @{ ALERT_QUEUE_URL = $alertQueue; REGION_CODE = "TW"; RELEVANCE_LANGUAGE = "zh-Hant"; MIN_VIEWS = "3000" }
Deploy-Fn "radar-notification" "radar-notification" 60  @{ SITE_URL = $site; ANTHROPIC_MODEL = "claude-haiku-4-5-20251001" }

Remove-Item -Recurse -Force $work
Write-Host "DONE - tell Claude so it can wire the schedules and the alert queue." -ForegroundColor Cyan
