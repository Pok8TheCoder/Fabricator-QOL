# One-time: publish this fork to your GitHub account.
# Requires: winget install GitHub.cli  &&  gh auth login

$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

$repoName = "Fabricator-QOL"
$description = "Fork of philderks/Fabricator — with our own edits and QOL. Full credit to the original authors."

gh auth status | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Not logged in. Run: gh auth login"
    exit 1
}

if (-not (git remote get-url upstream 2>$null)) {
    git remote add upstream https://github.com/philderks/Fabricator.git
}

if (git remote get-url origin 2>$null) {
    Write-Host "Remote origin exists. Push with: git push -u origin main"
    exit 0
}

gh repo create $repoName --public --source=. --remote=origin --push --description $description

$user = gh api user -q .login
Write-Host "Published: https://github.com/$user/$repoName"
