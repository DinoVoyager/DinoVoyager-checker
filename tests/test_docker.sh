#!/bin/sh
set -e

IMAGE_NAME="dinovoyager-checker:test"

echo "=== 1. Testing Docker Build ==="
docker build -t "$IMAGE_NAME" .

echo "=== 2. Testing --help Flag ==="
docker run --rm "$IMAGE_NAME" --help

echo "=== 3. Testing Missing Required Params (Must exit with code 1) ==="
set +e
docker run --rm "$IMAGE_NAME"
EXIT_CODE=$?
set -e
if [ "$EXIT_CODE" -ne 1 ]; then
    echo "Expected exit code 1 on missing params, got $EXIT_CODE"
    exit 1
fi
echo "Missing params correctly exited with code 1"

echo "=== 4. Testing Non-Root User (UID 1000) ==="
USER_ID=$(docker run --rm --entrypoint id "$IMAGE_NAME" -u)
if [ "$USER_ID" != "1000" ]; then
    echo "Expected UID 1000, got $USER_ID"
    exit 1
fi
echo "Verified running as non-root UID 1000"

echo "=== 5. Testing ENV Parameters Acceptance ==="
docker run --rm -e SUBSCRIPTION_URL="https://example.com/sub" -e API_KEY="test_key" "$IMAGE_NAME" --help

echo "=== All Docker smoke tests passed successfully! ==="
