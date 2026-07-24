#!/usr/bin/env python3
"""Model Registry — hardware-tier → recommended model mapping with metadata.

Provides ModelRegistry: a central catalogue of available GGUF models, their
metadata (size, quantisation, description, HuggingFace ID, version), and
per-tier recommendations based on the user's detected hardware capabilities.

Tier definitions:
  weak     — <4 CPU cores or <8 GB RAM     → STT small.en, no LLM stages
  mid      — 4+ cores, 8+ GB RAM           → STT small.en, Q4_K_M quants
  powerful — >=8 cores, >=16 GB RAM          → STT distil-large-v3, Q8_0 quants

Pure stdlib + branding (for tier detection). No network, no heavy deps.
"""

import copy
import os

try:
    from branding import detect_hardware_tier, MODEL_BY_TIER
except ImportError:
    detect_hardware_tier = None
    MODEL_BY_TIER = {}


class ModelRegistry:
    """Central catalogue of available models with hardware-tier recommendations."""

    TIERS = ("weak", "mid", "powerful")

    TIER_DEFS = {
        "weak":     (0,  0,  "Limited hardware — lightweight models only"),
        "mid":      (4,  8,  "Balanced hardware — Q4_K_M quantizations"),
        "powerful": (8, 16,  "Strong hardware — Q8_0 quantizations, all stages"),
    }

    MODELS = {
        "base.en": {
            "name": "Base English",
            "huggingface_id": "Systran/faster-whisper-base.en",
            "filename": "base.en",
            "quantisations": ["fp16"],
            "size_gb": 0.14,
            "version": "1.0",
            "description": "Fast English-only STT model. Suitable for weak hardware.",
            "role": "stt",
            "pipeline_stage": None,
        },
        "small.en": {
            "name": "Small English",
            "huggingface_id": "Systran/faster-whisper-small.en",
            "filename": "small.en",
            "quantisations": ["fp16"],
            "size_gb": 0.47,
            "version": "1.0",
            "description": "Accurate English-only STT model. Good balance of speed and accuracy.",
            "role": "stt",
            "pipeline_stage": None,
        },
        "distil-large-v3": {
            "name": "Distil Large v3",
            "huggingface_id": "Systran/faster-whisper-distil-large-v3",
            "filename": "distil-large-v3",
            "quantisations": ["fp16"],
            "size_gb": 0.76,
            "version": "3.0",
            "description": "High-accuracy multilingual STT. Best speed-accuracy point for strong PCs.",
            "role": "stt",
            "pipeline_stage": None,
        },
        "qwen2.5-1.5b-instruct": {
            "name": "Qwen2.5-1.5B-Instruct",
            "huggingface_id": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
            "filename": "qwen2.5-1.5b-instruct",
            "quantisations": ["Q4_K_M", "Q5_K_M", "Q8_0"],
            "size_gb": 1.3,
            "version": "2.5",
            "description": "Lightweight instruction-tuned LLM. Powers Stage 3 formatting (list, email, prompt).",
            "role": "llm",
            "pipeline_stage": "stage3_formatting",
        },
        "grmr-2b-instruct": {
            "name": "GRMR-2B-Instruct",
            "huggingface_id": "Mumble/grmr-2b-instruct-GGUF",
            "filename": "grmr-2b-instruct",
            "quantisations": ["Q4_K_M", "Q5_K_M", "Q8_0"],
            "size_gb": 1.5,
            "version": "1.0",
            "description": "Grammar correction + filler removal model. Powers Stage 2 grammar cleaning.",
            "role": "llm",
            "pipeline_stage": "stage2_grammar",
        },
    }

    _TIER_RECOMMENDATIONS = {
        "weak": {
            "stt_model": "small.en",
            "stage1_punctuation": {
                "recommended": True,
                "model": None,
                "description": "BERT punctuation restoration (~200MB RAM)",
            },
            "stage2_grammar": {
                "recommended": False,
                "model": None,
                "reason": "Not recommended — requires >=8 GB RAM",
            },
            "stage3_formatting": {
                "recommended": False,
                "model": None,
                "reason": "Not recommended — requires >=8 GB RAM",
            },
            "stage4_cleanup": {
                "recommended": True,
                "model": None,
                "description": "Surface cleanup (zero model cost)",
            },
        },
        "mid": {
            "stt_model": "small.en",
            "stage1_punctuation": {
                "recommended": True,
                "model": None,
                "description": "BERT punctuation restoration (~200MB RAM)",
            },
            "stage2_grammar": {
                "recommended": True,
                "model": "grmr-2b-instruct",
                "quantisation": "Q4_K_M",
                "description": "Grammar correction + filler removal (~1.5GB RAM)",
            },
            "stage3_formatting": {
                "recommended": True,
                "model": "qwen2.5-1.5b-instruct",
                "quantisation": "Q4_K_M",
                "description": "Mode formatting: list, email, prompt (~1.3GB RAM)",
            },
            "stage4_cleanup": {
                "recommended": True,
                "model": None,
                "description": "Surface cleanup (zero model cost)",
            },
        },
        "powerful": {
            "stt_model": "distil-large-v3",
            "stage1_punctuation": {
                "recommended": True,
                "model": None,
                "description": "BERT punctuation restoration (~200MB RAM)",
            },
            "stage2_grammar": {
                "recommended": True,
                "model": "grmr-2b-instruct",
                "quantisation": "Q8_0",
                "description": "Grammar correction + filler removal (~1.8GB RAM)",
            },
            "stage3_formatting": {
                "recommended": True,
                "model": "qwen2.5-1.5b-instruct",
                "quantisation": "Q8_0",
                "description": "Mode formatting: list, email, prompt (~1.6GB RAM)",
            },
            "stage4_cleanup": {
                "recommended": True,
                "model": None,
                "description": "Surface cleanup (zero model cost)",
            },
        },
    }

    def __init__(self):
        self._tier_cache = None

    def detect_tier(self):
        """Probe CPU/RAM and return the hardware tier: 'weak' | 'mid' | 'powerful'."""
        if self._tier_cache:
            return self._tier_cache
        if detect_hardware_tier is not None:
            try:
                tier = detect_hardware_tier()
            except Exception:
                tier = "mid"
        else:
            cores = os.cpu_count() or 4
            tier = "mid" if cores >= 4 else "weak"
        if tier not in self.TIERS:
            tier = "mid"
        self._tier_cache = tier
        return tier

    def recommendations(self, tier=None):
        """Return the full recommendations dict for a given hardware tier."""
        if tier is None:
            tier = self.detect_tier()
        if tier not in self._TIER_RECOMMENDATIONS:
            tier = "mid"
        # Callers customise these dictionaries for UI/runtime state.  A shallow
        # copy leaked nested mutations back into the class-wide catalogue, so a
        # change in one settings session altered all future recommendations.
        return copy.deepcopy(
            self._TIER_RECOMMENDATIONS.get(
                tier, self._TIER_RECOMMENDATIONS["mid"]
            )
        )

    def lookup(self, model_id):
        """Look up model metadata by id (case-insensitive). Returns a dict or None."""
        key = (model_id or "").strip().lower()
        for k, meta in self.MODELS.items():
            if k.lower() == key:
                return copy.deepcopy(meta)
        return None

    def recommended_quantisation(self, tier=None):
        """The recommended GGUF quantisation level for a given tier."""
        if tier is None:
            tier = self.detect_tier()
        if tier == "powerful":
            return "Q8_0"
        return "Q4_K_M"

    def model_for_stage(self, stage, tier=None):
        """Return the recommended model metadata for a pipeline stage and tier."""
        recs = self.recommendations(tier)
        entry = recs.get(stage)
        if not entry or not entry.get("recommended"):
            return None
        model_id = entry.get("model")
        if not model_id:
            return None
        return self.lookup(model_id)

    def enrich_model_info(self, model_info, tier=None):
        """Enrich a discovered model dict with registry metadata."""
        name = (model_info.get("name") or "").strip()
        meta = self.lookup(name)
        if meta:
            model_info["huggingface_id"] = meta.get("huggingface_id")
            model_info["registry_version"] = meta.get("version")
            model_info["description"] = meta.get("description")
            model_info["role"] = meta.get("role")
            model_info["pipeline_stage"] = meta.get("pipeline_stage")
            current = model_info.get("version", "")
            latest = meta.get("version", "")
            model_info["update_available"] = bool(
                current and latest and current != latest
            )
        return model_info
