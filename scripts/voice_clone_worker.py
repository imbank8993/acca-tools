"""Run voice-conditioned Indonesian TTS in an isolated local process."""

import sys
from pathlib import Path


def generate(model_dir: Path, reference: Path, output: Path, text: str, reference_text: str) -> None:
    import numpy as np
    import soundfile as sf
    from arbotts import ArboTTS

    if not (model_dir / "READY").is_file():
        raise RuntimeError("Voice model has not been prepared")
    model = ArboTTS(tier="studio_clone")
    audio, _ = model.clone(text=text, reference=reference, reference_text=reference_text,
                           phonetic_preprocess=True, polish_timbre=False)
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if not audio.size or not np.all(np.isfinite(audio)):
        raise RuntimeError("Invalid synthesized audio")
    peak = float(np.max(np.abs(audio)))
    if peak > 0.95:
        audio *= 0.95 / peak
    sf.write(output, audio, model.sampling_rate, subtype="PCM_16")


if __name__ == "__main__":
    generate(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5])
