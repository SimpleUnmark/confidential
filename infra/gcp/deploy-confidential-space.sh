#!/usr/bin/env bash
set -Eeuo pipefail

required_values=(
  SU_GCP_PROJECT_ID
  SU_GCP_ZONE
  SU_INSTANCE_NAME
  SU_WORKLOAD_SERVICE_ACCOUNT
  SU_IMAGE_REFERENCE
  SU_DEEPINFRA_API_KEY_FILE
  SU_CONFIDENTIAL_SHARED_SECRET_FILE
)

for value_name in "${required_values[@]}"; do
  if [[ -z "${!value_name:-}" ]]; then
    echo "Required environment variable is missing: ${value_name}" >&2
    exit 2
  fi
done

for credential_file in \
  "${SU_DEEPINFRA_API_KEY_FILE}" \
  "${SU_CONFIDENTIAL_SHARED_SECRET_FILE}"
do
  if [[ ! -f "${credential_file}" || ! -s "${credential_file}" ]]; then
    echo "Credential file is missing or empty: ${credential_file}" >&2
    exit 2
  fi
done

if [[ "$(wc -c < "${SU_CONFIDENTIAL_SHARED_SECRET_FILE}")" -lt 32 ]]; then
  echo "SU_CONFIDENTIAL_SHARED_SECRET_FILE must contain at least 32 bytes." >&2
  exit 2
fi

if [[ ! "${SU_IMAGE_REFERENCE}" =~ \.pkg\.dev/.+@sha256:[a-f0-9]{64}$ ]]; then
  echo "SU_IMAGE_REFERENCE must be an Artifact Registry reference pinned by sha256 digest." >&2
  exit 2
fi

metadata="^~^tee-image-reference=${SU_IMAGE_REFERENCE}"
metadata+="~tee-restart-policy=Always"
metadata+="~tee-container-log-redirect=false"
metadata+="~tee-mount=type=tmpfs,source=tmpfs,destination=/tmp/simpleunmark,size=1073741824"

gcloud compute instances create "${SU_INSTANCE_NAME}" \
  --project="${SU_GCP_PROJECT_ID}" \
  --zone="${SU_GCP_ZONE}" \
  --machine-type="${SU_MACHINE_TYPE:-n2d-standard-2}" \
  --confidential-compute-type=SEV \
  --maintenance-policy=MIGRATE \
  --shielded-secure-boot \
  --image-project=confidential-space-images \
  --image-family=confidential-space \
  --service-account="${SU_WORKLOAD_SERVICE_ACCOUNT}" \
  --scopes=cloud-platform \
  --no-address \
  --metadata="${metadata}" \
  --metadata-from-file="simpleunmark-deepinfra-api-key=${SU_DEEPINFRA_API_KEY_FILE},simpleunmark-shared-secret=${SU_CONFIDENTIAL_SHARED_SECRET_FILE}" \
  --tags=simpleunmark-confidential
