"""LoRA / QLoRA fine-tuning worker.

Run as a child process by the training engine:

``python -m codemind.training.worker --config run.json``

It performs genuine supervised fine-tuning of a local causal language model with PEFT LoRA
(optionally 4-bit quantised via bitsandbytes), writing checkpoints, a JSONL progress stream
and a final adapter to disk. It refuses to run — loudly, with a specific reason — when the
stack or the hardware cannot support it, rather than pretending to train.

Progress protocol (one JSON object per line on stdout):
  {"event": "start", "model": ..., "steps": N, "device": ...}
  {"event": "step", "step": i, "loss": x, "lr": y, "epoch": e, "elapsed": s}
  {"event": "checkpoint", "step": i, "path": ...}
  {"event": "eval", "step": i, "eval_loss": x, "perplexity": p}
  {"event": "done", "adapter": path, "metrics": {...}}
  {"event": "error", "message": ...}
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator


def emit(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, **payload}, default=str), flush=True)


def load_examples(path: Path, limit: int = 0) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not path.exists():
        return items
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            items.append(payload)
            if limit and len(items) >= limit:
                break
    return items


def to_prompt(example: dict[str, Any]) -> tuple[str, str]:
    """Render an example as (prompt, completion) for supervised fine-tuning."""
    if example.get("messages"):
        messages = example["messages"]
        prompt_parts = []
        completion = ""
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "assistant" and not completion:
                completion = content
            else:
                prompt_parts.append(f"### {role}\n{content}")
        return "\n\n".join(prompt_parts) + "\n\n### assistant\n", completion
    instruction = example.get("instruction", "")
    context = example.get("context", "")
    action = example.get("action")
    output = example.get("output", "")
    if action and not output:
        output = json.dumps(action, indent=2)
    prompt = "### instruction\n" + instruction
    if context:
        prompt += "\n\n### context\n" + context
    return prompt + "\n\n### response\n", output


def check_requirements(require_cuda: bool, precision: str) -> str | None:
    try:
        import torch  # noqa: F401
    except Exception as exc:
        return f"PyTorch is not installed for this interpreter ({exc}). Install it from https://pytorch.org (CUDA build for your GPU)."
    try:
        import transformers  # noqa: F401
    except Exception as exc:
        return f"transformers is not installed ({exc}). `pip install transformers`."
    try:
        import peft  # noqa: F401
    except Exception as exc:
        return f"peft is not installed ({exc}). `pip install peft`."
    if precision in ("4bit", "8bit"):
        try:
            import bitsandbytes  # noqa: F401
        except Exception as exc:
            return f"bitsandbytes is required for {precision} quantisation ({exc}). `pip install bitsandbytes`."
    import torch

    if require_cuda and not torch.cuda.is_available():
        return "CUDA is unavailable for this PyTorch build; a GPU is required for this configuration."
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CodeMind LoRA/QLoRA trainer")
    parser.add_argument("--config", required=True, help="path to the run configuration JSON")
    args = parser.parse_args(argv)
    config_path = Path(args.config)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        emit("error", message=f"cannot read config: {exc}")
        return 2

    precision = config.get("precision", "auto")
    require_cuda = bool(config.get("require_cuda", True))
    problem = check_requirements(require_cuda, precision)
    if problem:
        emit("error", message=problem, kind="requirements")
        return 3

    import torch
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup

    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training  # type: ignore

    base_model = config["base_model"]
    dataset_path = Path(config["dataset"])
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = Path(config.get("checkpoint_dir") or output_dir / "checkpoints")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if (device == "cuda" and torch.cuda.is_bf16_supported()) else (torch.float16 if device == "cuda" else torch.float32)
    quant_config = None
    if precision == "4bit":
        from transformers import BitsAndBytesConfig

        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=dtype,
            bnb_4bit_use_double_quant=True,
        )
    elif precision == "8bit":
        from transformers import BitsAndBytesConfig

        quant_config = BitsAndBytesConfig(load_in_8bit=True)

    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=False)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        quantization_config=quant_config,
        torch_dtype=dtype if quant_config is None else None,
        device_map={"": 0} if device == "cuda" else None,
        trust_remote_code=False,
        low_cpu_mem_usage=True,
    )
    if quant_config is not None:
        model = prepare_model_for_kbit_training(model)
    model.gradient_checkpointing_enable()
    lora = LoraConfig(
        r=int(config.get("lora_rank", 16)),
        lora_alpha=int(config.get("lora_alpha", 32)),
        lora_dropout=float(config.get("lora_dropout", 0.05)),
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=config.get("target_modules") or ["q_proj", "v_proj", "k_proj", "o_proj"],
    )
    model = get_peft_model(model, lora)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())

    class InstructionDataset(Dataset):  # type: ignore[misc]
        def __init__(self, path: Path, max_len: int) -> None:
            self.items = load_examples(path)
            self.max_len = max_len

        def __len__(self) -> int:
            return len(self.items)

        def __getitem__(self, index: int) -> dict[str, Any]:
            prompt, completion = to_prompt(self.items[index])
            text = prompt + completion + (tokenizer.eos_token or "")
            encoded = tokenizer(text, truncation=True, max_length=self.max_len, padding=False)
            prompt_ids = tokenizer(prompt, truncation=True, max_length=self.max_len, padding=False)["input_ids"]
            labels = list(encoded["input_ids"])
            for position in range(min(len(prompt_ids), len(labels))):
                labels[position] = -100
            encoded["labels"] = labels
            return encoded

    def collate(batch: list[dict[str, Any]]) -> dict[str, Any]:
        max_len = max(len(item["input_ids"]) for item in batch)
        input_ids, attention, labels = [], [], []
        for item in batch:
            pad = max_len - len(item["input_ids"])
            input_ids.append(item["input_ids"] + [tokenizer.pad_token_id] * pad)
            attention.append(item["attention_mask"] + [0] * pad)
            labels.append(item["labels"] + [-100] * pad)
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "attention_mask": torch.tensor(attention, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
        }

    max_len = int(config.get("max_seq_len", 1024))
    dataset = InstructionDataset(dataset_path, max_len)
    if len(dataset) == 0:
        emit("error", message=f"dataset {dataset_path} contains no usable examples")
        return 4
    validation_path = config.get("validation_dataset")
    validation = InstructionDataset(Path(validation_path), max_len) if validation_path and Path(validation_path).exists() else None
    loader = DataLoader(dataset, batch_size=int(config.get("batch_size", 1)), shuffle=True, collate_fn=collate, num_workers=0)
    validation_loader = DataLoader(validation, batch_size=1, collate_fn=collate) if validation else None

    epochs = int(config.get("epochs", 1))
    grad_accum = max(1, int(config.get("grad_accum", 8)))
    lr = float(config.get("learning_rate", 2e-4))
    max_steps = int(config.get("max_steps", 0) or 0)
    checkpoint_every = int(config.get("checkpoint_every", 50))
    resume_from = config.get("resume_from")
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr)
    scheduler = get_cosine_schedule_with_warmup(optimizer, num_warmup_steps=max(1, len(loader) // 10), num_training_steps=max(1, len(loader) * epochs // grad_accum))
    start_step = 0
    if resume_from and Path(resume_from).exists():
        from peft import PeftModel  # noqa: F401  (documentation of the resume path)

        state = torch.load(Path(resume_from) / "training_state.pt", map_location="cpu") if (Path(resume_from) / "training_state.pt").exists() else None
        if state:
            model.load_state_dict(state["model"], strict=False)
            optimizer.load_state_dict(state["optimizer"])
            start_step = int(state.get("step", 0))
            emit("resume", step=start_step, path=str(resume_from))

    emit(
        "start",
        model=base_model,
        device=device,
        precision=precision,
        steps=len(dataset) * epochs // grad_accum,
        examples=len(dataset),
        trainable_params=trainable,
        total_params=total,
        trainable_percent=round(100.0 * trainable / max(1, total), 3),
        dtype=str(dtype),
    )

    model.train()
    running_loss = 0.0
    step = start_step
    started = time.monotonic()
    micro = 0
    try:
        for epoch in range(epochs):
            for batch in loader:
                micro += 1
                outputs = model(**{key: value.to(model.device) for key, value in batch.items()})
                loss = outputs.loss / grad_accum
                loss.backward()
                running_loss += float(loss.item()) * grad_accum
                if micro % grad_accum == 0:
                    torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)
                    step += 1
                    if step % 5 == 0 or step == 1:
                        emit(
                            "step",
                            step=step,
                            loss=round(running_loss / max(1, micro), 5),
                            lr=round(scheduler.get_last_lr()[0], 8),
                            epoch=epoch + 1,
                            elapsed=round(time.monotonic() - started, 2),
                            examples=min(len(dataset), micro * int(config.get("batch_size", 1))),
                        )
                    if checkpoint_every and step % checkpoint_every == 0:
                        target = checkpoint_dir / f"step-{step}"
                        model.save_pretrained(target)
                        torch.save(
                            {"model": model.state_dict(), "optimizer": optimizer.state_dict(), "step": step},
                            target / "training_state.pt",
                        )
                        eval_metrics = None
                        if validation_loader is not None:
                            model.eval()
                            total_loss = 0.0
                            with torch.no_grad():
                                for batch in validation_loader:
                                    out = model(**{key: value.to(model.device) for key, value in batch.items()})
                                    total_loss += float(out.loss.item())
                            eval_metrics = {"eval_loss": round(total_loss / max(1, len(validation_loader)), 5)}
                            eval_metrics["perplexity"] = round(math.exp(min(20.0, eval_metrics["eval_loss"])), 3)
                            model.train()
                            emit("eval", step=step, **eval_metrics)
                        emit("checkpoint", step=step, path=str(target), metrics=eval_metrics)
                    if max_steps and step >= max_steps:
                        break
            if max_steps and step >= max_steps:
                break
    except KeyboardInterrupt:  # pragma: no cover
        emit("interrupted", step=step)
    except RuntimeError as exc:
        message = str(exc)
        if "out of memory" in message.lower():
            emit("error", message="CUDA out of memory — lower the micro batch size, sequence length or LoRA rank.", kind="oom")
        else:
            emit("error", message=message, kind="runtime")
        return 5

    adapter_dir = output_dir / "adapter"
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    metrics = {
        "steps": step,
        "final_loss": round(running_loss / max(1, micro), 5),
        "elapsed": round(time.monotonic() - started, 2),
        "trainable_params": trainable,
        "trainable_percent": round(100.0 * trainable / max(1, total), 3),
        "examples": len(dataset),
        "seconds_per_step": round((time.monotonic() - started) / max(1, step - start_step), 3),
    }
    emit("done", adapter=str(adapter_dir), metrics=metrics)
    return 0


if __name__ == "__main__":  # pragma: no cover - executed as a module
    sys.exit(main())
