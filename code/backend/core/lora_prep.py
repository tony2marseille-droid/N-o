"""Preparation d'un atelier de fine-tuning LoRA.

Genere un dossier complet : datasets (SFT, traces agentiques, paires DPO),
script d'entrainement adapte au palier materiel et mode d'emploi. Le modele
fusionne (.gguf) produit ensuite se depose dans le dossier GGUF pour devenir
directement disponible dans LocalAI.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from backend.core.config import settings

ATELIER = Path("data/lora_atelier")

CONFIGS_PAR_PALIER = {
    "T1": {
        "base": "Qwen/Qwen3-4B",
        "lora_r": 16,
        "batch": 1,
        "accum": 8,
        "epochs": 2,
        "seq_len": 2048,
        "note": "Entrainement CPU lent ou quantize QLoRA obligatoire.",
    },
    "T2": {
        "base": "Qwen/Qwen3-8B",
        "lora_r": 32,
        "batch": 2,
        "accum": 8,
        "epochs": 3,
        "seq_len": 4096,
        "note": "QLoRA 4-bit recommande sur GPU 10-20 Go.",
    },
    "T3": {
        "base": "Qwen/Qwen3-8B",
        "lora_r": 64,
        "batch": 4,
        "accum": 4,
        "epochs": 3,
        "seq_len": 8192,
        "note": "LoRA full precision possible sur GPU >= 22 Go ; 30B envisageable en QLoRA.",
    },
}


def _ecrire(path: Path, contenu: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contenu, encoding="utf-8")
    return path.stat().st_size


def preparer(tier: str = "") -> dict:
    """Construit l'atelier LoRA complet. Retourne un resume."""
    from backend.core.distiller import (
        export_dpo_pairs,
        export_sft_dataset,
        export_tool_traces,
    )
    from backend.core.hardware import load_cached_profile

    if not tier:
        tier = (load_cached_profile() or {}).get("tier", "T1")
    cfg = CONFIGS_PAR_PALIER.get(tier, CONFIGS_PAR_PALIER["T1"])

    if ATELIER.exists():
        shutil.rmtree(ATELIER, ignore_errors=True)
    ATELIER.mkdir(parents=True, exist_ok=True)

    tailles: dict[str, int] = {}
    contenu, n = export_sft_dataset()
    tailles["sft.jsonl"] = (_ecrire(ATELIER / "sft.jsonl", contenu), n)
    contenu, n = export_tool_traces()
    tailles["agent_traces.jsonl"] = (_ecrire(ATELIER / "agent_traces.jsonl", contenu), n)
    contenu, n = export_dpo_pairs()
    tailles["dpo_pairs.jsonl"] = (_ecrire(ATELIER / "dpo_pairs.jsonl", contenu), n)

    total_exemples = sum(n for _, n in tailles.values())

    script = f"""# Fine-tuning LoRA - LocalAI ({tier})
# Base: {cfg['base']} | r={cfg['lora_r']} | batch={cfg['batch']}x{cfg['accum']} | {cfg['epochs']} epochs
# {cfg['note']}
#
# Prerequis : pip install unsloth  (dans un venv dedie, python 3.11)
# Puis : python train_lora.py

import json

BASE_MODEL = "{cfg['base']}"
MAX_SEQ = {cfg['seq_len']}
EPOCHS = {cfg['epochs']}
BATCH = {cfg['batch']}
ACCUM = {cfg['accum']}
R = {cfg['lora_r']}

def charger(fichier):
    exemples = []
    try:
        with open(fichier, encoding='utf-8') as f:
            for ligne in f:
                if ligne.strip():
                    exemples.append(json.loads(ligne))
    except FileNotFoundError:
        pass
    return exemples

if __name__ == "__main__":
    sft = charger("sft.jsonl")
    traces = charger("agent_traces.jsonl")
    print(f"SFT: {{len(sft)}} exemples | Traces agent: {{len(traces)}}")
    try:
        from triton.compiler.compiler import AttrsDescriptor  # noqa
    except ImportError:
        import triton.compiler.compiler as _tc

        class _AttrsDescriptorShim:
            pass

        _tc.AttrsDescriptor = _AttrsDescriptorShim
    from unsloth import FastLanguageModel
    model, tokenizer = FastLanguageModel.from_pretrained(
        BASE_MODEL, max_seq_length=MAX_SEQ, load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(
        model, r=R, target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                     "gate_proj", "up_proj", "down_proj"],
        lora_alpha=R * 2, lora_dropout=0.05, bias="none", use_gradient_checkpointing="unsloth")
    from trl import SFTTrainer, SFTConfig
    from datasets import Dataset as HFDataset

    def formater(exemple):
        msgs = exemple.get("messages") or [
            {{"role": "user", "content": exemple.get("instruction", "")}},
            {{"role": "assistant", "content": exemple.get("output", "")}},
        ]
        return tokenizer.apply_chat_template(msgs, tokenize=False)

    dataset = HFDataset.from_list([{{"text": formater(e)}} for e in sft + traces])
    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        args=SFTConfig(
            dataset_text_field="text",
            per_device_train_batch_size=BATCH, gradient_accumulation_steps=ACCUM,
            num_train_epochs=EPOCHS, learning_rate=2e-4, fp16=False, bf16=True,
            logging_steps=1, output_dir="./sortie", save_strategy="epoch"))
    trainer.train()
    model.save_pretrained_merged("localai_finetuned", tokenizer,
                                  save_method="merged_16bit")
    print("Modele fusionne : ./localai_finetuned -> convertir en GGUF puis")
    print("deposer le .gguf dans le dossier GGUF de LocalAI.")
"""
    tailles["train_lora.py"] = (_ecrire(ATELIER / "train_lora.py", script), None)

    readme = f"""# Atelier fine-tuning LocalAI - genere le {datetime.now().strftime('%Y-%m-%d %H:%M')}

Palier detecte : {tier} — base conseillee : {cfg['base']}
{sfg_note(cfg)}

## Datasets inclus
- sft.jsonl          : conversations reussies (reponses notees >= 70)
- agent_traces.jsonl : sessions agentiques complètes ayant abouti (verdict OUI)
- dpo_pairs.jsonl    : paires choisi/rejete pour apprentissage de preference

## Etapes
1. Creer un venv dedie : `python -m venv venv-ft` puis `pip install unsloth trl transformers`
2. Lancer : `python train_lora.py`
3. Convertir le modele fusionne en GGUF :
   `python convert_hf_to_gguf.py localai_finetuned --outfile localai_ft.gguf --outtype q8_0`
   (llama.cpp fournit ce script)
4. Copier `localai_ft.gguf` dans le dossier GGUF configure dans LocalAI
5. Dans LocalAI : Moteur llama.cpp -> recharger la liste -> Charger

## Conseils par palier
- T1 : restez sur des modeles <= 4B, entrainement tres long sinon
- T2 : QLoRA 4-bit, sequence 4096
- T3 : 8B full LoRA rapide ; 30B en QLoRA si VRAM >= 24 Go
"""
    tailles["README.md"] = (_ecrire(ATELIER / "README.md", readme), None)

    manifest = {
        "genere_le": datetime.now().isoformat(timespec="seconds"),
        "tier": tier,
        "config": cfg,
        "fichiers": {
            k: {"octets": v[0], **({"exemples": v[1]} if isinstance(v[1], int) else {})}
            for k, v in tailles.items()
        },
        "total_exemples": total_exemples,
    }
    _ecrire(ATELIER / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def sfg_note(cfg: dict) -> str:
    return f"> {cfg['note']}"
