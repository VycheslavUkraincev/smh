#!/usr/bin/env bash
# SaveMyHistory — pull Path A / CodeFormer weights from DigitalOcean Spaces → /weights
# Required env: SPACES_KEY SPACES_SECRET SPACES_REGION SPACES_BUCKET
# Optional: SPACES_ENDPOINT DEST_DIR
# Never prints secrets.
set -euo pipefail

: "${SPACES_KEY:?SPACES_KEY required}"
: "${SPACES_SECRET:?SPACES_SECRET required}"
: "${SPACES_REGION:?SPACES_REGION required}"
: "${SPACES_BUCKET:?SPACES_BUCKET required}"

ENDPOINT="${SPACES_ENDPOINT:-https://${SPACES_REGION}.digitaloceanspaces.com}"
DEST="${DEST_DIR:-/weights}"

mkdir -p "$DEST" "$DEST/codeformer"

if command -v aws >/dev/null 2>&1; then
  export AWS_ACCESS_KEY_ID="$SPACES_KEY"
  export AWS_SECRET_ACCESS_KEY="$SPACES_SECRET"
  export AWS_DEFAULT_REGION="$SPACES_REGION"
  aws s3 sync "s3://${SPACES_BUCKET}/weights/path_a/" "$DEST/" \
    --endpoint-url "$ENDPOINT" --no-progress
  aws s3 sync "s3://${SPACES_BUCKET}/weights/codeformer/" "$DEST/codeformer/" \
    --endpoint-url "$ENDPOINT" --no-progress
  aws s3 cp "s3://${SPACES_BUCKET}/weights/manifest.json" "$DEST/manifest.json" \
    --endpoint-url "$ENDPOINT" --only-show-errors || true
  aws s3 cp "s3://${SPACES_BUCKET}/weights/README.md" "$DEST/README.md" \
    --endpoint-url "$ENDPOINT" --only-show-errors || true
else
  python3 - <<'PY'
import os, sys
try:
    import boto3
    from botocore.client import Config
except ImportError:
    sys.exit("Need awscli or boto3 to pull weights")

key=os.environ["SPACES_KEY"]; secret=os.environ["SPACES_SECRET"]
region=os.environ["SPACES_REGION"]; bucket=os.environ["SPACES_BUCKET"]
endpoint=os.environ.get("SPACES_ENDPOINT") or f"https://{region}.digitaloceanspaces.com"
dest=os.environ.get("DEST_DIR","/weights")
os.makedirs(dest, exist_ok=True)
os.makedirs(os.path.join(dest,"codeformer"), exist_ok=True)
s3=boto3.client("s3", region_name=region, endpoint_url=endpoint,
    aws_access_key_id=key, aws_secret_access_key=secret,
    config=Config(signature_version="s3v4"))

def pull_prefix(prefix, local_dir):
    token=None
    while True:
        kw=dict(Bucket=bucket, Prefix=prefix)
        if token: kw["ContinuationToken"]=token
        r=s3.list_objects_v2(**kw)
        for obj in r.get("Contents") or []:
            k=obj["Key"]
            if k.endswith("/"): continue
            rel=k[len(prefix):]
            if not rel: continue
            out=os.path.join(local_dir, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            print(f"GET {k} -> {out} ({obj['Size']} bytes)")
            s3.download_file(bucket, k, out)
        if not r.get("IsTruncated"): break
        token=r.get("NextContinuationToken")

pull_prefix("weights/path_a/", dest)
pull_prefix("weights/codeformer/", os.path.join(dest,"codeformer"))
for meta in ("weights/manifest.json","weights/README.md"):
    name=os.path.basename(meta)
    out=os.path.join(dest, name)
    try:
        s3.download_file(bucket, meta, out)
        print(f"GET {meta} -> {out}")
    except Exception as e:
        print(f"skip {meta}: {type(e).__name__}")
print("OK weights pulled into", dest)
PY
fi

echo "OK: weights available under $DEST"
ls -lah "$DEST" | head -50
