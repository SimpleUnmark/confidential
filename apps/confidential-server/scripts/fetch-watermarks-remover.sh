#!/usr/bin/env bash
set -Eeuo pipefail

readonly source_url="https://github.com/guillaumemeyer/watermarks-remover.git"
readonly release_version="v0.7.0"
readonly release_commit="321d93d2efd6a8b26915c5eb5193d9d1701e2c4b"
readonly script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly default_target="${script_directory}/../.vendor/watermarks-remover"
readonly target_directory="${1:-${default_target}}"

if [[ -d "${target_directory}/.git" ]]; then
  git -C "${target_directory}" fetch --quiet --depth 1 origin "${release_commit}"
else
  mkdir -p "$(dirname "${target_directory}")"
  git init --quiet "${target_directory}"
  git -C "${target_directory}" remote add origin "${source_url}"
  git -C "${target_directory}" fetch --quiet --depth 1 origin "${release_commit}"
fi

git -C "${target_directory}" checkout --quiet --detach FETCH_HEAD
checked_out_commit="$(git -C "${target_directory}" rev-parse HEAD)"
if [[ "${checked_out_commit}" != "${release_commit}" ]]; then
  echo "watermarks-remover checkout mismatch" >&2
  exit 1
fi
for required_script in text_unicode.py common.py image_meta.py av_meta.py clean_audio.py clean_video.py; do
  if [[ ! -f "${target_directory}/service/scripts/${required_script}" ]]; then
    echo "watermarks-remover ${release_version} is missing ${required_script}" >&2
    exit 1
  fi
done

echo "watermarks-remover ${release_version} (${release_commit}) is ready"
