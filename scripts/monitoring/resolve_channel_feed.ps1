$ErrorActionPreference = "Stop"

if ($env:GITHUB_REF -ne "refs/heads/main") {
    throw "The White House monitor must run from main to publish the site."
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$stateFile = Join-Path $repoRoot ".github\state\whitehouse-processed-videos.txt"
$processed = if (Test-Path $stateFile) { Get-Content $stateFile } else { @() }
$feedUrl = "https://www.youtube.com/feeds/videos.xml?channel_id=UCXZC1DQ5d7O7fCwD3WhkLxw"
$feed = [xml](Invoke-WebRequest -Uri $feedUrl -TimeoutSec 30).Content

$namespaces = [System.Xml.XmlNamespaceManager]::new($feed.NameTable)
$namespaces.AddNamespace("atom", "http://www.w3.org/2005/Atom")
$namespaces.AddNamespace("yt", "http://www.youtube.com/xml/schemas/2015")
$entries = $feed.SelectNodes("/atom:feed/atom:entry", $namespaces)
if ($entries.Count -eq 0) {
    throw "The White House YouTube feed returned no entries."
}

$videoId = ""
$videoTitle = ""
foreach ($entry in $entries) {
    $idNode = $entry.SelectSingleNode("yt:videoId", $namespaces)
    if ($null -eq $idNode -or $idNode.InnerText -notmatch "^[A-Za-z0-9_-]{11}$") {
        continue
    }
    if ($env:FORCE_CHECK -ne "true" -and $processed -contains $idNode.InnerText) {
        continue
    }

    $videoId = $idNode.InnerText
    $titleNode = $entry.SelectSingleNode("atom:title", $namespaces)
    $videoTitle = if ($null -eq $titleNode) { $videoId } else { $titleNode.InnerText }
    break
}

if ($videoId) {
    Write-Host "Discovered ${videoId}: $videoTitle"
} else {
    Write-Host "No unprocessed uploads found in the latest channel feed."
}

$videoTitle = $videoTitle.Replace("%", "%25").Replace("`r", "%0D").Replace("`n", "%0A")
"video_id=$videoId" | Add-Content -Path $env:GITHUB_OUTPUT -Encoding utf8
"video_title=$videoTitle" | Add-Content -Path $env:GITHUB_OUTPUT -Encoding utf8
