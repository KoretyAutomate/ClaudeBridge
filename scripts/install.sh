#!/usr/bin/env bash
# ClaudeBridge install script.
# Idempotent — safe to re-run.
set -euo pipefail

PROJECT_DIR="${HOME}/Project/ClaudeBridge"
ENV_NAME="claude_bridge"
CONDA_BIN="${CONDA_BIN:-${HOME}/miniconda3/bin/conda}"
ENV_PYTHON="${HOME}/miniconda3/envs/${ENV_NAME}/bin/python"
UNIT_SRC="${PROJECT_DIR}/systemd/claude-bridge.service"
UNIT_DST_DIR="${HOME}/.config/systemd/user"
UNIT_DST="${UNIT_DST_DIR}/claude-bridge.service"
ENV_FILE="${PROJECT_DIR}/.env"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"
CONFIG_FILE="${PROJECT_DIR}/config.yaml"
CONFIG_EXAMPLE="${PROJECT_DIR}/config.example.yaml"

echo ">> ClaudeBridge install"

# 1. Bootstrap config.yaml from example if missing
if [[ ! -f "${CONFIG_FILE}" ]]; then
  if [[ ! -f "${CONFIG_EXAMPLE}" ]]; then
    echo "ERROR: neither ${CONFIG_FILE} nor ${CONFIG_EXAMPLE} exists."
    exit 1
  fi
  cp "${CONFIG_EXAMPLE}" "${CONFIG_FILE}"
  echo ">> bootstrapped config.yaml from config.example.yaml"
  echo "   ⚠ Edit ${CONFIG_FILE} to set your project prefixes."
fi

# 2. Bootstrap .env from example if missing
if [[ ! -f "${ENV_FILE}" ]]; then
  if [[ ! -f "${ENV_EXAMPLE}" ]]; then
    echo "ERROR: neither ${ENV_FILE} nor ${ENV_EXAMPLE} exists."
    exit 1
  fi
  cp "${ENV_EXAMPLE}" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo ">> bootstrapped .env from .env.example (mode 0600)"
  echo
  echo "   ⚠ Edit ${ENV_FILE} with your bot token and Telegram user ID."
  echo "     Then re-run this script."
  exit 0
fi

# 3. Validate .env was filled in
if grep -q "PUT_BOT_TOKEN_HERE" "${ENV_FILE}"; then
  echo "ERROR: ${ENV_FILE} still has placeholder values. Fill it in first."
  exit 1
fi

env_mode="$(stat -c '%a' "${ENV_FILE}")"
if [[ "${env_mode}" != "600" ]]; then
  echo ">> chmod 600 .env (was ${env_mode})"
  chmod 600 "${ENV_FILE}"
fi

# 4. Conda env
if [[ ! -x "${ENV_PYTHON}" ]]; then
  echo ">> creating conda env ${ENV_NAME}"
  "${CONDA_BIN}" create -y -n "${ENV_NAME}" python=3.11
fi
echo ">> installing python deps"
"${ENV_PYTHON}" -m pip install --quiet --upgrade pip
"${ENV_PYTHON}" -m pip install --quiet \
  "python-telegram-bot>=21.0" \
  "python-dotenv>=1.0" \
  "PyYAML>=6.0"

# 5. Ideation dir
PROJECT_ROOT="${CLAUDE_BRIDGE_PROJECT_ROOT:-${HOME}/Project}"
mkdir -p "${PROJECT_ROOT}/_ideas"

# 6. Audit .gitignore in each scoped project — adds .claude/ and *.jsonl
echo ">> auditing .gitignore for .claude/ and *.jsonl in scoped projects"
prefixes_tmp="$(mktemp)"
trap 'rm -f "${prefixes_tmp}"' EXIT
"${ENV_PYTHON}" -c "
import yaml
with open('${CONFIG_FILE}') as f:
    cfg = yaml.safe_load(f)
for p in cfg.get('prefixes', {}).values():
    print(p['cwd'])
" > "${prefixes_tmp}"
while IFS= read -r dir; do
  [[ -z "${dir}" ]] && continue
  if [[ ! -d "${dir}" ]]; then
    echo "   skip ${dir} (not present)"
    continue
  fi
  gi="${dir}/.gitignore"
  touch "${gi}"
  if ! grep -qxF '.claude/' "${gi}"; then
    echo '.claude/' >> "${gi}"
    echo "   added .claude/ to ${gi}"
  fi
  if ! grep -qxF '*.jsonl' "${gi}"; then
    echo '*.jsonl' >> "${gi}"
    echo "   added *.jsonl to ${gi}"
  fi
done < "${prefixes_tmp}"

# 7. Symlink systemd unit
mkdir -p "${UNIT_DST_DIR}"
if [[ -L "${UNIT_DST}" || -f "${UNIT_DST}" ]]; then
  rm -f "${UNIT_DST}"
fi
ln -s "${UNIT_SRC}" "${UNIT_DST}"
echo ">> linked ${UNIT_DST} -> ${UNIT_SRC}"

systemctl --user daemon-reload
echo ">> systemd reloaded"

# 8. Smoke-test imports
echo ">> smoke-testing imports"
( cd "${PROJECT_DIR}" && "${ENV_PYTHON}" -c "import bridge" )
echo "   imports OK"

cat <<EOF

>> Install complete.

Next steps:
  systemctl --user enable --now claude-bridge.service
  journalctl --user -u claude-bridge.service -f       # watch logs

From your phone, message the bridge bot:
  /help     — should reply with verb + prefix list
EOF
