"""
Voice cloning wrapper for CosyVoice zero-shot voice cloning.
Usage: python clone_tts.py --ref <sample_path> --text <text_file> --out <output_path>

Set COSYVOICE_DIR to the local CosyVoice repository if it is not importable from PYTHONPATH.
Optionally set COSYVOICE_MODEL_DIR to a local CosyVoice model directory.
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _convert_if_needed(source_path: Path, output_path: Path) -> None:
    if output_path.suffix.lower() == ".wav":
        output_path.write_bytes(source_path.read_bytes())
        return

    convert_cmd = [
        "ffmpeg", "-y",
        "-i", str(source_path),
        "-vn",
        "-acodec", "libmp3lame",
        "-q:a", "2",
        str(output_path),
    ]
    convert = _run(convert_cmd)
    if convert.returncode != 0 or not output_path.exists():
        err = convert.stderr[:1000] if convert.stderr else "failed to convert CosyVoice audio"
        print(err, file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="CosyVoice zero-shot voice cloning wrapper")
    parser.add_argument("--ref", required=True, help="Reference speaker audio path")
    parser.add_argument("--text", required=True, help="UTF-8 text file path")
    parser.add_argument("--out", required=True, help="Output audio path")
    parser.add_argument("--prompt-text", default="", help="Transcript of the reference audio; optional but recommended")
    parser.add_argument("--model-dir", default=os.getenv("COSYVOICE_MODEL_DIR"))
    args = parser.parse_args()

    sample_path = Path(args.ref).expanduser().resolve()
    text_file = Path(args.text).expanduser().resolve()
    output_path = Path(args.out).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cosyvoice_dir = os.getenv("COSYVOICE_DIR")
    if cosyvoice_dir:
        sys.path.insert(0, str(Path(cosyvoice_dir).expanduser().resolve()))

    model_dir = args.model_dir or (
        str(Path(cosyvoice_dir).expanduser().resolve() / "pretrained_models" / "CosyVoice2-0.5B")
        if cosyvoice_dir else "pretrained_models/CosyVoice2-0.5B"
    )

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

    try:
        import torchaudio
        from cosyvoice.cli.cosyvoice import CosyVoice2
        from cosyvoice.utils.file_utils import load_wav
    except Exception as e:
        print(f"Error: CosyVoice dependencies are not available: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        cosyvoice = CosyVoice2(model_dir, load_jit=False, load_trt=False, load_vllm=False, fp16=False)
        prompt_speech = load_wav(str(sample_path), 16000)
        prompt_text = args.prompt_text.strip()

        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_output = Path(tmp_dir) / "cosyvoice_output.wav"
            result = next(cosyvoice.inference_zero_shot(text, prompt_text, prompt_speech, stream=False))
            torchaudio.save(str(wav_output), result["tts_speech"], cosyvoice.sample_rate)
            if not wav_output.exists():
                print("Error: CosyVoice did not produce output", file=sys.stderr)
                sys.exit(1)
            _convert_if_needed(wav_output, output_path)
    except Exception as e:
        print(str(e)[:1000], file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
