#!/bin/sh
set -e

: "${OLLAMA_BASE_URL}"
: "${OLLAMA_MODEL}"
: "${OPENCODE_PORT}"

CONFIG_DIR="${HOME}/.config/opencode"
mkdir -p "${CONFIG_DIR}"

cat > "${CONFIG_DIR}/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "DevOps",
      "options": {
        "baseURL": "${OLLAMA_BASE_URL}"
      },
      "models": {
        "${OLLAMA_MODEL}": {}
      }
    }
  },
  "model": "ollama/${OLLAMA_MODEL}"
}
EOF

cd "${HOME}"

exec opencode web --hostname 0.0.0.0 --port "${OPENCODE_PORT}"
