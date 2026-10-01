#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${XIAOZHI_APP_DIR:-/opt/xiaozhi}"
ENV_FILE="${APP_DIR}/.env"
REDIRECT_URI="https://xiaozhiscig.biz.id/api/auth/google/callback"

if [[ ! -t 0 ]]; then
  echo "Jalankan skrip ini secara interaktif agar secret tidak masuk shell history." >&2
  exit 1
fi
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "File ${ENV_FILE} tidak ditemukan." >&2
  exit 1
fi

echo "Authorized redirect URI yang wajib didaftarkan di Google Cloud:"
echo "${REDIRECT_URI}"
echo
read -r -p "Google OAuth Client ID: " google_client_id
read -r -s -p "Google OAuth Client Secret: " google_client_secret
echo

if [[ ! "${google_client_id}" =~ ^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$ ]]; then
  echo "Format Client ID Google tidak valid." >&2
  exit 1
fi
if [[ ${#google_client_secret} -lt 16 || ! "${google_client_secret}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Format Client Secret Google tidak valid." >&2
  exit 1
fi

umask 077
temp_env="$(mktemp "${APP_DIR}/.env.google.XXXXXX")"
cleanup() {
  rm -f -- "${temp_env}"
}
trap cleanup EXIT

declare -A replacements=(
  [GOOGLE_AUTH_ENABLED]="true"
  [GOOGLE_CLIENT_ID]="${google_client_id}"
  [GOOGLE_CLIENT_SECRET]="${google_client_secret}"
  [GOOGLE_REDIRECT_URI]="${REDIRECT_URI}"
)
declare -A seen=()

while IFS= read -r line || [[ -n "${line}" ]]; do
  key="${line%%=*}"
  case "${key}" in
    GOOGLE_AUTH_ENABLED|GOOGLE_CLIENT_ID|GOOGLE_CLIENT_SECRET|GOOGLE_REDIRECT_URI)
      printf '%s=%s\n' "${key}" "${replacements[${key}]}" >>"${temp_env}"
      seen["${key}"]=1
      ;;
    *)
      printf '%s\n' "${line}" >>"${temp_env}"
      ;;
  esac
done <"${ENV_FILE}"

for key in GOOGLE_AUTH_ENABLED GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET GOOGLE_REDIRECT_URI; do
  if [[ ! -v "seen[${key}]" ]]; then
    printf '%s=%s\n' "${key}" "${replacements[${key}]}" >>"${temp_env}"
  fi
done

chmod 600 "${temp_env}"
mv -f -- "${temp_env}" "${ENV_FILE}"
trap - EXIT
google_client_secret=""

cd "${APP_DIR}"
current_image="$(docker inspect --format '{{.Config.Image}}' xiaozhi)"
XIAOZHI_IMAGE="${current_image}" docker compose up -d --force-recreate --no-deps xiaozhi

cookie_jar="$(mktemp)"
cleanup_cookie() {
  rm -f -- "${cookie_jar}"
}
trap cleanup_cookie EXIT

for _ in $(seq 1 24); do
  if [[ "$(docker inspect --format '{{.State.Health.Status}}' xiaozhi 2>/dev/null || true)" == "healthy" ]]; then
    location="$(curl --fail --silent --show-error --output /dev/null --write-out '%{redirect_url}' \
      -H 'Host: xiaozhiscig.biz.id' \
      -c "${cookie_jar}" \
      http://127.0.0.1:8080/api/auth/google/login?intent=login)"
    if [[ "${location}" == https://accounts.google.com/* && "${location}" == *"code_challenge="* ]]; then
      cleanup_cookie
      trap - EXIT
      echo "Google OAuth aktif dan redirect PKCE berhasil diverifikasi."
      exit 0
    fi
    echo "Aplikasi sehat, tetapi redirect Google tidak lolos verifikasi." >&2
    exit 1
  fi
  sleep 5
done

docker compose logs --tail 100 xiaozhi >&2
echo "Container tidak sehat setelah konfigurasi Google OAuth." >&2
exit 1
