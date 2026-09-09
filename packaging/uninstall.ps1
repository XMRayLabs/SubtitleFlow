param([switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
try {
    $expected = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\SubtitleFlow'))
    $actual = [IO.Path]::GetFullPath($PSScriptRoot)
    if (-not $actual.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Uninstaller must run from the SubtitleFlow installation directory.'
    }
    # Reject redirected folders before any recursive removal.
    $ancestor = Get-Item -LiteralPath $actual -Force
    while ($null -ne $ancestor) {
        if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Redirected installation paths are not supported.' }
        $ancestor = $ancestor.Parent
    }
    if (Get-ChildItem -LiteralPath $actual -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
        throw 'Installation contains redirected files. Remove them before uninstalling.'
    }
    if ($ValidateOnly) { return }
    $answer = [Windows.Forms.MessageBox]::Show('卸载 SubtitleFlow？字幕输出、设置和已保存的密钥将保留。', '卸载 SubtitleFlow', 'YesNo', 'Question')
    if ($answer -ne [Windows.Forms.DialogResult]::Yes) { return }
    if (Get-Process -Name SubtitleFlow -ErrorAction SilentlyContinue) {
        throw '请先退出 SubtitleFlow，然后重新卸载。'
    }
    Set-Location -LiteralPath $env:TEMP
    Remove-Item -LiteralPath $actual -Recurse -Force
    $shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'SubtitleFlow.lnk'
    if (Test-Path -LiteralPath $shortcutPath) {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        if ($shortcut.TargetPath -eq (Join-Path $expected 'SubtitleFlow.exe')) {
            Remove-Item -LiteralPath $shortcutPath -Force
        }
    }
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SubtitleFlow'
    if (Test-Path -LiteralPath $key) { Remove-Item -LiteralPath $key -Force }
    [Windows.Forms.MessageBox]::Show('SubtitleFlow 已卸载。', 'SubtitleFlow') | Out-Null
} catch {
    if ($ValidateOnly) { throw }
    [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'SubtitleFlow 卸载失败') | Out-Null
    exit 1
}
