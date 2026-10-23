#!/usr/bin/env python3
"""Module 2b-1 - third-party caption generation office.

Runs the same audio through third-party speech-to-text platforms, normalizes
every result to the repository's cue model, and compares it with the reference
track (the fused json3 timeline or a supplied SRT/VTT/ASS).

Adapters that are implemented end-to-end:

* AssemblyAI (upload + async transcript, word timestamps)
* Deepgram (Nova-3, utterances + words)
* Gladia (upload + async transcription, words)
* Rev AI (job upload + transcript elements)
* ElevenLabs Scribe v2 (multipart speech-to-text, words)
* OpenAI transcription (whisper-1 verbose JSON, word timestamps)

Platforms without an official/raw word-timestamp API are still in the registry,
with an explicit ``unsupported`` status instead of fabricated output: Otter.ai,
Descript, OpenRouter audio chat, Zoom Scribe, Speechmatics, Sonix, Trint, Happy
Scribe. Add keys through environment variables; a missing key is reported as
``skipped``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import subtitle_ergonomics  # noqa: E402
import subtitle_timing  # noqa: E402

PROVIDERS = {
    "assemblyai": {
        "name": "AssemblyAI",
        "env": "ASSEMBLYAI_API_KEY",
        "price_usd_per_hour": 0.21,
        "price_note": "$0.21/hr Universal-3.5 Pro; $0.15/hr Universal-2; $50 free credits",
        "languages": 18,
        "word_timestamps": True,
        "diarization": True,
        "adapter": "assemblyai",
        "source": "https://www.assemblyai.com/pricing",
    },
    "deepgram": {
        "name": "Deepgram",
        "env": "DEEPGRAM_API_KEY",
        "price_usd_per_hour": 0.288,
        "price_note": "Nova-3 $0.0048/min promotional ($0.288/hr); regular $0.0077/min; $200 credit",
        "languages": 45,
        "word_timestamps": True,
        "diarization": True,
        "adapter": "deepgram",
        "source": "https://deepgram.com/pricing",
    },
    "gladia": {
        "name": "Gladia",
        "env": "GLADIA_API_KEY",
        "price_usd_per_hour": 0.174,
        "price_note": "Solaria-3 async from EUR 0.16/hr (~$0.174); EUR 50 free; 100+ languages",
        "languages": 100,
        "word_timestamps": True,
        "diarization": True,
        "adapter": "gladia",
        "source": "https://www.gladia.io/pricing",
    },
    "revai": {
        "name": "Rev AI",
        "env": "REVAI_API_KEY",
        "price_usd_per_hour": 0.20,
        "price_note": "Reverb $0.20/hr English; $0.30/hr foreign; 5 free hours",
        "languages": 58,
        "word_timestamps": True,
        "diarization": True,
        "adapter": "revai",
        "source": "https://www.rev.ai/pricing",
    },
    "elevenlabs": {
        "name": "ElevenLabs Scribe v2",
        "env": "ELEVENLABS_API_KEY",
        "price_usd_per_hour": 0.22,
        "price_note": "Scribe v2; check current plan pricing; 90 languages",
        "languages": 90,
        "word_timestamps": True,
        "diarization": True,
        "adapter": "elevenlabs",
        "source": "https://elevenlabs.io/speech-to-text",
    },
    "openai": {
        "name": "OpenAI Whisper API",
        "env": "OPENAI_API_KEY",
        "price_usd_per_hour": 0.36,
        "price_note": "whisper-1 $0.006/min = $0.36/hr; word timestamps in verbose JSON",
        "languages": 99,
        "word_timestamps": True,
        "diarization": False,
        "adapter": "openai",
        "source": "https://platform.openai.com/docs/guides/speech-to-text",
    },
    "speechmatics": {
        "name": "Speechmatics",
        "env": "SPEECHMATICS_API_KEY",
        "price_usd_per_hour": 0.12,
        "price_note": "Melia 1 $0.12/hr; Enhanced $0.38/hr; $100 credits; 55+ languages",
        "languages": 55,
        "word_timestamps": True,
        "diarization": True,
        "adapter": None,
        "note": "Adapter pending; batch jobs API documents word-level timings.",
        "source": "https://www.speechmatics.com/pricing",
    },
    "sonix": {
        "name": "Sonix",
        "env": "SONIX_API_KEY",
        "price_usd_per_hour": 10.0,
        "price_note": "Pay as you go $10/hr; 30 free minutes; 54+ languages",
        "languages": 54,
        "word_timestamps": True,
        "diarization": True,
        "adapter": None,
        "note": "REST API exists; adapter pending.",
        "source": "https://sonix.ai/pricing",
    },
    "trint": {
        "name": "Trint",
        "env": "TRINT_API_KEY",
        "price_usd_per_hour": None,
        "price_note": "API available on many plans; Enterprise for high volume",
        "languages": 40,
        "word_timestamps": True,
        "diarization": True,
        "adapter": None,
        "note": "API exists; pricing depends on plan; adapter pending.",
        "source": "https://developer.trint.com/docs/pricing/",
    },
    "happyscribe": {
        "name": "Happy Scribe",
        "env": "HAPPYSCRIBE_API_KEY",
        "price_usd_per_hour": None,
        "price_note": "Subscription/credits; API orders accept a URL and export SRT/VTT",
        "languages": 120,
        "word_timestamps": True,
        "diarization": True,
        "adapter": None,
        "note": "Orders API implemented by vendor; adapter pending.",
        "source": "https://dev.happyscribe.com/",
    },
    "otter": {
        "name": "Otter.ai",
        "env": "OTTER_API_KEY",
        "price_usd_per_hour": None,
        "price_note": "No official self-serve transcription API found; OtterAI/speech-v3.1 ranks 3.88% WER",
        "languages": 1,
        "word_timestamps": None,
        "diarization": True,
        "adapter": None,
        "note": "No official public API; browser automation is not a reliable API substitute.",
        "source": "https://otter.ai/",
    },
    "descript": {
        "name": "Descript",
        "env": "DESCRIPT_API_TOKEN",
        "price_usd_per_hour": None,
        "price_note": "API access included for paying plans; usage draws AI credits/media hours",
        "languages": 30,
        "word_timestamps": True,
        "diarization": True,
        "adapter": None,
        "note": "Workflow/Underlord API, not a raw word-timestamp STT endpoint.",
        "source": "https://www.descript.com/api",
    },
    "openrouter": {
        "name": "OpenRouter (audio chat)",
        "env": "OPENROUTER_API_KEY",
        "price_usd_per_hour": None,
        "price_note": "Token-priced multimodal chat; no dedicated STT word-timing endpoint",
        "languages": None,
        "word_timestamps": False,
        "diarization": False,
        "adapter": None,
        "note": "Audio input is supported via chat completions, but it is not a WER-comparable STT service and returns no word timestamps.",
        "source": "https://openrouter.ai/docs/features/multimodal/audio",
    },
    "zoom": {
        "name": "Zoom Scribe",
        "env": "ZOOM_API_KEY",
        "price_usd_per_hour": None,
        "price_note": "Proprietary; 4.15% / 4.77% WER on the leaderboard",
        "languages": 11,
        "word_timestamps": None,
        "diarization": True,
        "adapter": None,
        "note": "No public raw STT API advertised; leaderboard result only.",
        "source": "https://huggingface.co/spaces/hf-audio/open_asr_leaderboard",
    },
}

MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}


class ProviderError(RuntimeError):
    """Raised when a provider returns an error or unusable payload."""


def _request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    json_body: dict | None = None,
    timeout: float = 180.0,
) -> dict:
    """Minimal JSON HTTP client using the standard library."""
    body = data
    request_headers = dict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        url,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", "replace")[:800]
        raise ProviderError(f"HTTP {error.code} from {url}: {details}") from error
    except urllib.error.URLError as error:
        raise ProviderError(f"Cannot reach {url}: {error.reason}") from error
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        text = payload.decode("utf-8", "replace")[:500]
        raise ProviderError(
            f"Expected JSON from {url} (content-type {content_type!r}): {text}"
        ) from error


def _multipart(
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes, str]],
) -> tuple[bytes, str]:
    boundary = "----gitsynonyms" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            + str(value).encode()
            + b"\r\n"
        )
    for name, (filename, content, content_type) in files.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
            + content
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _mime(path: Path) -> str:
    return MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")


def prepare_audio(
    *,
    audio: Path | None,
    url: str | None,
    work_dir: Path,
    limit_seconds: float | None = None,
) -> Path:
    """Return a local audio file, downloading a URL when necessary."""
    work_dir.mkdir(parents=True, exist_ok=True)
    if audio is None and url is None:
        raise ValueError("Provide --audio PATH or --url URL.")
    source = audio
    if source is None:
        template = work_dir / "source.%(ext)s"
        command = [
            "yt-dlp",
            "--no-warnings",
            "-x",
            "--audio-format",
            "m4a",
            "--audio-quality",
            "5",
            "-o",
            str(template),
            str(url),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError as error:
            raise RuntimeError("yt-dlp is required to download a URL but was not found.") from error
        except subprocess.CalledProcessError as error:
            details = (error.stderr or error.stdout or "").strip()[-800:]
            raise RuntimeError(f"yt-dlp could not download the audio: {details}") from error
        matches = sorted(work_dir.glob("source.*"))
        if not matches:
            raise RuntimeError("yt-dlp produced no audio file.")
        source = matches[0]
    source = source.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Audio file not found: {source}")
    if limit_seconds is None or limit_seconds <= 0:
        return source
    target = work_dir / f"excerpt{source.suffix or '.m4a'}"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-t",
        f"{limit_seconds:.3f}",
        "-i",
        str(source),
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(target),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except FileNotFoundError as error:
        raise RuntimeError("ffmpeg is required to trim the audio excerpt.") from error
    except subprocess.CalledProcessError as error:
        details = (error.stderr or b"").decode("utf-8", "replace")[-800:]
        raise RuntimeError(f"ffmpeg could not trim the audio: {details}") from error
    return target


def _join_words(words: Sequence[str]) -> str:
    if words and all(re.search(r"[\u3400-\u9fff]", word) for word in words):
        return "".join(words)
    return " ".join(words)


def cue_from_words(
    words: Sequence[dict],
    *,
    max_gap: float = 0.8,
    max_chars: int = 42,
    cjk_max_chars: int = 16,
    max_duration: float = 7.0,
) -> list[dict]:
    """Group normalized words into readable cue-sized lines."""
    ordered = sorted(
        (
            {
                "text": str(word["text"]).strip(),
                "start": float(word["start"]),
                "end": float(word["end"]),
            }
            for word in words
            if str(word.get("text", "")).strip()
        ),
        key=lambda word: (word["start"], word["end"]),
    )
    cues: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if not current:
            return
        text = _join_words([word["text"] for word in current]).strip()
        if text:
            cues.append(
                {
                    "start": round(current[0]["start"], 3),
                    "end": round(max(word["end"] for word in current), 3),
                    "text": text,
                    "words": [dict(word) for word in current],
                }
            )
        current.clear()

    for word in ordered:
        if current:
            gap = word["start"] - current[-1]["end"]
            projected = _join_words([item["text"] for item in [*current, word]])
            limit = cjk_max_chars if re.search(r"[\u3400-\u9fff]", projected) else max_chars
            too_long = len(projected) > limit
            too_slow = word["end"] - current[0]["start"] > max_duration
            if gap > max_gap or too_long or too_slow:
                flush()
        current.append(word)
        if re.search(r"[.?!\u3002\uff01\uff1f]$", word["text"]):
            flush()
    flush()
    return cues


def flatten_words(cues: Sequence[dict]) -> list[dict]:
    words = []
    for cue in cues:
        for word in cue.get("words") or []:
            words.append(
                {
                    "text": word["text"],
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                }
            )
    return sorted(words, key=lambda word: word["start"])


def _poll(
    fetch: Callable[[], dict],
    *,
    timeout: float,
    interval: float,
    done: set[str],
    failed: set[str],
) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        data = fetch()
        status = str(data.get("status") or data.get("state") or "").lower()
        if status in done:
            return data
        if status in failed:
            raise ProviderError(f"Provider job failed: {data}")
        if time.monotonic() >= deadline:
            raise ProviderError(f"Provider job timed out after {timeout:.0f} s (status {status!r}).")
        time.sleep(max(0.0, interval))


def _words_from_seconds(items: Sequence[dict], text_keys: Sequence[str] = ("text", "word")) -> list[dict]:
    words = []
    for item in items:
        text = next((str(item[key]) for key in text_keys if item.get(key)), "")
        if not text.strip():
            continue
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        words.append({"text": text.strip(), "start": start, "end": end})
    return words


def _cues_from_utterances(utterances: Sequence[dict]) -> list[dict] | None:
    cues = []
    for utterance in utterances:
        words = _words_from_seconds(utterance.get("words") or [])
        if not words:
            continue
        cues.append(
            {
                "start": round(words[0]["start"], 3),
                "end": round(max(word["end"] for word in words), 3),
                "text": _join_words([word["text"] for word in words]),
                "words": words,
            }
        )
    return cues or None


def run_assemblyai(audio: Path, key: str, language: str, timeout: float, interval: float) -> dict:
    upload = _request(
        "https://api.assemblyai.com/v2/upload",
        method="POST",
        headers={"authorization": key, "Content-Type": _mime(audio)},
        data=audio.read_bytes(),
    )
    job = _request(
        "https://api.assemblyai.com/v2/transcript",
        method="POST",
        headers={"authorization": key},
        json_body={
            "audio_url": upload["upload_url"],
            "speech_models": ["universal-3-5-pro"],
            "language_code": language,
            "punctuate": True,
            "format_text": True,
            "utterances": True,
        },
    )
    job_id = job["id"]
    data = _poll(
        lambda: _request(
            f"https://api.assemblyai.com/v2/transcript/{job_id}",
            headers={"authorization": key},
        ),
        timeout=timeout,
        interval=interval,
        done={"completed"},
        failed={"error"},
    )
    def millisecond_words(items):
        return [
            {
                "text": str(item.get("text", "")).strip(),
                "start": float(item.get("start", 0)) / 1000,
                "end": float(item.get("end", 0)) / 1000,
            }
            for item in items
            if str(item.get("text", "")).strip()
        ]

    words = millisecond_words(data.get("words") or [])
    utterances = [
        {**utterance, "words": millisecond_words(utterance.get("words") or [])}
        for utterance in data.get("utterances") or []
    ]
    if not words and utterances:
        words = [word for utterance in utterances for word in utterance.get("words") or []]
    cues = _cues_from_utterances(utterances) or cue_from_words(words)
    return {"cues": cues, "words": words, "model": "universal-3-5-pro"}


def run_deepgram(audio: Path, key: str, language: str, timeout: float, interval: float) -> dict:
    query = (
        "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true"
        "&punctuate=true&utterances=true&language=" + urllib.parse.quote(language)
    )
    data = _request(
        query,
        method="POST",
        headers={"Authorization": f"Token {key}", "Content-Type": _mime(audio)},
        data=audio.read_bytes(),
        timeout=timeout,
    )
    results = (data.get("results") or {})
    utterances = results.get("utterances") or []
    words_payload = []
    if utterances:
        words_payload = [word for utterance in utterances for word in utterance.get("words") or []]
    else:
        channels = results.get("channels") or []
        if channels:
            alternatives = channels[0].get("alternatives") or []
            if alternatives:
                words_payload = alternatives[0].get("words") or []
    words = _words_from_seconds(words_payload)
    cues = _cues_from_utterances(utterances) or cue_from_words(words)
    return {"cues": cues, "words": words, "model": "nova-3"}


def run_gladia(audio: Path, key: str, language: str, timeout: float, interval: float) -> dict:
    upload = _request(
        "https://api.gladia.io/v2/upload",
        method="POST",
        headers={"x-gladia-key": key, "Content-Type": _mime(audio)},
        data=audio.read_bytes(),
    )
    job = _request(
        "https://api.gladia.io/v2/transcription",
        method="POST",
        headers={"x-gladia-key": key},
        json_body={
            "audio_url": upload["audio_url"],
            "language_config": {"languages": [language]},
            "options": {"punctuation": True, "word_timestamps": True},
        },
    )
    job_id = job["id"]
    data = _poll(
        lambda: _request(
            f"https://api.gladia.io/v2/transcription/{job_id}",
            headers={"x-gladia-key": key},
        ),
        timeout=timeout,
        interval=interval,
        done={"done"},
        failed={"error", "failed"},
    )
    result = ((data.get("result") or {}).get("transcription") or {})
    utterances = result.get("utterances") or []
    words = _words_from_seconds(
        [word for utterance in utterances for word in utterance.get("words") or []],
        text_keys=("word", "text"),
    )
    cues = _cues_from_utterances(utterances) or cue_from_words(words)
    return {"cues": cues, "words": words, "model": "solaria-3"}


def run_revai(audio: Path, key: str, language: str, timeout: float, interval: float) -> dict:
    config = json.dumps({"language": language, "transcriber": "reverb", "skip_diarization": False})
    body, content_type = _multipart(
        {"config": config},
        {"media": (audio.name, audio.read_bytes(), _mime(audio))},
    )
    job = _request(
        "https://api.rev.ai/speechtotext/v1/jobs",
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": content_type},
        data=body,
    )
    job_id = job["id"]
    _poll(
        lambda: _request(
            f"https://api.rev.ai/speechtotext/v1/jobs/{job_id}",
            headers={"Authorization": f"Bearer {key}"},
        ),
        timeout=timeout,
        interval=interval,
        done={"completed"},
        failed={"failed"},
    )
    transcript = _request(
        f"https://api.rev.ai/speechtotext/v1/jobs/{job_id}/transcript",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/vnd.rev.transcript.v1.0+json",
        },
    )
    words = []
    for monologue in transcript.get("monologues") or []:
        for element in monologue.get("elements") or []:
            if element.get("type") != "text" or not str(element.get("value", "")).strip():
                continue
            words.append(
                {
                    "text": str(element["value"]).strip(),
                    "start": float(element["ts"]),
                    "end": float(element["end_ts"]),
                }
            )
    return {"cues": cue_from_words(words), "words": words, "model": "reverb"}


def run_elevenlabs(audio: Path, key: str, language: str, timeout: float, interval: float) -> dict:
    body, content_type = _multipart(
        {"model_id": "scribe_v2", "language_code": language},
        {"file": (audio.name, audio.read_bytes(), _mime(audio))},
    )
    data = _request(
        "https://api.elevenlabs.io/v1/speech-to-text",
        method="POST",
        headers={"xi-api-key": key, "Content-Type": content_type},
        data=body,
        timeout=timeout,
    )
    payload_words = [
        word
        for word in data.get("words") or []
        if str(word.get("type", "word")) == "word"
        and str(word.get("text", "")).strip()
    ]
    words = _words_from_seconds(payload_words, text_keys=("text", "word"))
    return {"cues": cue_from_words(words), "words": words, "model": "scribe_v2"}


def run_openai(audio: Path, key: str, language: str, timeout: float, interval: float) -> dict:
    body, content_type = _multipart(
        {
            "model": "whisper-1",
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
            "language": language,
        },
        {"file": (audio.name, audio.read_bytes(), _mime(audio))},
    )
    data = _request(
        "https://api.openai.com/v1/audio/transcriptions",
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": content_type},
        data=body,
        timeout=timeout,
    )
    words = _words_from_seconds(data.get("words") or [], text_keys=("word", "text"))
    segments = data.get("segments") or []
    cues = []
    for segment in segments:
        segment_words = [
            word
            for word in words
            if float(segment["start"]) <= word["start"] < float(segment["end"])
        ]
        cues.append(
            {
                "start": round(float(segment["start"]), 3),
                "end": round(float(segment["end"]), 3),
                "text": str(segment.get("text", "")).strip(),
                "words": segment_words,
            }
        )
    return {
        "cues": [cue for cue in cues if cue["text"]] or cue_from_words(words),
        "words": words,
        "model": "whisper-1",
    }


ADAPTERS = {
    "assemblyai": run_assemblyai,
    "deepgram": run_deepgram,
    "gladia": run_gladia,
    "revai": run_revai,
    "elevenlabs": run_elevenlabs,
    "openai": run_openai,
}


TOKEN = re.compile(r"[a-z0-9']+|[\u3400-\u9fff]")


def tokenize(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, ref_token in enumerate(reference, 1):
        current = [row]
        for column, hyp_token in enumerate(hypothesis, 1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (ref_token != hyp_token),
                )
            )
        previous = current
    return previous[-1]


def word_error_rate(reference: Sequence[str], hypothesis: Sequence[str]) -> float:
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return edit_distance(reference, hypothesis) / len(reference)


def _cue_words(cues: Sequence[dict]) -> list[dict]:
    words = []
    for cue in cues:
        if cue.get("words"):
            words.extend(
                {
                    "text": str(word["text"]),
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                }
                for word in cue["words"]
            )
        else:
            tokens = tokenize(str(cue.get("text", "")))
            for token in tokens:
                words.append(
                    {"text": token, "start": float(cue["start"]), "end": float(cue["end"])}
                )
    return sorted(words, key=lambda word: word["start"])


def alignment_offsets(reference_words: Sequence[dict], hypothesis_words: Sequence[dict]) -> list[float]:
    """Pair words through a token alignment and return start-time deltas (seconds)."""
    from difflib import SequenceMatcher

    ref_tokens = [token for word in reference_words for token in tokenize(word["text"])]
    hyp_tokens = [token for word in hypothesis_words for token in tokenize(word["text"])]
    ref_by_token = [
        word for word in reference_words for _ in tokenize(word["text"])
    ]
    hyp_by_token = [
        word for word in hypothesis_words for _ in tokenize(word["text"])
    ]
    offsets = []
    for block in SequenceMatcher(None, ref_tokens, hyp_tokens, autojunk=False).get_matching_blocks():
        for index in range(block.size):
            if block.a + index < len(ref_by_token) and block.b + index < len(hyp_by_token):
                offsets.append(
                    abs(
                        float(hyp_by_token[block.b + index]["start"])
                        - float(ref_by_token[block.a + index]["start"])
                    )
                )
    return offsets


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def load_reference(
    reference: Path,
    *,
    language: str,
    video_id: str | None,
) -> dict:
    """Load the fused json3 timeline or a prepared subtitle as the reference."""
    if reference.is_dir():
        if not video_id:
            raise ValueError("--video-id is required when --reference is a caption folder.")
        track = subtitle_timing.build_tracks(reference, video_id, [language])[language]
        cues = []
        for cue in track["cues"]:
            cues.append(
                {
                    "start": subtitle_timing.to_seconds(cue["start_ns"]),
                    "end": subtitle_timing.to_seconds(cue["end_ns"]),
                    "text": cue["text"],
                    "words": [
                        {
                            "text": word["text"],
                            "start": subtitle_timing.to_seconds(word["start_ns"]),
                            "end": subtitle_timing.to_seconds(word["end_ns"]),
                        }
                        for word in cue.get("word_timings") or []
                    ],
                }
            )
        return {
            "kind": "fused-caption-folder",
            "path": str(reference),
            "formats": track["formats_used"],
            "cues": cues,
            "words": _cue_words(cues),
        }
    cues = subtitle_ergonomics.load_cues(reference)
    return {
        "kind": "subtitle-file",
        "path": str(reference),
        "formats": [reference.suffix.lower().lstrip(".")],
        "cues": cues,
        "words": _cue_words(cues),
    }


def _srt_timestamp(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def write_srt(cues: Sequence[dict], path: Path) -> None:
    blocks = []
    for index, cue in enumerate(cues, 1):
        blocks.append(
            f"{index}\n{_srt_timestamp(cue['start'])} --> {_srt_timestamp(cue['end'])}\n"
            f"{cue['text']}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def compare_provider(
    slug: str,
    result: dict,
    reference: dict | None,
    *,
    duration: float,
    language: str,
) -> dict:
    cues = result["cues"]
    words = result.get("words") or _cue_words(cues)
    rules = subtitle_ergonomics.rules_for(language)
    ergonomics = subtitle_ergonomics.analyze_cues(cues, rules)
    record = {
        "name": PROVIDERS[slug]["name"],
        "status": "compared" if reference else "generated",
        "model": result.get("model"),
        "cues": len(cues),
        "words": len(words),
        "mean_cps": ergonomics["summary"]["mean_cps"],
        "max_cps": ergonomics["summary"]["max_cps"],
        "ergonomics_errors": ergonomics["summary"]["errors"],
        "duration_seconds": round(duration, 3),
        "cost_usd": (
            round(duration / 3600 * PROVIDERS[slug]["price_usd_per_hour"], 4)
            if PROVIDERS[slug].get("price_usd_per_hour") is not None
            else None
        ),
    }
    if reference:
        ref_tokens = [token for word in reference["words"] for token in tokenize(word["text"])]
        hyp_tokens = [token for word in words for token in tokenize(word["text"])]
        offsets = alignment_offsets(reference["words"], words)
        record.update(
            {
                "reference_words": len(ref_tokens),
                "wer_percent": round(word_error_rate(ref_tokens, hyp_tokens) * 100, 2),
                "timestamp_median_offset_ms": round(_median(offsets) * 1000, 1),
                "timestamp_mean_offset_ms": round(
                    (sum(offsets) / len(offsets) * 1000) if offsets else 0.0, 1
                ),
                "matched_words": len(offsets),
            }
        )
    return record


def probe_duration(path: Path) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        str(path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return float(result.stdout.strip())
    except (OSError, ValueError, subprocess.CalledProcessError):
        return 0.0


def _selected_providers(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(PROVIDERS)
    selected = [item.strip().lower() for item in value.split(",") if item.strip()]
    unknown = [item for item in selected if item not in PROVIDERS]
    if unknown:
        raise ValueError(f"Unknown provider(s): {', '.join(unknown)}")
    return selected


def _markdown(report: dict) -> str:
    lines = [
        "# Third-party caption comparison",
        "",
        f"Reference: `{report.get('reference', {}).get('path', 'none')}` · "
        f"language `{report['language']}` · audio {report['duration_seconds']:.1f} s",
        "",
        "| Provider | Status | WER | Timestamp median | Cues | Mean CPS | Cost |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for record in report["providers"].values():
        wer = f"{record['wer_percent']:.2f}%" if record.get("wer_percent") is not None else "—"
        offset = (
            f"{record['timestamp_median_offset_ms']:.0f} ms"
            if record.get("timestamp_median_offset_ms") is not None
            else "—"
        )
        cost = f"${record['cost_usd']:.3f}" if record.get("cost_usd") is not None else "—"
        lines.append(
            f"| {record['name']} | {record['status']} | {wer} | {offset} | "
            f"{record.get('cues', '—')} | {record.get('mean_cps', '—')} | {cost} |"
        )
    lines += [
        "",
        "WER is computed after lowercasing/punctuation removal; timestamp offset is the "
        "median absolute start difference of aligned words.",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate captions with third-party platforms and compare them to a reference."
    )
    parser.add_argument("--audio", type=Path, help="Local audio/video file.")
    parser.add_argument("--url", help="YouTube or media URL; downloaded with yt-dlp when keys exist.")
    parser.add_argument("--reference", type=Path, help="Caption folder or SRT/VTT/ASS reference.")
    parser.add_argument("--video-id", help="Video id for a caption-folder reference.")
    parser.add_argument("--language", default="en", help="Language code (default: en).")
    parser.add_argument(
        "--providers",
        default="all",
        help="Comma-separated provider slugs, or all (default: all).",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=ROOT / "work" / "thirdparty",
        help="Output directory (default: work/thirdparty).",
    )
    parser.add_argument("--limit-seconds", type=float, help="Send only an excerpt (cost control).")
    parser.add_argument("--timeout", type=float, default=900.0, help="Job timeout in seconds.")
    parser.add_argument("--poll-interval", type=float, default=3.0, help="Polling interval in seconds.")
    parser.add_argument("--json", action="store_true", help="Print the JSON report.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        selected = _selected_providers(args.providers)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    work_dir = args.work_dir.expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "module": "module2b-third-party-captions",
        "language": args.language,
        "duration_seconds": 0.0,
        "reference": {},
        "providers": {},
        "ranking": [],
    }
    reference = None
    runnable = []
    for slug in selected:
        meta = PROVIDERS[slug]
        key = os.environ.get(meta["env"], "").strip()
        if meta["adapter"] not in ADAPTERS:
            report["providers"][slug] = {
                "name": meta["name"],
                "status": "unsupported",
                "reason": meta.get("note") or "No raw word-timestamp API adapter.",
                "source": meta["source"],
            }
        elif not key:
            report["providers"][slug] = {
                "name": meta["name"],
                "status": "skipped",
                "reason": f"{meta['env']} is not set.",
                "source": meta["source"],
            }
        else:
            runnable.append(slug)
    if runnable:
        try:
            audio = prepare_audio(
                audio=args.audio,
                url=args.url,
                work_dir=work_dir,
                limit_seconds=args.limit_seconds,
            )
            duration = probe_duration(audio)
            report["audio"] = str(audio)
            report["duration_seconds"] = round(duration, 3)
            if args.reference:
                reference = load_reference(
                    args.reference,
                    language=args.language,
                    video_id=args.video_id,
                )
                if args.limit_seconds:
                    reference["cues"] = [
                        cue for cue in reference["cues"] if float(cue["start"]) < args.limit_seconds
                    ]
                    reference["words"] = [
                        word for word in reference["words"] if float(word["start"]) < args.limit_seconds
                    ]
                report["reference"] = {
                    key: reference[key]
                    for key in ("kind", "path", "formats")
                }
                report["reference"]["cues"] = len(reference["cues"])
                report["reference"]["words"] = len(reference["words"])
            for slug in runnable:
                meta = PROVIDERS[slug]
                try:
                    result = ADAPTERS[slug](
                        audio,
                        os.environ[meta["env"]],
                        args.language,
                        args.timeout,
                        args.poll_interval,
                    )
                    provider_dir = work_dir / slug
                    provider_dir.mkdir(parents=True, exist_ok=True)
                    write_srt(result["cues"], provider_dir / f"{slug}.srt")
                    (provider_dir / f"{slug}.json").write_text(
                        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    record = compare_provider(
                        slug,
                        result,
                        reference,
                        duration=duration,
                        language=args.language,
                    )
                    record["source"] = meta["source"]
                    record["output"] = str(provider_dir)
                    report["providers"][slug] = record
                except Exception as error:  # noqa: BLE001 - one provider must not stop the bench
                    report["providers"][slug] = {
                        "name": meta["name"],
                        "status": "error",
                        "reason": str(error),
                        "source": meta["source"],
                    }
        except Exception as error:  # noqa: BLE001 - audio/reference preparation
            for slug in runnable:
                report["providers"][slug] = {
                    "name": PROVIDERS[slug]["name"],
                    "status": "error",
                    "reason": str(error),
                    "source": PROVIDERS[slug]["source"],
                }
    ranked = [
        (record.get("wer_percent"), slug)
        for slug, record in report["providers"].items()
        if record.get("wer_percent") is not None
    ]
    report["ranking"] = [slug for _, slug in sorted(ranked)]
    (work_dir / "COMPARISON.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (work_dir / "COMPARISON.md").write_text(_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(_markdown(report))
        print(f"Report: {work_dir / 'COMPARISON.json'}")
    return 1 if any(item.get("status") == "error" for item in report["providers"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
