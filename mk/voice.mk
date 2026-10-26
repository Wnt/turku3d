# Character voice lines (audio/voice/): local Kokoro-82M TTS -> game/public/assets/voice (committed).
# Model weights live outside the repo under VOICE_MODELS (public Hugging Face download, no token).
VOICE_DIR    := $(REPO)/audio/voice
VOICE_VENV   ?= $(VOICE_DIR)/.venv
VOICE_PY     ?= $(VOICE_VENV)/bin/python
VOICE_MODELS ?= $(HOME)/turku3d/tools/voice/hf
VOICE_RUN    := cd $(VOICE_DIR) && HF_HOME=$(VOICE_MODELS) HF_HUB_DISABLE_TELEMETRY=1

.PHONY: voice-setup voice-build voice-check voice-test

$(VOICE_PY): $(VOICE_DIR)/requirements.txt
	uv venv --allow-existing --python 3.12 $(VOICE_VENV)
	uv pip install --python $(VOICE_PY) -r $(VOICE_DIR)/requirements.txt
	@touch $@

voice-setup: $(VOICE_PY) ## Voice venv + pinned Kokoro-82M weights and voice packs into $(VOICE_MODELS)
	$(VOICE_RUN) $(VOICE_PY) -m voicegen fetch

voice-build: $(VOICE_PY) ## Synthesize changed dialogue lines into game/public/assets/voice (idempotent, cached)
	$(VOICE_RUN) HF_HUB_OFFLINE=1 python3 $(REPO)/scripts/dev/host_slots.py build -- $(VOICE_PY) -m voicegen build

voice-check: $(VOICE_PY) ## Committed clips match dialogue.json and voices.toml (no model)
	$(VOICE_RUN) $(VOICE_PY) -m voicegen check

voice-test: $(VOICE_PY) ## Voice pipeline tests (fake synthesizer, no model download)
	$(VOICE_RUN) $(VOICE_PY) -m pytest -q -n $(TURKU3D_JOBS) tests
