$ErrorActionPreference = "Stop"

if ($env:GITHUB_REF -ne "refs/heads/main") {
    throw "The White House monitor must run from main to publish the site."
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$stateFile = Join-Path $repoRoot ".github\state\whitehouse-processed-videos.txt"
$processed = if (Test-Path $stateFile) { Get-Content $stateFile } else { @() }
$videoId = ""
$videoTitle = ""
$channels = @(
      "https://www.youtube.com/@WhiteHouse/videos",
      "https://www.youtube.com/@WhiteHouse/streams"
)
$proxyCandidates = @()
if (-not [string]::IsNullOrWhiteSpace($env:YTDLP_PROXY_POOL_JSON)) {
    $proxyCandidates = @($env:YTDLP_PROXY_POOL_JSON | ConvertFrom-Json)
}
$foundItems = 0
foreach ($channel in $channels) {
      $listing = & yt-dlp --js-runtimes deno --no-warnings --ignore-errors --flat-playlist --playlist-end 10 --print "%(id)s|%(title)s" $channel 2>$null
      $listingExitCode = $LASTEXITCODE
      if ($listingExitCode -ne 0 -and $proxyCandidates.Count -gt 0) {
          foreach ($proxy in $proxyCandidates) {
              $listing = & yt-dlp --proxy $proxy --js-runtimes deno --no-warnings --ignore-errors --flat-playlist --playlist-end 10 --print "%(id)s|%(title)s" $channel 2>$null
              $listingExitCode = $LASTEXITCODE
              if ($listingExitCode -eq 0 -and $listing.Count -gt 0) { break }
          }
      }
      if ($listingExitCode -ne 0 -and $listing.Count -eq 0) {
          throw "Could not inspect White House channel: $channel"
      }
      foreach ($line in $listing) {
          $parts = $line -split "\|", 2
          $candidate = $parts[0]
          if ($candidate -notmatch "^[A-Za-z0-9_-]{11}$") { continue }
          $foundItems++
          if ($env:FORCE_CHECK -ne "true" -and $processed -contains $candidate) { continue }

          $videoId = $candidate
          $videoTitle = if ($parts.Count -gt 1 -and $parts[1]) { $parts[1] } else { $videoId }
          break
      }
      if ($videoId) { break }
}
if ($foundItems -eq 0) {
      throw "No video IDs were returned by either White House channel listing."
}

if ($videoId) {
    Write-Host "Discovered ${videoId}: $videoTitle"
} else {
      Write-Host "No unprocessed uploads found in the latest channel entries."
}

$videoTitle = $videoTitle.Replace("%", "%25").Replace("`r", "%0D").Replace("`n", "%0A")
"video_id=$videoId" | Add-Content -Path $env:GITHUB_OUTPUT -Encoding utf8
"video_title=$videoTitle" | Add-Content -Path $env:GITHUB_OUTPUT -Encoding utf8
