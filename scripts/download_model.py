"""Fetch one immutable model snapshot at setup/build time, never during a request."""

from pathlib import Path

from huggingface_hub import snapshot_download

MODEL_ID = "minishlab/potion-base-8M"
# Pin the revision so a rebuild cannot silently change existing vector semantics.
MODEL_REVISION = "bf8b056651a2c21b8d2565580b8569da283cab23"
MODEL_PATH = Path(__file__).resolve().parents[1] / "assets" / "model"


def main() -> None:
    snapshot_download(
        MODEL_ID,
        revision=MODEL_REVISION,
        local_dir=MODEL_PATH,
        allow_patterns=["config.json", "tokenizer.json", "model.safetensors", "README.md"],
    )
    print(f"Model ready: {MODEL_PATH}")


if __name__ == "__main__":
    main()
