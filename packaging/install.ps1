$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$stagingDirectory = $null
$backupDirectory = $null
$installDirectory = $null
try {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (-not (Get-Process -Name SubtitleFlow -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Seconds 1
    }
    if (Get-Process -Name SubtitleFlow -ErrorAction SilentlyContinue) {
        throw 'Please close SubtitleFlow, then run the installer again.'
    }
    $programsDirectory = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
    [System.IO.Directory]::CreateDirectory($programsDirectory) | Out-Null
    $installDirectory = [System.IO.Path]::GetFullPath((Join-Path $programsDirectory 'SubtitleFlow'))
    $stagingDirectory = [System.IO.Path]::GetFullPath((Join-Path $programsDirectory ('SubtitleFlow-staging-' + [Guid]::NewGuid().ToString('N'))))
    $backupDirectory = [System.IO.Path]::GetFullPath((Join-Path $programsDirectory ('SubtitleFlow-backup-' + [Guid]::NewGuid().ToString('N'))))
    foreach ($candidate in @($installDirectory, $stagingDirectory, $backupDirectory)) {
        if (-not $candidate.StartsWith($programsDirectory + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Invalid installation path.'
        }
    }
    Expand-Archive -LiteralPath (Join-Path $PSScriptRoot 'app.zip') -DestinationPath $stagingDirectory
    if (-not (Test-Path -LiteralPath (Join-Path $stagingDirectory 'SubtitleFlow.exe'))) {
        throw 'The application archive is incomplete.'
    }
    if (Test-Path -LiteralPath $installDirectory) {
        Move-Item -LiteralPath $installDirectory -Destination $backupDirectory
    }
    try {
        Move-Item -LiteralPath $stagingDirectory -Destination $installDirectory
    } catch {
        if (Test-Path -LiteralPath $backupDirectory) {
            Move-Item -LiteralPath $backupDirectory -Destination $installDirectory
        }
        throw
    }
    $shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'SubtitleFlow.lnk'
    $shortcutShell = New-Object -ComObject WScript.Shell
    $shortcut = $shortcutShell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $installDirectory 'SubtitleFlow.exe'
    $shortcut.WorkingDirectory = $installDirectory
    $shortcut.Save()
    $uninstaller = Join-Path $installDirectory 'uninstall.ps1'
    if (-not (Test-Path -LiteralPath $uninstaller)) { throw 'Uninstaller missing from package.' }
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SubtitleFlow'
    New-Item -Path $key -Force | Out-Null
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $properties = @{
        DisplayName = 'SubtitleFlow'
        DisplayVersion = (Get-Content -LiteralPath (Join-Path $installDirectory 'installed-version.txt') -Raw).Trim()
        Publisher = 'XMRayLabs'
        InstallLocation = $installDirectory
        DisplayIcon = (Join-Path $installDirectory 'SubtitleFlow.exe')
        UninstallString = ('"{0}" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{1}"' -f $powershell, $uninstaller)
        URLInfoAbout = 'https://github.com/XMRayLabs/SubtitleFlow'
    }
    foreach ($name in $properties.Keys) {
        New-ItemProperty -Path $key -Name $name -Value $properties[$name] -PropertyType String -Force | Out-Null
    }
    New-ItemProperty -Path $key -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $key -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null
    # Keep the preceding installation as a recoverable backup; never recursively delete it.
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'SubtitleFlow installation failed')
    exit 1
}
