---
base_model: Qwen/Qwen3-1.7B
library_name: transformers
license: apache-2.0
language:
  - zh
  - en
pipeline_tag: text-generation
tags:
  - qwen3
  - lora
  - peft
  - gguf
  - ollama
  - photo-editing
---

# ResidualFusion Prompt Controller

ResidualFusion Prompt Controller is a supervised BF16 LoRA adaptation of
Qwen3-1.7B. It converts conversational photo-editing requests into the constrained
control schema consumed by the ResidualFusion image pipeline.

The model does not generate or modify pixels. Image analysis, residual parameter
prediction, semantic masks, safety selection, and rendering remain deterministic
components of the ResidualFusion application.

## Model Assets

| Path | Purpose |
| --- | --- |
| `ai-photo-prompt-control-exp007-bf16.gguf` | Base model and selected LoRA merged in BF16 GGUF form for Ollama deployment |
| `lora_adapter/adapter_model.safetensors` | Selected rank-16 LoRA adapter for research or continued fine-tuning |
| `lora_adapter/adapter_config.json` | PEFT adapter configuration |
| `Modelfile` | Deterministic Ollama import and inference configuration |

SHA-256 hashes and exact file sizes are recorded in the
[`v1.0.0` asset manifest](https://huggingface.co/Kaiii1912/residual_fusion/blob/v1.0.0/MODEL_ASSETS.json).
The complete editor is published in
the [AI Photo Editor repository](https://github.com/WeiChen80percent/AI_photo_editor/tree/main/residual_fusion).

## Training

- Base model: `Qwen/Qwen3-1.7B`, frozen during adapter training.
- Method: supervised BF16 LoRA with rank 16, alpha 32, and dropout 0.05.
- Selected checkpoint: step 260, chosen by intent accuracy, complete-field exact
  accuracy, and then the earliest checkpoint.
- Output contract: structured intent, strength, and preservation constraints for
  the downstream image editor.

## Evaluation

The selected validation checkpoint achieved:

- Intent accuracy: 100%.
- Complete-field exact accuracy: 95%.
- Constraint micro-F1: 98.46% on the selected validation split.

An isolated synthetic prompt audit achieved 97.5% intent accuracy, while a harder
compound-constraint audit achieved 74.03% micro-F1. These are task-specific
control metrics and should not be interpreted as general language-model accuracy.

## Ollama Deployment

Use the installer included in the ResidualFusion application package:

```powershell
.\install_prompt_model.ps1 -RepoId "Kaiii1912/residual_fusion"
.\install_prompt_model.ps1 -CheckOnly
```

The installer downloads the GGUF, verifies its SHA-256 hash, and creates the local
Ollama model `ai-photo-prompt-control:exp007-v1`.

## Limitations

- The model is specialized for ResidualFusion's constrained editing schema; it is
  not intended as a general chatbot.
- Compound constraints remain less reliable than single editing intents.
- Reported results are validation and isolated audit measurements. A new,
  untouched natural-language blind test has not been opened.
- The complete photo-editing result also depends on the separate ResidualFusion
  image pipeline and cannot be reproduced from this language model alone.

## License

The base Qwen3-1.7B model is distributed under Apache-2.0. Use of the application
code and other third-party components remains subject to their respective licenses.
