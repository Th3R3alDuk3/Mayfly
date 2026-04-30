#!/bin/sh
set -e

: "${OPENAI_BASE_URL:?}"
: "${OPENAI_MODEL:?}"
: "${OPENAI_CONTEXT_TOKENS:?}"
: "${OPENAI_OUTPUT_TOKENS:?}"
: "${OPENAI_TIMEOUT:?}"
: "${OPENAI_CHUNK_TIMEOUT:?}"
: "${MAYFLY_PORT:?}"
: "${MAYFLY_PASSWORD:?}"

CONFIG_DIR="${HOME}/.config/opencode"
mkdir -p "${CONFIG_DIR}"

cat > "${CONFIG_DIR}/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "enabled_providers": ["Mayfly"],
  "provider": {
    "Mayfly": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Mayfly",
      "options": {
        "baseURL": "${OPENAI_BASE_URL}",
        "timeout": ${OPENAI_TIMEOUT},
        "chunkTimeout": ${OPENAI_CHUNK_TIMEOUT}
      },
      "models": {
        "${OPENAI_MODEL}": {
          "limit": {
            "context": ${OPENAI_CONTEXT_TOKENS},
            "output": ${OPENAI_OUTPUT_TOKENS}
          },
          "modalities": {
            "input": ["text", "image"],
            "output": ["text"]
          },
          "variants": {
            "think": {
              "reasoningEffort": "high"
            },
            "fast": {
              "reasoningEffort": "low"
            }
          }
        }
      }
    }
  },
  "model": "Mayfly/${OPENAI_MODEL}"
}
EOF

WORKSPACE_DIR="${HOME}/WORKSPACE"
mkdir -p "${WORKSPACE_DIR}"

cd "${HOME}"

exec openchamber \
  --foreground \
  --host 0.0.0.0 \
  --port "${MAYFLY_PORT}" \
  --ui-password "${MAYFLY_PASSWORD}"
