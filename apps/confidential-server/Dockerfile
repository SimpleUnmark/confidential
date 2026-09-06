ARG PYTHON_VERSION=3.13.7

FROM mirror.gcr.io/library/python:${PYTHON_VERSION}-slim-trixie@sha256:5f55cdf0c5d9dc1a415637a5ccc4a9e18663ad203673173b8cda8f8dcacef689 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /src
RUN apt-get -o Acquire::Retries=3 -o Acquire::http::Pipeline-Depth=0 update \
    && apt-get -o Acquire::Retries=3 -o Acquire::http::Pipeline-Depth=0 \
      install --yes --no-install-recommends ca-certificates ffmpeg git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt pyproject.toml ./
COPY simpleunmark_confidential ./simpleunmark_confidential
COPY scripts ./scripts
RUN bash scripts/fetch-watermarks-remover.sh /opt/watermarks-remover-source
RUN pip install --target /opt/python --requirement requirements.txt \
    && pip install --target /opt/python --no-deps .
RUN install --directory --mode=1777 /opt/simpleunmark-tmp
RUN install --directory /opt/ffmpeg-root/usr/bin \
      /opt/ffmpeg-root/usr/share/licenses/ffmpeg \
    && cp /usr/bin/ffmpeg /usr/bin/ffprobe /opt/ffmpeg-root/usr/bin/ \
    && cp /usr/share/doc/ffmpeg/copyright \
      /opt/ffmpeg-root/usr/share/licenses/ffmpeg/copyright \
    && ldd /usr/bin/ffmpeg /usr/bin/ffprobe \
      | awk '/=> \// { print $3 } $1 ~ /^\// && $1 !~ /:$/ { print $1 }' \
      | sed -e 's#^/lib/#/usr/lib/#' -e 's#^/lib64/#/usr/lib64/#' \
      | sort -u \
      | xargs -I '{}' cp --dereference --parents '{}' /opt/ffmpeg-root

FROM gcr.io/distroless/python3-debian13:nonroot@sha256:f3d5ddc6c64a019fe520e7f005f2880be21e6afc461b10a3c15ef2e4edc71e33

ARG BUILD_DATE
ARG VCS_REF
ARG SOURCE_URL=https://github.com/SimpleUnmark/confidential

LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.description="Simple Unmark Confidential Space text and media workload" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.title="simpleunmark-confidential" \
      org.simpleunmark.watermarks-remover.version="v0.7.0" \
      org.simpleunmark.watermarks-remover.revision="321d93d2efd6a8b26915c5eb5193d9d1701e2c4b" \
      "tee.launch_policy.allow_capabilities"="false" \
      "tee.launch_policy.allow_cgroups"="false" \
      "tee.launch_policy.allow_cmd_override"="false" \
      "tee.launch_policy.allow_env_override"="" \
      "tee.launch_policy.allow_mount_destinations"="/tmp/simpleunmark" \
      "tee.launch_policy.log_redirect"="never" \
      "tee.launch_policy.monitoring_memory_allow"="never"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/python \
    APP_RECEIPT_URL=https://simpleunmark.com/api/clean/receipt \
    ALLOWED_ORIGINS=https://simpleunmark.com \
    DEEPINFRA_BASE_URL=https://api.deepinfra.com/v1/openai \
    DEEPINFRA_MODEL=deepseek-ai/DeepSeek-V4-Flash-0731 \
    ATTESTATION_AUDIENCE=https://simpleunmark.com/confidential-workload/v1 \
    REQUIRE_CLIENT_ATTESTATION=1 \
    WATERMARKS_REMOVER_SCRIPTS=/opt/watermarks-remover \
    TMPDIR=/tmp/simpleunmark \
    HOST=0.0.0.0 \
    PORT=8080 \
    LOG_LEVEL=warning \
    SIMPLEUNMARK_REVISION=${VCS_REF}

WORKDIR /app
COPY --from=builder /opt/python /opt/python
COPY --from=builder /opt/watermarks-remover-source/service/scripts/text_unicode.py /opt/watermarks-remover/text_unicode.py
COPY --from=builder /opt/watermarks-remover-source/service/scripts/common.py /opt/watermarks-remover/common.py
COPY --from=builder /opt/watermarks-remover-source/service/scripts/image_meta.py /opt/watermarks-remover/image_meta.py
COPY --from=builder /opt/watermarks-remover-source/service/scripts/av_meta.py /opt/watermarks-remover/av_meta.py
COPY --from=builder /opt/watermarks-remover-source/service/scripts/clean_audio.py /opt/watermarks-remover/clean_audio.py
COPY --from=builder /opt/watermarks-remover-source/service/scripts/clean_video.py /opt/watermarks-remover/clean_video.py
COPY --from=builder /opt/watermarks-remover-source/LICENSE /opt/watermarks-remover/LICENSE
COPY --from=builder /opt/ffmpeg-root /
COPY --from=builder --chown=nonroot:nonroot /opt/simpleunmark-tmp /tmp/simpleunmark

USER nonroot
EXPOSE 8080/tcp
ENTRYPOINT ["/usr/bin/python3", "-m", "simpleunmark_confidential"]
