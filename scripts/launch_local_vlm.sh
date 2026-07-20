#!/usr/bin/env bash
# Launch a local VLM server for smallgameagent.
#
# Supports two backends:
#   1. LM Studio (GUI or CLI) — easiest on Windows/WSL2.
#   2. llama.cpp server with Vulkan/CUDA — headless, Linux native.
#
# The agent talks to the server via the OpenAI-compatible endpoint
# http://127.0.0.1:1234/v1 (LM Studio default) or http://127.0.0.1:8080/v1
# (llama.cpp default).  Configure LMSTUDIO_BASE_URL / LMSTUDIO_MODEL in .env.

set -euo pipefail

PORT="${VLM_PORT:-1234}"
MODEL="${LMSTUDIO_MODEL:-gemma-4-e4b-it-Q4_K_M.gguf}"
BACKEND="${VLM_BACKEND:-lmstudio}"  # lmstudio | cuda | vulkan

show_help() {
  cat <<EOF
Usage: $0 [backend]

Environment variables:
  VLM_BACKEND    lmstudio | cuda | vulkan   (default: lmstudio)
  VLM_PORT       server port                (default: 1234)
  LMSTUDIO_MODEL model filename             (default: gemma-4-e4b-it-Q4_K_M.gguf)
  VLM_CTX_SIZE   context size               (default: 4096)
  VLM_KV_QUANT   q4_0 | q8_0 | q4_k_m       KV-cache quantization (llama.cpp only)
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ -n "${1:-}" ]]; then
  BACKEND="$1"
fi

CTX_SIZE="${VLM_CTX_SIZE:-4096}"
KV_QUANT="${VLM_KV_QUANT:-q4_0}"

case "$BACKEND" in
  lmstudio)
    echo "================================================================"
    echo "LM Studio backend selected."
    echo "Please load the model in LM Studio and start the local server on"
    echo "port $PORT.  The agent will connect to:"
    echo "  LMSTUDIO_BASE_URL=http://127.0.0.1:${PORT}/v1"
    echo "  LMSTUDIO_MODEL=$MODEL"
    echo ""
    echo "Recommended LM Studio settings for RTX 5060 Laptop 8 GB:"
    echo "  - Context Length: $CTX_SIZE"
    echo "  - GPU offload: max layers"
    echo "  - KV-cache quantization: $KV_QUANT"
    echo "================================================================"
    ;;

  cuda)
    if ! command -v llama-server &> /dev/null; then
      echo "llama-server not found. Install llama.cpp with CUDA, or use LM Studio."
      exit 1
    fi
    echo "Starting llama.cpp server (CUDA) on port $PORT ..."
    exec llama-server \
      -m "$MODEL" \
      --port "$PORT" \
      -ngl 999 \
      -c "$CTX_SIZE" \
      --flash-attn \
      --kv-cache-type "$KV_QUANT" \
      --api-key lm-studio
    ;;

  vulkan)
    if ! command -v llama-server &> /dev/null; then
      echo "llama-server not found. Install llama.cpp with Vulkan, or use LM Studio."
      exit 1
    fi
    echo "Starting llama.cpp server (Vulkan) on port $PORT ..."
    # For Intel iGPU keep ngl moderate; tune for your device.
    exec llama-server \
      -m "$MODEL" \
      --port "$PORT" \
      -ngl 50 \
      -c "$CTX_SIZE" \
      --kv-cache-type "$KV_QUANT" \
      --api-key lm-studio
    ;;

  *)
    echo "Unknown backend: $BACKEND"
    show_help
    exit 1
    ;;
esac
