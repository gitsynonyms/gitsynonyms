$ErrorActionPreference = "Stop"

if ($env:GITHUB_REF -ne "refs/heads/main") {
    throw "The White House monitor must run from main to publish the site."
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$stateFile = Join-Path $repoRoot ".github\state\whitehouse-processed-videos.txt"
$processed = @()
if (Test-Path $stateFile) {
    foreach ($line in Get-Content $stateFile) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) { continue }
        if ($line.StartsWith("{")) {
            $record = $line | ConvertFrom-Json
            if ($record.source_platform -eq "youtube" -and $record.source_id) {
                $processed += [string]$record.source_id
            }
        } else {
            $processed += $line.Trim()
        }
    }
}
$videoId = ""
$channels = @(
    "https://www.youtube.com/@WhiteHouse/videos",
    "https://www.youtube.com/@WhiteHouse/streams"
)
$proxyCandidates = @()
if (-not [string]::IsNullOrWhiteSpace($env:YTDLP_PROXY_POOL_JSON)) {
    $proxyCandidates = @(ConvertFrom-Json -InputObject $env:YTDLP_PROXY_POOL_JSON)
}

function Get-YouTubeMetadata {
    param([Parameter(Mandatory = $true)][string]$Url)

    $jsonLines = & yt-dlp --js-runtimes deno --no-warnings --skip-download --dump-single-json $Url 2>$null
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0 -and $jsonLines.Count -gt 0) {
        return (($jsonLines -join [Environment]::NewLine) | ConvertFrom-Json)
    }
    foreach ($proxy in $proxyCandidates) {
        $jsonLines = & yt-dlp --proxy $proxy --js-runtimes deno --no-warnings --skip-download --dump-single-json $Url 2>$null
        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0 -and $jsonLines.Count -gt 0) {
            return (($jsonLines -join [Environment]::NewLine) | ConvertFrom-Json)
        }
    }
    throw "Could not read YouTube metadata directly or through the bounded proxy set."
}

function Write-VideoOutputs {
    param([Parameter(Mandatory = $true)]$Metadata)

    $script:videoId = [string]$Metadata.id
    $script:videoTitle = if ($Metadata.title) { [string]$Metadata.title } else { $script:videoId }
    $sourceChannel = if ($Metadata.channel) { [string]$Metadata.channel } elseif ($Metadata.uploader) { [string]$Metadata.uploader } else { "" }
    $sourceChannelId = if ($Metadata.channel_id) { [string]$Metadata.channel_id } else { "" }
    $publishedValue = $null
    $timeSource = ""
    if ($null -ne $Metadata.timestamp) {
        $publishedValue = $Metadata.timestamp
        $timeSource = "timestamp"
    } elseif ($null -ne $Metadata.release_timestamp) {
        $publishedValue = $Metadata.release_timestamp
        $timeSource = "release_timestamp"
    }
    $publishedAt = ""
    $publishedEpochNs = ""
    $publishedPrecision = "unknown"
    $publishedDate = ""
    if ($null -ne $publishedValue) {
        $culture = [Globalization.CultureInfo]::InvariantCulture
        $timestampText = [Convert]::ToString($publishedValue, $culture)
        $timestampDecimal = [decimal]::Parse(
            $timestampText,
            [Globalization.NumberStyles]::Float,
            $culture
        )
        $epochNs = [long][decimal]::Round(
            $timestampDecimal * 1000000000m,
            0,
            [MidpointRounding]::AwayFromZero
        )
        $wholeSeconds = [long][Math]::Floor($epochNs / 1000000000m)
        $fractionNs = [long]($epochNs - ($wholeSeconds * 1000000000L))
        $datePart = [DateTimeOffset]::FromUnixTimeSeconds($wholeSeconds).UtcDateTime.ToString(
            "yyyy-MM-dd'T'HH:mm:ss",
            $culture
        )
        $publishedAt = "$datePart.$($fractionNs.ToString('D9', $culture))Z"
        $publishedEpochNs = [string]$epochNs
        $publishedDate = $datePart.Substring(0, 10)
        $fraction = if ($timestampText.Contains(".")) { $timestampText.Split(".", 2)[1].TrimEnd("0") } else { "" }
        $fractionLength = $fraction.Length
        $publishedPrecision = if ($fractionLength -eq 0) { "second" } elseif ($fractionLength -le 3) { "millisecond" } elseif ($fractionLength -le 6) { "microsecond" } else { "nanosecond" }
    } elseif ($Metadata.upload_date -match "^\d{8}$") {
        $parsedDate = [DateTime]::ParseExact(
            [string]$Metadata.upload_date,
            "yyyyMMdd",
            [Globalization.CultureInfo]::InvariantCulture
        )
        $publishedDate = $parsedDate.ToString("yyyy-MM-dd", [Globalization.CultureInfo]::InvariantCulture)
        $timeSource = "upload_date"
        $publishedPrecision = "day"
    }
    $outputs = @{
        source_platform = "youtube"
        source_id = $script:videoId
        source_channel = $sourceChannel
        source_channel_id = $sourceChannelId
        source_title = $script:videoTitle
        source_url = "https://www.youtube.com/watch?v=$script:videoId"
        published_at_ns = $publishedAt
        published_epoch_ns = $publishedEpochNs
        published_date = $publishedDate
        published_time_source = $timeSource
        published_time_precision = $publishedPrecision
        video_id = $script:videoId
        video_title = $script:videoTitle
    }
    foreach ($name in $outputs.Keys) {
        $value = [string]$outputs[$name]
        $delimiter = [Guid]::NewGuid().ToString()
        Add-Content -Path $env:GITHUB_OUTPUT -Value "$name<<$delimiter" -Encoding utf8
        Add-Content -Path $env:GITHUB_OUTPUT -Value $value -Encoding utf8
        Add-Content -Path $env:GITHUB_OUTPUT -Value $delimiter -Encoding utf8
    }
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
    $metadata = Get-YouTubeMetadata -Url $videoPage
    if ($metadata.id -ne $videoId) {
        throw "yt-dlp returned metadata for a different video than $videoId."
    }
    if ($metadata.channel_id -ne "UCYxRlFDqcWM4y7FfpiAN3KQ") {
        throw "Requested video $videoId is not published by the verified White House YouTube channel."
    }
    Write-VideoOutputs -Metadata $metadata
    Write-Host "Processing requested video as new: $videoId"
    exit 0
}

$foundItems = 0
foreach ($channel in $channels) {
    $listing = & yt-dlp --js-runtimes deno --no-warnings --ignore-errors --flat-playlist --playlist-end 10 --print "%(id)s" $channel 2>$null
    $listingExitCode = $LASTEXITCODE
    if ($listingExitCode -ne 0 -and $proxyCandidates.Count -gt 0) {
        foreach ($proxy in $proxyCandidates) {
            $listing = & yt-dlp --proxy $proxy --js-runtimes deno --no-warnings --ignore-errors --flat-playlist --playlist-end 10 --print "%(id)s" $channel 2>$null
            $listingExitCode = $LASTEXITCODE
            if ($listingExitCode -eq 0 -and $listing.Count -gt 0) { break }
        }
    }
    if ($listingExitCode -ne 0 -and $listing.Count -eq 0) {
        throw "Could not inspect White House channel: $channel"
    }
    foreach ($line in $listing) {
        $candidate = "$line".Trim()
        if ($candidate -notmatch "^[A-Za-z0-9_-]{11}$") { continue }
        $foundItems++
        if ($env:FORCE_CHECK -ne "true" -and $processed -contains $candidate) { continue }
        $videoId = $candidate
        break
    }
    if ($videoId) { break }
}
if ($foundItems -eq 0) {
    throw "No video IDs were returned by either White House channel listing."
}

if ($videoId) {
    $metadata = Get-YouTubeMetadata -Url "https://www.youtube.com/watch?v=$videoId"
    if ($metadata.id -ne $videoId) {
        throw "yt-dlp returned metadata for a different video than $videoId."
    }
    Write-VideoOutputs -Metadata $metadata
    Write-Host "Discovered video: $videoId"
} else {
    Write-Host "No unprocessed uploads found in the latest channel entries."
}
