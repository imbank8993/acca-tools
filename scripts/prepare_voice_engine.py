"""Download and verify the local voice model once before enabling the UI."""

import os
from pathlib import Path


def main():
    from arbotts import ArboTTS

    model_dir = Path(os.environ.get("ACCA_VOICE_MODEL_DIR", Path(__file__).resolve().parent.parent / "voice_models"))
    model_dir.mkdir(parents=True, exist_ok=True)
    ready = model_dir / "READY"
    ready.unlink(missing_ok=True)
    model = ArboTTS(tier="studio_clone")
    rate = model.sampling_rate
    if not 8000 <= rate <= 96000:
        raise RuntimeError(f"Unexpected sample rate: {rate}")
    ready.write_text(f"studio_clone {rate} Hz\n", encoding="utf-8")
    print(f"Voice model ready: {rate} Hz")


if __name__ == "__main__":
    main()
