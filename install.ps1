$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$tools = Join-Path $env:USERPROFILE 'tools'
$binDir = Join-Path $tools 'bin'
$gitExe = Join-Path $tools 'git\cmd\git.exe'
$ghExe = Join-Path $tools 'gh\bin\gh.exe'
$toolPy = Join-Path $PSScriptRoot 'ghpublish.py'

New-Item -ItemType Directory -Force -Path $tools, $binDir | Out-Null

function Get-LatestReleaseAsset($repo, $pattern, $outFile) {
    $rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers @{ 'User-Agent' = 'ghpublish' }
    $asset = $rel.assets | Where-Object { $_.name -like $pattern } | Select-Object -First 1
    if (-not $asset) { throw "找不到 $repo 的发布资源: $pattern" }
    Write-Host "下载 $($asset.name) ..."
    Invoke-WebRequest $asset.browser_download_url -OutFile $outFile
}

if (-not (Test-Path $gitExe)) {
    Write-Host '安装 portable Git (MinGit) ...'
    $zip = Join-Path $env:TEMP 'mingit.zip'
    Get-LatestReleaseAsset 'git-for-windows/git' 'MinGit-*-64-bit.zip' $zip
    Expand-Archive $zip -DestinationPath (Join-Path $tools 'git') -Force
}

if (-not (Test-Path $ghExe)) {
    Write-Host '安装 GitHub CLI ...'
    $zip = Join-Path $env:TEMP 'gh.zip'
    Get-LatestReleaseAsset 'cli/cli' '*windows_amd64.zip' $zip
    $tmp = Join-Path $tools 'gh-tmp'
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip -DestinationPath $tmp -Force
    if (Test-Path (Join-Path $tools 'gh')) { Remove-Item (Join-Path $tools 'gh') -Recurse -Force }
    Move-Item $tmp (Join-Path $tools 'gh')
}

# 一键命令 ghpublish
$shim = Join-Path $binDir 'ghpublish.cmd'
@(
    '@echo off'
    "python `"$toolPy`" %*"
) | Set-Content -Path $shim -Encoding ASCII

function Add-UserPath([string]$dir) {
    $cur = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $cur) { $cur = '' }
    $parts = @($cur -split ';' | Where-Object { $_ -ne '' })
    if ($parts -notcontains $dir) {
        [Environment]::SetEnvironmentVariable('Path', (($parts + $dir) -join ';'), 'User')
        Write-Host "已加入用户 PATH: $dir"
    }
}

Add-UserPath (Join-Path $tools 'git\cmd')
Add-UserPath (Join-Path $tools 'gh\bin')
Add-UserPath $binDir

$env:Path = "$(Join-Path $tools 'git\cmd');$(Join-Path $tools 'gh\bin');$binDir;$env:Path"
Write-Host ''
& $gitExe --version
& $ghExe --version
Write-Host ''
Write-Host '安装完成。新开一个终端后可直接运行: ghpublish doctor'
