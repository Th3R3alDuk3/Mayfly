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
: "${MAYFLY_WORKSPACE_DIR:?}"

###

OPENCODE_DATA_DIR="${HOME}/.config/opencode"
mkdir -p "${OPENCODE_DATA_DIR}"

cat > "${OPENCODE_DATA_DIR}/opencode.json" <<EOF
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
          "tool_call": true,
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
  "model": "Mayfly/${OPENAI_MODEL}",
  "permission": {
    "read": "allow",
    "write": "ask",
    "edit": "ask",
    "patch": "allow",
    "bash": "allow",
    "glob": "allow",
    "grep": "allow",
    "list": "allow",
    "lsp": "allow",
    "webfetch": "deny",
    "websearch": "deny",
    "skill": "allow",
    "todoread": "allow",
    "todowrite": "allow",
    "question": "allow",
    "doom_loop": "allow",
    "external_directory": "allow"
  }
}
EOF

###

OPENCHAMBER_DATA_DIR="${OPENCHAMBER_DATA_DIR:-${HOME}/.config/openchamber}"
export OPENCHAMBER_DATA_DIR
mkdir -p "${OPENCHAMBER_DATA_DIR}"

OPENCHAMBER_WORKSPACE_DIR="${HOME}/${MAYFLY_WORKSPACE_DIR}"
mkdir -p "${OPENCHAMBER_WORKSPACE_DIR}"

OPENCHAMBER_SETTINGS="${OPENCHAMBER_DATA_DIR}/settings.json"
if [ ! -f "${OPENCHAMBER_SETTINGS}" ]; then
  cat > "${OPENCHAMBER_SETTINGS}" <<EOF
{
  "lastDirectory": "${OPENCHAMBER_WORKSPACE_DIR}",
  "homeDirectory": "${OPENCHAMBER_WORKSPACE_DIR}",
  "projects": [
    {
      "id": "workspace",
      "path": "${OPENCHAMBER_WORKSPACE_DIR}",
      "label": "Workspace"
    }
  ],
  "activeProjectId": "workspace",
  "directoryShowHidden": true,
  "reportUsage": false
}
EOF
fi

exec openchamber \
  --foreground \
  --host 0.0.0.0 \
  --port "${MAYFLY_PORT}" \
  --ui-password "${MAYFLY_PASSWORD}"
