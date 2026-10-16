"""Real local training: datasets, adapters, checkpoints, evaluation and promotion.

Two practical methods ship by default and both actually train:

* ``adapter_lite`` — a small neural network with real backpropagation, written in plain Python
  so it trains anywhere (no torch, no CUDA, a few MB of RAM). It learns the decisions the
  agent makes constantly: which tool to use for an instruction, and which files matter.
* ``qlora`` / ``lora`` — genuine LoRA/QLoRA fine-tuning of a local causal LM through
  ``transformers`` + ``peft`` (+ ``bitsandbytes`` for 4-bit). CodeMind generates a real
  training script and runs it in a child process with checkpoints and resume; it is only
  offered when the stack and the hardware can actually support it.

Whatever the method, the artefact is measured against the current baseline before it can be
promoted, and "memory" is never presented as "training".
"""

from codemind.training.engine import TrainingEngine, TrainingRun
from codemind.training.manager import TrainingManager

__all__ = ["TrainingEngine", "TrainingRun", "TrainingManager"]
