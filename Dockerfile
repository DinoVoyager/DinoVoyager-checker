FROM alpine:3.21

LABEL org.opencontainers.image.title="DinoVoyager Checker" \
      org.opencontainers.image.description="Lightweight distributed connectivity monitoring probe for DinoVoyager" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.authors="DinoVoyager Team" \
      org.opencontainers.image.source="https://github.com/DinoVoyager/DinoVoyager-checker" \
      org.opencontainers.image.licenses="MIT" \
      maintainer="DinoVoyager Team"

# Install minimal runtime dependencies (zero pip / zero build tools needed)
RUN apk add --no-cache python3 ca-certificates curl tzdata && \
    adduser -D -u 1000 -h /app appuser && \
    mkdir -p /app/checker /app/geo && \
    chown -R appuser:appuser /app

WORKDIR /app

# Copy Go ELF binary and set executable permissions
COPY bin/xray-checker /usr/bin/xray-checker
RUN chmod +x /usr/bin/xray-checker

# Copy checker python package and entrypoint script
COPY checker/ /app/checker/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Drop privileges to non-root appuser
USER appuser

ENV PYTHONUNBUFFERED=1
ENV POLLING_INTERVAL=60

ENTRYPOINT ["/app/entrypoint.sh"]
