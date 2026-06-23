"""
Voice cloning wrapper for Coqui TTS XTTS-v2.
Usage: python clone_tts.py --ref <sample_path> --text <text_file> --out <output_path>
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def main():
    parser = argparse.ArgumentParser(description="Coqui XTTS-v2 voice cloning wrapper")
    parser.add_argument("--ref", required=True, help="Reference speaker audio path")
    parser.add_argument("--text", required=True, help="UTF-8 text file path")
    parser.add_argument("--out", required=True, help="Output audio path")
    args = parser.parse_args()

    sample_path = Path(args.ref).expanduser().resolve()
    text_file = Path(args.text).expanduser().resolve()
    output_path = Path(args.out).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not sample_path.exists():
        print("Error: reference audio not found", file=sys.stderr)
        sys.exit(1)
    if not text_file.exists():
        print("Error: text file not found", file=sys.stderr)
        sys.exit(1)

    text = text_file.read_text(encoding="utf-8").strip()
    if not text:
        print("Error: empty text", file=sys.stderr)
        sys.exit(1)

    os.environ["COQUI_TOS_AGREED"] = "1"

    with tempfile.TemporaryDirectory() as tmp_dir:
        xtts_output = Path(tmp_dir) / "xtts_output.wav"
        cmd = [
            "tts",
            "--model_name", "tts_models/multilingual/multi-dataset/xtts_v2",
            "--speaker_wav", str(sample_path),
            "--text", text,
            "--out_path", str(xtts_output),
            "--language_idx", "zh-cn",
        ]

        result = _run(cmd)
        if result.returncode != 0 or not xtts_output.exists():
            err = result.stderr[:1000] if result.stderr else "voice clone failed"
            print(err, file=sys.stderr)
            sys.exit(1)

        if output_path.suffix.lower() == ".mp3":
            convert_cmd = [
                "ffmpeg", "-y",
                "-i", str(xtts_output),
                "-vn",
                "-acodec", "libmp3lame",
                "-q:a", "2",
                str(output_path),
            ]
            convert = _run(convert_cmd, timeout=60)
            if convert.returncode != 0 or not output_path.exists():
                err = convert.stderr[:1000] if convert.stderr else "failed to convert cloned audio to mp3"
                print(err, file=sys.stderr)
                sys.exit(1)
        else:
            output_path.write_bytes(xtts_output.read_bytes())


if __name__ == "__main__":
    main()
