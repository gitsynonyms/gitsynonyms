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
    $proxyCandidates = @(ConvertFrom-Json -InputObject $env:YTDLP_PROXY_POOL_JSON)
}
if (-not [string]::IsNullOrWhiteSpace($env:VIDEO_URL)) {
    $videoUri = $null
    if (-not [Uri]::TryCreate($env:VIDEO_URL, [UriKind]::Absolute, [ref]$videoUri)) {
        throw "VIDEO_URL must be an absolute YouTube video URL."
    }
    $allowedHosts = @("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")
    if ($allowedHosts -notcontains $videoUri.Host.ToLowerInvariant()) {
        throw "VIDEO_URL must use a supported YouTube host."
    }
    if ($videoUri.Host -eq "youtu.be") {
        $videoId = $videoUri.AbsolutePath.Trim("/")
    } elseif ($videoUri.AbsolutePath -match "^/(shorts|embed|live)/([A-Za-z0-9_-]{11})/?$") {
        $videoId = $Matches[2]
    } else {
        foreach ($part in $videoUri.Query.TrimStart("?").Split("&")) {
            $pair = $part -split "=", 2
            if ($pair[0] -eq "v" -and $pair.Count -eq 2) {
                $videoId = [Uri]::UnescapeDataString($pair[1])
                break
            }
        }
    }
    if ($videoId -notmatch "^[A-Za-z0-9_-]{11}$") {
        throw "VIDEO_URL does not contain a valid YouTube video ID."
    }
    $videoPage = "https://www.youtube.com/watch?v=$videoId"
    $metadataFormat = "%(id)s`t%(channel_id)s`t%(title)s"
    $metadataOutput = & yt-dlp --js-runtimes deno --no-warnings --skip-download --print $metadataFormat $videoPage 2>$null
    $metadataExitCode = $LASTEXITCODE
    if ($metadataExitCode -ne 0 -or $metadataOutput.Count -eq 0) {
        foreach ($proxy in $proxyCandidates) {
            $metadataOutput = & yt-dlp --proxy $proxy --js-runtimes deno --no-warnings --skip-download --print $metadataFormat $videoPage 2>$null
            $metadataExitCode = $LASTEXITCODE
            if ($metadataExitCode -eq 0 -and $metadataOutput.Count -gt 0) { break }
        }
    }
    if ($metadataExitCode -ne 0 -or $metadataOutput.Count -eq 0) {
        throw "Could not read metadata for requested video $videoId directly or through the bounded proxy set."
    }
    $metadataParts = "$($metadataOutput[0])" -split "`t", 3
    if ($metadataParts[0] -ne $videoId) {
        throw "yt-dlp returned metadata for a different video than $videoId."
    }
    if ($metadataParts.Count -lt 3 -or $metadataParts[1] -ne "UCYxRlFDqcWM4y7FfpiAN3KQ") {
        throw "Requested video $videoId is not published by the verified White House YouTube channel."
    }
    $videoTitle = if ($metadataParts[2]) { $metadataParts[2] } else { $videoId }
    Write-Host "Processing requested video as new: ${videoId}: $videoTitle"
    $videoTitle = $videoTitle.Replace("%", "%25").Replace("`r", "%0D").Replace("`n", "%0A")
    "video_id=$videoId" | Add-Content -Path $env:GITHUB_OUTPUT -Encoding utf8
    "video_title=$videoTitle" | Add-Content -Path $env:GITHUB_OUTPUT -Encoding utf8
    exit 0
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
