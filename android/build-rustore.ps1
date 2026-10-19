#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$SigningDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) '.private/android-signing'),
    [string]$KeystorePath,
    [string]$KeyAlias,
    [string]$JavaHome = $env:JAVA_HOME,
    [string]$SdkPath = $env:ANDROID_HOME,
    [switch]$Offline
)
$ErrorActionPreference = 'Stop'
if (-not $JavaHome) { $JavaHome = Join-Path $env:ProgramFiles 'Android/Android Studio/jbr' }
if (-not (Test-Path -LiteralPath (Join-Path $JavaHome 'bin/java.exe') -PathType Leaf)) { throw 'JDK not found. Set JAVA_HOME or pass -JavaHome.' }
if (-not $SdkPath) { $SdkPath = $env:ANDROID_SDK_ROOT }
if (-not $SdkPath) { $SdkPath = Join-Path $env:LOCALAPPDATA 'Android/Sdk' }
if (-not (Test-Path -LiteralPath (Join-Path $SdkPath 'platforms/android-35/android.jar') -PathType Leaf)) { throw 'Android SDK Platform 35 not found. Install it in Android Studio SDK Manager or pass -SdkPath.' }
$signingPath = [IO.Path]::GetFullPath($SigningDirectory)
$useSaved = -not $KeystorePath
if ($useSaved) {
    $metadataFile = Join-Path $signingPath 'signing.json'
    $passwordFile = Join-Path $signingPath 'password.clixml'
    if (-not (Test-Path -LiteralPath $metadataFile) -or -not (Test-Path -LiteralPath $passwordFile)) {
        throw 'Saved signing key not found. Run create-release-key.ps1 once, or pass -KeystorePath and -KeyAlias for an existing key.'
    }
    $metadata = Get-Content -LiteralPath $metadataFile -Raw | ConvertFrom-Json
    $KeystorePath = Join-Path $signingPath $metadata.keyFile
    if (-not $KeyAlias) { $KeyAlias = $metadata.keyAlias }
    $storePassword = Import-Clixml -LiteralPath $passwordFile
    if ($storePassword -isnot [Security.SecureString]) { throw 'Saved password must be a DPAPI SecureString.' }
    $keyPassword = $storePassword
} else {
    if (-not $KeyAlias) { throw 'Pass -KeyAlias for your existing keystore.' }
    $storePassword = Read-Host 'Keystore password' -AsSecureString
    $keyPassword = Read-Host 'Key password' -AsSecureString
}
if (-not (Test-Path -LiteralPath $KeystorePath -PathType Leaf)) { throw 'Keystore file not found.' }
$names = @('JAVA_HOME','ANDROID_HOME','ANDROID_SDK_ROOT','JAVA_TOOL_OPTIONS','RUSTORE_KEYSTORE','RUSTORE_STORE_PASSWORD','RUSTORE_KEY_ALIAS','RUSTORE_KEY_PASSWORD')
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:JAVA_HOME = $JavaHome
    $env:ANDROID_HOME = $SdkPath
    $env:ANDROID_SDK_ROOT = $SdkPath
    $sockets = Join-Path $PSScriptRoot '.gradle/sockets'
    New-Item -ItemType Directory -Force -Path $sockets | Out-Null
    $env:JAVA_TOOL_OPTIONS = (($env:JAVA_TOOL_OPTIONS + ' -Djdk.net.unixdomain.tmpdir="' + $sockets + '"').Trim())
    $env:RUSTORE_KEYSTORE = [IO.Path]::GetFullPath($KeystorePath)
    $env:RUSTORE_KEY_ALIAS = $KeyAlias
    $env:RUSTORE_STORE_PASSWORD = [Net.NetworkCredential]::new('', $storePassword).Password
    $env:RUSTORE_KEY_PASSWORD = [Net.NetworkCredential]::new('', $keyPassword).Password
    $arguments = @('--no-daemon', 'assembleRuStore', 'lintRelease')
    if ($Offline) { $arguments += '--offline' }
    $aapt = Join-Path $SdkPath 'build-tools/35.0.0/aapt2.exe'
    if (Test-Path -LiteralPath $aapt -PathType Leaf) { $arguments += ('-Pandroid.aapt2FromMavenOverride=' + ($aapt -replace '\\','/')) }
    Push-Location $PSScriptRoot
    try {
        & (Join-Path $PSScriptRoot 'gradlew.bat') @arguments
        if ($LASTEXITCODE -ne 0) { throw 'RuStore release build failed. No upload should be performed.' }
    } finally { Pop-Location }
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    if ($keyPassword -ne $storePassword) { $keyPassword.Dispose() }
    $storePassword.Dispose()
}
Write-Output 'RuStore release build and lint passed. Upload only the *-rustore.apk from android/releases.'
