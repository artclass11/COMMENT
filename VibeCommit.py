#!/usr/bin/env python3
"""VibeCommit - voice-aware Conventional Commit generator.

Record up to 10 seconds from the default microphone, transcribe locally with
Whisper, inspect the staged Git diff, ask an OpenAI-compatible LLM endpoint for
one Conventional Commit line, and optionally create the commit.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_RECORD_SECONDS = 10
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_WHISPER_MODEL = "base"
DEFAULT_LLM_URL = "http://localhost:11434/v1/chat/completions"
DEFAULT_LLM_MODEL = "llama3.2:3b"
DEFAULT_MAX_DIFF_CHARS = 30_000
DEFAULT_TIMEOUT_SECONDS = 90
VERSION = "1.1.0"

CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(?P<type>feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(?:\((?P<scope>[A-Za-z0-9._/-]+)\))?"
    r"(?P<breaking>!)?: (?P<description>[^\r\n]{1,72})$"
)

SYSTEM_PROMPT = """You generate exactly one Conventional Commit subject line.

Rules:
- Return ONE line only. No markdown, no quotes, no explanation.
- Format: type(scope): imperative description
- Allowed types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert.
- Scope is optional.
- Keep the subject concise and <= 72 characters after the colon when possible.
- Use imperative mood (add, fix, remove, update), not past tense.
- Never invent changes not supported by the staged diff.
- The spoken summary is context; the staged diff is the source of truth.
"""


class VibeCommitError(RuntimeError):
    """Expected runtime error with a human-readable message."""


def eprint(message: str) -> None:
    print(f"VibeCommit: {message}", file=sys.stderr)


def run_command(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise VibeCommitError(f"Required command not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VibeCommitError(f"Command timed out: {' '.join(args)}") from exc


def ensure_git_repo() -> None:
    result = run_command(["git", "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        details = result.stderr.strip() or "not inside a Git repository"
        raise VibeCommitError(details)


def get_staged_diff(max_chars: int) -> str:
    ensure_git_repo()
    result = run_command(
        ["git", "diff", "--staged", "--no-ext-diff", "--no-color", "--submodule=short"],
        timeout=30,
    )
    if result.returncode != 0:
        raise VibeCommitError(result.stderr.strip() or "Unable to read staged changes.")

    diff = result.stdout.strip()
    if not diff:
        raise VibeCommitError("No staged changes found. Stage your files first with git add.")

    if len(diff) > max_chars:
        eprint(f"Staged diff is {len(diff):,} chars; truncating to {max_chars:,} chars for the LLM.")
        diff = diff[:max_chars] + "\n\n[diff truncated by VibeCommit]"
    return diff


def record_audio(seconds: float, sample_rate: int) -> Path:
    if seconds <= 0 or seconds > MAX_RECORD_SECONDS:
        raise VibeCommitError(f"Recording duration must be > 0 and <= {MAX_RECORD_SECONDS} seconds.")

    try:
        import sounddevice as sd
        from scipy.io.wavfile import write as wav_write
    except ImportError as exc:
        raise VibeCommitError(
            "Audio dependencies are missing. Install with: pip install -r requirements.txt"
        ) from exc

    frames = int(round(seconds * sample_rate))
    temp = tempfile.NamedTemporaryFile(prefix="vibecommit_", suffix=".wav", delete=False)
    wav_path = Path(temp.name)
    temp.close()

    try:
        print(f"\n🎙  Speak now — recording for up to {seconds:g}s", flush=True)
        print("    " + "-" * 24, flush=True)
        recording = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="int16")
        for remaining in range(int(seconds), 0, -1):
            print(f"    {remaining:2d}s", end="\r", flush=True)
            time.sleep(1)
        sd.wait()
        wav_write(str(wav_path), sample_rate, recording)
        print("    done.          ", flush=True)
        return wav_path
    except Exception as exc:  # sounddevice exposes platform-specific exceptions
        try:
            wav_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise VibeCommitError(f"Microphone recording failed: {exc}") from exc


def transcribe_with_whisper(wav_path: Path, model_name: str) -> str:
    try:
        import whisper
    except ImportError as exc:
        raise VibeCommitError(
            "Local Whisper is not installed. Run: pip install -r requirements.txt"
        ) from exc

    print(f"🧠 Transcribing locally with Whisper ({model_name})...", flush=True)
    try:
        model = whisper.load_model(model_name)
        result: dict[str, Any] = model.transcribe(
            str(wav_path),
            fp16=False,
            verbose=False,
            condition_on_previous_text=False,
        )
    except Exception as exc:
        raise VibeCommitError(f"Whisper transcription failed: {exc}") from exc

    text = " ".join(str(result.get("text", "")).split())
    if not text:
        raise VibeCommitError("Whisper returned an empty transcript. Try speaking more clearly.")
    return text


def build_user_prompt(transcript: str, diff: str) -> str:
    return f"""Spoken change summary:\n{transcript}\n\nStaged Git diff:\n```diff\n{diff}\n```\n\nGenerate exactly one Conventional Commit subject line."""


def extract_commit_line(raw: str) -> str:
    text = raw.strip()
    text = re.sub(r"^```(?:text|bash|git)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = text.replace("\r", "")

    candidates = []
    for line in text.split("\n"):
        candidate = line.strip().strip('"').strip("'").strip()
        candidate = re.sub(r"^(?:commit message|message)\s*:\s*", "", candidate, flags=re.I)
        if candidate:
            candidates.append(candidate)
            if CONVENTIONAL_COMMIT_RE.fullmatch(candidate):
                return candidate

    if candidates:
        return candidates[0][:200]
    raise VibeCommitError("LLM returned an empty response.")


def validate_commit_message(message: str) -> bool:
    match = CONVENTIONAL_COMMIT_RE.fullmatch(message)
    if not match:
        return False
    description = match.group("description").strip()
    return bool(description) and len(message) <= 100 and not description.endswith(".")


def call_llm(
    transcript: str,
    diff: str,
    *,
    url: str,
    model: str,
    api_key: str | None,
    timeout: int,
) -> str:
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 80,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(transcript, diff)},
        ],
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1_000]
        raise VibeCommitError(f"LLM endpoint returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise VibeCommitError(f"Could not reach LLM endpoint: {exc.reason}") from exc
    except TimeoutError as exc:
        raise VibeCommitError("LLM request timed out.") from exc

    try:
        data = json.loads(body)
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise VibeCommitError("Unexpected LLM response format; expected OpenAI-compatible chat completion JSON.") from exc

    message = extract_commit_line(str(content))
    if not validate_commit_message(message):
        raise VibeCommitError(
            f"LLM output is not a valid Conventional Commit line: {message!r}"
        )
    return message


def print_wave(message: str, cycles: int = 2) -> None:
    print("\n  ~~~ VibeCommit ~~~")
    for frame in ["·  ·  ·", "~  ·  ~", "≈  ~  ≈", "~  ≈  ~"] * cycles:
        print(f"  {frame}   {message}", end="\r", flush=True)
        time.sleep(0.08)
    print(" " * (len(message) + 24), end="\r")
    print(f"  ~  {message}  ~")


def confirm_and_commit(message: str) -> None:
    try:
        answer = input("\nCommit this change? [Y/n] ").strip().lower()
    except EOFError:
        answer = "n"

    if answer not in ("", "y", "yes"):
        print("Commit cancelled. Your working tree was not changed.")
        return

    result = run_command(["git", "commit", "-m", message], timeout=120)
    if result.returncode != 0:
        combined = (result.stderr or result.stdout).strip()
        raise VibeCommitError(f"git commit failed:\n{combined}")
    print(result.stdout.strip() or "Commit created successfully.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="VibeCommit",
        description="Generate a Conventional Commit from your voice summary and staged Git diff.",
    )
    parser.add_argument("--version", action="version", version=f"VibeCommit {VERSION}")\n    parser.add_argument("--seconds", type=float, default=10.0, help="Recording duration, max 10 seconds (default: 10).")
    parser.add_argument(
        "--text",
        metavar="INTENT",
        help="Use typed intent instead of microphone recording; useful for CI, servers, and quick tests.",
    )
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE, help="Microphone sample rate (default: 16000).")
    parser.add_argument("--whisper-model", default=os.getenv("VIBECOMMIT_WHISPER_MODEL", DEFAULT_WHISPER_MODEL), help="Whisper model (default: base).")
    parser.add_argument("--llm-url", default=os.getenv("VIBECOMMIT_LLM_URL", DEFAULT_LLM_URL), help="OpenAI-compatible chat-completions URL.")
    parser.add_argument("--llm-model", default=os.getenv("VIBECOMMIT_LLM_MODEL", DEFAULT_LLM_MODEL), help="LLM model name.")
    parser.add_argument("--max-diff-chars", type=int, default=int(os.getenv("VIBECOMMIT_MAX_DIFF_CHARS", DEFAULT_MAX_DIFF_CHARS)), help="Maximum staged diff characters sent to the LLM.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="LLM request timeout in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Generate and print the commit message without committing.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.getenv("VIBECOMMIT_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if args.max_diff_chars <= 0:
        raise VibeCommitError("--max-diff-chars must be positive.")
    if args.sample_rate < 8_000:
        raise VibeCommitError("--sample-rate must be at least 8000 Hz.")
    if args.timeout <= 0:
        raise VibeCommitError("--timeout must be positive.")

    wav_path: Path | None = None
    try:
        diff = get_staged_diff(args.max_diff_chars)
        if args.text is not None:
            transcript = " ".join(args.text.split())
            if not transcript:
                raise VibeCommitError("--text cannot be empty.")
            print(f"Intent: {transcript}")
        else:
            wav_path = record_audio(args.seconds, args.sample_rate)
            transcript = transcribe_with_whisper(wav_path, args.whisper_model)
            print(f"📝 Heard: {transcript}")
        commit_message = call_llm(
            transcript,
            diff,
            url=args.llm_url,
            model=args.llm_model,
            api_key=api_key,
            timeout=args.timeout,
        )
        print_wave(commit_message)

        if args.dry_run:
            print("\nDry run: no commit was created.")
            return 0

        confirm_and_commit(commit_message)
        return 0
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except VibeCommitError as exc:
        eprint(str(exc))
        return 1
    finally:
        if wav_path is not None:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())