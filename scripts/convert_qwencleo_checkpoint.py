"""One-time conversion of mohammedaly22/QwenCleo-ASR into a checkpoint that
transformers>=5.x's native qwen3_asr implementation can load directly.

Why this is needed: the checkpoint on the Hub was saved against the OLDER
transformers==4.57.6 module layout (matching the `qwen-asr` PyPI package,
which pins that exact version). Its weight keys are prefixed
"thinker.audio_tower.*" / "thinker.model.*" (a Qwen-Omni-style "Thinker"
wrapper) and its config.json nests audio_config/text_config inside a
"thinker_config" block. Loading it directly with
AutoModelForSpeechSeq2Seq/AutoProcessor against transformers>=5.13.1
"succeeds" with zero errors but is silently wrong: every real weight comes
back MISSING against the current "model.audio_tower.*" /
"model.language_model.*" layout, so the whole model ends up randomly
initialized, and it then crashes on the first real input anyway (an
audio-features/audio-tokens shape-mismatch error deep in generation).

This script remaps the legacy state-dict keys to the current layout,
rebuilds a flat config.json with the corrected audio_config/text_config/
model_type/token ids, and saves a proper local checkpoint that
`from_pretrained` loads cleanly.

The processor needs the same treatment for the same reason: the source repo
declares `feature_extractor_type: WhisperFeatureExtractor`, which is what the
legacy layout used. transformers>=5.x ships a dedicated
`Qwen3ASRFeatureExtractor` that additionally right-pads the mel time axis to
a multiple of `2 * n_window` -- which `Qwen3ASREncoder` REQUIRES, since it
reshapes the features into fixed chunks. Kept as a plain WhisperFeatureExtractor,
every clip whose mel length isn't already a multiple of 100 (i.e. almost any
real recording) dies at generate() with "expects `padded_feature_length` to be
a multiple of `n_window * 2` (100), but got 264".

Run once (re-run only if `models/qwencleo-asr-converted/` is deleted):

    uv run python scripts/convert_qwencleo_checkpoint.py

Deliberately avoids ever constructing the full nn.Module in float32 (the
model.safetensors -> Qwen3ASRForConditionalGeneration(config) -> .to(bfloat16)
sequence transiently allocates the whole ~2B-param model in fp32, ~8GB+,
which segfaults on a 16GB machine). Instead it only remaps and re-saves
tensor keys; the actual model construction happens later via
`from_pretrained`'s own memory-efficient meta-device loading path -- which
this script also uses to verify the result.
"""

import json
import os

import torch
from huggingface_hub import snapshot_download
from safetensors.torch import load_file, save_file
from transformers import (
    AutoProcessor,
    Qwen3ASRConfig,
    Qwen3ASRFeatureExtractor,
    Qwen3ASRForConditionalGeneration,
    Qwen3ASRProcessor,
)

SOURCE_MODEL_ID = "mohammedaly22/QwenCleo-ASR"
DST = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "models", "qwencleo-asr-converted"))


def main():
    src = snapshot_download(SOURCE_MODEL_ID)
    os.makedirs(DST, exist_ok=True)

    with open(os.path.join(src, "config.json"), encoding="utf-8") as f:
        raw = json.load(f)
    tc = raw["thinker_config"]

    audio_config = dict(tc["audio_config"])
    audio_config["model_type"] = "qwen3_asr_encoder"  # legacy name was "qwen3_asr_audio_encoder"
    text_config = dict(tc["text_config"])
    text_config["model_type"] = "qwen3"  # legacy name was "qwen3_asr_text"

    config = Qwen3ASRConfig(
        audio_config=audio_config,
        text_config=text_config,
        audio_token_id=tc["audio_token_id"],
        pad_token_id=raw["pad_token_id"],
        eos_token_id=raw["eos_token_id"],
        tie_word_embeddings=True,
    )
    config.save_pretrained(DST)
    print("config saved")

    state = load_file(os.path.join(src, "model.safetensors"))
    print(f"loaded {len(state)} raw keys")

    remapped = {}
    for k, v in state.items():
        assert k.startswith("thinker."), k
        rest = k[len("thinker."):]
        if rest.startswith("audio_tower.proj1."):
            nk = "model.multi_modal_projector.linear_1." + rest[len("audio_tower.proj1."):]
        elif rest.startswith("audio_tower.proj2."):
            nk = "model.multi_modal_projector.linear_2." + rest[len("audio_tower.proj2."):]
        elif rest.startswith("audio_tower."):
            nk = "model." + rest
        elif rest.startswith("model."):
            nk = "model.language_model." + rest[len("model."):]
        else:
            raise ValueError(f"unhandled key: {k}")
        remapped[nk] = v

    del state
    save_file(remapped, os.path.join(DST, "model.safetensors"), metadata={"format": "pt"})
    print(f"saved {len(remapped)} remapped keys to {DST}\\model.safetensors")
    del remapped

    src_processor = AutoProcessor.from_pretrained(src)
    fe_kwargs = src_processor.feature_extractor.to_dict()
    fe_kwargs.pop("feature_extractor_type", None)
    fe_kwargs["n_window"] = audio_config["n_window"]
    Qwen3ASRProcessor(
        feature_extractor=Qwen3ASRFeatureExtractor(**fe_kwargs),
        tokenizer=src_processor.tokenizer,
        chat_template=src_processor.chat_template,
    ).save_pretrained(DST)
    print("processor saved")
    print("DONE (conversion). Verifying load via from_pretrained...")

    model = Qwen3ASRForConditionalGeneration.from_pretrained(DST, dtype=torch.bfloat16)
    model.eval()
    print("VERIFIED: model loaded cleanly from converted checkpoint.")

    assert torch.equal(model.lm_head.weight, model.model.language_model.embed_tokens.weight), (
        "lm_head is not tied to embed_tokens"
    )
    print("VERIFIED: lm_head correctly tied to embed_tokens.")


if __name__ == "__main__":
    main()
