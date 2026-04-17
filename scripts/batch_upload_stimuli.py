"""Batch-convert and upload MusicXML stimuli to a webeval experiment.

Given a source tree shaped as::

    <source>/
        <condition>/
            <prompt>/
                prompt.txt
                piece1.musicxml
                piece2.musicxml
            ...

the script renders each ``*.musicxml`` file to MP3 with the bundled MuseScore
AppImage, trims the result to N seconds with ffmpeg, and uploads each clip as
a ``Stimulus`` attached to the named experiment. Condition folders map to
``Condition.name``; prompt folders map to ``Stimulus.prompt_group``; the
contents of ``prompt.txt`` go into ``Stimulus.description``; the MusicXML
filename stem becomes ``Stimulus.title``.

The target experiment must be in DRAFT state (enforced by the API). Missing
conditions are auto-created. Re-running the script on the same source tree
is safe: duplicates (same SHA-256) are skipped server-side.

Usage::

    # One-time token setup on the server:
    uv run ./manage.py drf_create_token <staff-username>

    # Then, from the client:
    uv run --group scripts python scripts/batch_upload_stimuli.py \\
        --source ~/Scrivania/generated \\
        --experiment my-study \\
        --duration 20 \\
        --token <token>        # or set WEBEVAL_API_TOKEN
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests

DEFAULT_MUSESCORE = "./bin/MuseScore-3.6.2.548021370-x86_64.AppImage"


@dataclass
class Args:
    source: Path
    experiment: str
    api_url: str
    token: str
    duration: int
    musescore: Path
    workdir: Path | None
    keep_converted: bool
    dry_run: bool


def parse_args(argv: list[str] | None = None) -> Args:
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Mint a token on the server with\n"
            "  uv run ./manage.py drf_create_token <staff-username>\n"
            "then pass it via --token or the WEBEVAL_API_TOKEN env var."
        ),
    )
    p.add_argument("--source", type=Path, required=True,
                   help="Root folder organised as <condition>/<prompt>/*.musicxml.")
    p.add_argument("--experiment", required=True,
                   help="Target experiment slug (must be in DRAFT state).")
    p.add_argument("--api-url", default="http://127.0.0.1:8000",
                   help="Base URL of the webeval webapp (default: %(default)s).")
    p.add_argument("--token", default=os.environ.get("WEBEVAL_API_TOKEN", ""),
                   help="DRF auth token. Defaults to $WEBEVAL_API_TOKEN.")
    p.add_argument("--duration", type=int, default=20,
                   help="Trim each clip to this many seconds (default: %(default)s).")
    p.add_argument("--musescore", type=Path, default=Path(DEFAULT_MUSESCORE),
                   help="Path to the MuseScore binary/AppImage.")
    p.add_argument("--workdir", type=Path, default=None,
                   help="Directory for intermediate files. Defaults to a temp dir.")
    p.add_argument("--keep-converted", action="store_true",
                   help="Don't delete the workdir on exit (for debugging).")
    p.add_argument("--dry-run", action="store_true",
                   help="Convert + trim locally, but do not upload.")
    ns = p.parse_args(argv)
    return Args(
        source=ns.source.expanduser().resolve(),
        experiment=ns.experiment,
        api_url=ns.api_url.rstrip("/"),
        token=ns.token,
        duration=ns.duration,
        musescore=ns.musescore.expanduser().resolve(),
        workdir=ns.workdir.expanduser().resolve() if ns.workdir else None,
        keep_converted=ns.keep_converted,
        dry_run=ns.dry_run,
    )


@dataclass
class StimulusJob:
    condition: str
    prompt_group: str
    prompt_text: str
    musicxml: Path
    title: str


def discover_jobs(source: Path) -> list[StimulusJob]:
    """Walk ``source`` and enumerate one StimulusJob per MusicXML file.

    Folders that don't fit the ``<source>/<condition>/<prompt>/`` shape are
    skipped with a warning rather than raising — matches the server's
    "skip don't fail" contract for duplicates.
    """
    if not source.is_dir():
        sys.exit(f"error: --source {source} is not a directory")

    jobs: list[StimulusJob] = []
    for condition_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        for prompt_dir in sorted(p for p in condition_dir.iterdir() if p.is_dir()):
            prompt_file = prompt_dir / "prompt.txt"
            musicxml_files = sorted(prompt_dir.glob("*.musicxml"))
            if not musicxml_files:
                print(f"[warn] {prompt_dir}: no .musicxml files; skipping", file=sys.stderr)
                continue
            prompt_text = ""
            if prompt_file.is_file():
                prompt_text = prompt_file.read_text(encoding="utf-8").strip()
            else:
                print(f"[warn] {prompt_dir}: no prompt.txt; description will be empty",
                      file=sys.stderr)
            for xml in musicxml_files:
                jobs.append(StimulusJob(
                    condition=condition_dir.name,
                    prompt_group=prompt_dir.name,
                    prompt_text=prompt_text,
                    musicxml=xml,
                    title=xml.stem,
                ))
    return jobs


def render_mp3(musescore: Path, musicxml: Path, out_mp3: Path) -> None:
    """Render MusicXML to MP3 using MuseScore in headless mode.

    ``QT_QPA_PLATFORM=offscreen`` is required on machines without a live
    display session (servers, CI, SSH shells). The AppImage honours the
    standard MuseScore 3 CLI: ``-o <out> <in>`` drives format by extension.
    """
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    subprocess.run(
        [str(musescore), "-o", str(out_mp3), str(musicxml)],
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def trim_mp3(in_mp3: Path, out_mp3: Path, duration: int) -> None:
    """Trim ``in_mp3`` to ``duration`` seconds and re-encode with libmp3lame.

    Stream-copy (``-c copy``) can produce glitchy cuts because MP3 frames
    don't always align with arbitrary timestamps; re-encoding is slower but
    reliable and 20-second clips are tiny.
    """
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(in_mp3),
            "-t", str(duration),
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(out_mp3),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def upload_stimulus(
    api_url: str,
    token: str,
    experiment_slug: str,
    job: StimulusJob,
    mp3_path: Path,
) -> tuple[str, dict]:
    """POST the trimmed MP3 to the API. Returns (outcome, body).

    Outcome is one of ``created``, ``skipped``, ``error``.
    """
    url = f"{api_url}/api/v1/experiments/{experiment_slug}/stimuli/"
    headers = {"Authorization": f"Token {token}"}
    with mp3_path.open("rb") as fh:
        files = {"audio": (mp3_path.name, fh, "audio/mpeg")}
        data = {
            "condition": job.condition,
            "prompt_group": job.prompt_group,
            "title": job.title,
            "description": job.prompt_text,
            "kind": "audio",
        }
        resp = requests.post(url, headers=headers, data=data, files=files, timeout=120)
    try:
        body = resp.json()
    except ValueError:
        body = {"detail": resp.text}
    if resp.status_code == 201:
        return "created", body
    if resp.status_code == 200 and body.get("skipped"):
        return "skipped", body
    return "error", {"status": resp.status_code, **body}


def run(args: Args) -> int:
    if not args.dry_run and not args.token:
        sys.exit("error: --token is required (or set WEBEVAL_API_TOKEN).")
    if not args.musescore.exists():
        sys.exit(f"error: MuseScore binary not found at {args.musescore}")
    if shutil.which("ffmpeg") is None:
        sys.exit("error: ffmpeg not found on PATH.")

    jobs = discover_jobs(args.source)
    if not jobs:
        print("No MusicXML files found; nothing to do.")
        return 0
    print(f"Found {len(jobs)} MusicXML file(s) across {args.source}.")

    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="webeval-stimuli-"))
    workdir.mkdir(parents=True, exist_ok=True)
    cleanup = args.workdir is None and not args.keep_converted

    created = skipped = errors = 0
    try:
        for i, job in enumerate(jobs, start=1):
            tag = f"{job.condition}/{job.prompt_group}/{job.musicxml.name}"
            rendered = workdir / f"{i:04d}-{job.title}.raw.mp3"
            trimmed = workdir / f"{i:04d}-{job.title}.mp3"
            try:
                render_mp3(args.musescore, job.musicxml, rendered)
                trim_mp3(rendered, trimmed, args.duration)
            except subprocess.CalledProcessError as exc:
                errors += 1
                msg = (exc.stderr or b"").decode(errors="replace").strip().splitlines()
                last = msg[-1] if msg else "(no stderr)"
                print(f"[err] {tag}: conversion failed: {last}")
                continue

            if args.dry_run:
                print(f"[dry] {tag}: would upload {trimmed.name} ({trimmed.stat().st_size} bytes)")
                continue

            outcome, body = upload_stimulus(
                args.api_url, args.token, args.experiment, job, trimmed,
            )
            if outcome == "created":
                created += 1
                print(f"[ok]  {tag} sha256={body['sha256'][:12]} "
                      f"dur={body.get('duration_seconds')}")
            elif outcome == "skipped":
                skipped += 1
                print(f"[skip] {tag} duplicate (sha256={body['sha256'][:12]})")
            else:
                errors += 1
                print(f"[err] {tag}: {body}")
    finally:
        if cleanup and workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nSummary: {created} created, {skipped} skipped, {errors} errors, "
          f"{len(jobs)} total.")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
