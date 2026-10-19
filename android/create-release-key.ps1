#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$SigningDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) '.private/android-signing'),
    [ValidatePattern('^[A-Za-z0-9._-]+$')][string]$KeyAlias = 'qicko',
    [string]$JavaHome = $env:JAVA_HOME,
    [switch]$GeneratePassword
)
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
    throw 'This helper protects the password with Windows DPAPI. On macOS/Linux use Android Studio Generate Signed APK.'
}
if (-not $JavaHome) { $JavaHome = Join-Path $env:ProgramFiles 'Android/Android Studio/jbr' }
$keytool = Join-Path $JavaHome 'bin/keytool.exe'
if (-not (Test-Path -LiteralPath $keytool -PathType Leaf)) { throw 'JDK not found. Set JAVA_HOME or pass -JavaHome.' }
$signingPath = [IO.Path]::GetFullPath($SigningDirectory)
$keyPath = Join-Path $signingPath 'qicko-release.jks'
$passwordPath = Join-Path $signingPath 'password.clixml'
if ((Test-Path -LiteralPath $keyPath) -or (Test-Path -LiteralPath $passwordPath)) {
    throw 'Signing files already exist. They will NOT be replaced. Use the existing key for updates.'
}
New-Item -ItemType Directory -Force -Path $signingPath | Out-Null
# Restrict this directory to its Windows owner and SYSTEM before writing secrets.
$owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetOwner($owner)
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @($owner, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $rule = [Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $signingPath -AclObject $acl
if ($GeneratePassword) {
    $bytes = New-Object byte[] 32
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($bytes) } finally { $random.Dispose() }
    $password = ConvertTo-SecureString ([Convert]::ToBase64String($bytes)) -AsPlainText -Force
    [Array]::Clear($bytes, 0, $bytes.Length)
} else {
    $password = Read-Host 'New permanent signing password (at least 12 characters)' -AsSecureString
}
if ($password.Length -lt 12) { throw 'Use a signing password of at least 12 characters.' }
# Export-Clixml uses DPAPI: readable only by this Windows user on this computer.
$password | Export-Clixml -LiteralPath $passwordPath
$previous = [Environment]::GetEnvironmentVariable('PORTAL_KEY_STORE_PASSWORD', 'Process')
try {
    $env:PORTAL_KEY_STORE_PASSWORD = [Net.NetworkCredential]::new('', $password).Password
    & $keytool -genkeypair -storetype JKS -keystore $keyPath -storepass:env PORTAL_KEY_STORE_PASSWORD -keypass:env PORTAL_KEY_STORE_PASSWORD -alias $KeyAlias -keyalg RSA -keysize 4096 -sigalg SHA256withRSA -validity 10000 -dname 'CN=Qicko Android Signing, OU=Application Signing' -noprompt
    if ($LASTEXITCODE -ne 0) { throw 'Key generation failed. Keep the password file and inspect the directory before retrying.' }
    @{ keyAlias=$KeyAlias; keyFile='qicko-release.jks'; passwordFile='password.clixml'; algorithm='RSA-4096'; createdUtc=[DateTime]::UtcNow.ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $signingPath 'signing.json') -Encoding UTF8
} finally {
    [Environment]::SetEnvironmentVariable('PORTAL_KEY_STORE_PASSWORD', $previous, 'Process')
    $password.Dispose()
}
Write-Output "Permanent release key created: $keyPath"
Write-Output "Encrypted password saved: $passwordPath"
Write-Output 'Back up the keystore AND the password in a password manager. DPAPI files alone are not a portable backup.'
Write-Output 'Never put these files in Git, the publication ZIP, or the store console.'
