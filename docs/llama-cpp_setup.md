llama-cpp setup

python3.12 -m venv venv
source venv/bin/activate
pip install huggingface_hub

mkdir -p factorio-models/gemma4-e2b
cd factorio-models/gemma4-e2b

# Download model + mmproj (vision projector - REQUIRED)
hf download unsloth/gemma-4-E2B-it-GGUF   "gemma-4-E2B-it-Q4_K_M.gguf" "mmproj-BF16.gguf" "config.json" --local-dir .

docker pull ghcr.io/ggml-org/llama.cpp:server-cuda