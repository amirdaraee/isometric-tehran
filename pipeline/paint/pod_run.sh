#!/bin/bash
# On a rented RunPod pod (started by paint/runpod.py): install the painter, paint one block, write the exit code.
#   pod_run.sh <deadline-epoch> <block dir> <version> [painter args...]
# - A watchdog TERMINATES this pod at the deadline, whatever happens to the laptop (the painter pauses 10 min
#   before it with its state saved, and the laptop copies results back as soon as the job ends). It uses the
#   pod's own RUNPOD_API_KEY and RUNPOD_POD_ID, which RunPod puts in every pod's environment.
# - The python env goes in /venv on top of the image's torch; packages are the pipeline's `paint` extra.
# - The weights come from Hugging Face on first use (paint/config.py), into /hf.
# Output: /workspace/run.log (the painter's log), /workspace/rc (its exit code: 0 done, 3 paused, else failed).
set -u
DEADLINE="$1"; BLOCK_DIR="$2"; VERSION="$3"; shift 3
REPO=/workspace/repo
LOG=/workspace/run.log
export $(tr "\0" "\n" < /proc/1/environ | grep -E "^RUNPOD_(API_KEY|POD_ID)=" | xargs)
end_pod() {
  echo "GUARD $(date -u +%H:%M:%S) terminating pod: $1" >> "$LOG"
  curl -s -m 30 -H "Content-Type: application/json" -H "Authorization: Bearer $RUNPOD_API_KEY" \
    -d "{\"query\":\"mutation { podTerminate(input:{podId:\\\"$RUNPOD_POD_ID\\\"}) }\"}" https://api.runpod.io/graphql >> "$LOG"
}
( while [ "$(date +%s)" -lt "$DEADLINE" ]; do sleep 30; done; end_pod "deadline reached" ) > /workspace/watchdog.log 2>&1 &

if [ ! -x /venv/bin/python ]; then
  echo "SETUP $(date -u +%H:%M:%S)" >> "$LOG"
  python3 -m venv --system-site-packages /venv
  python3 - "$REPO/pipeline/pyproject.toml" > /workspace/requirements.txt <<'EOF'
import sys, tomllib
p = tomllib.load(open(sys.argv[1], "rb"))["project"]
print("\n".join(d for d in p["optional-dependencies"]["paint"] if not d.startswith("torch")))
print("\n".join(["numpy", "pillow", "scipy", "opencv-python-headless"]))
EOF
  /venv/bin/pip install -q --no-cache-dir -r /workspace/requirements.txt >> /workspace/setup.log 2>&1 \
    || { echo "SETUP FAILED (see /workspace/setup.log)" >> "$LOG"; echo 1 > /workspace/rc; exit 1; }
  /venv/bin/python -c "import torch;assert torch.cuda.is_available();print('torch',torch.__version__,torch.version.cuda)" \
    >> "$LOG" 2>&1 || { echo "SETUP FAILED (no CUDA)" >> "$LOG"; echo 1 > /workspace/rc; exit 1; }
  echo "SETUP DONE $(date -u +%H:%M:%S)" >> "$LOG"
fi

export HF_HOME=/hf HF_XET_HIGH_PERFORMANCE=1
cd "$REPO/pipeline"
/venv/bin/python -m paint.painter --block-dir "$BLOCK_DIR" --version "$VERSION" --deadline "$DEADLINE" "$@" >> "$LOG" 2>&1
RC=$?
echo $RC > /workspace/rc
echo "PAINTER EXIT $RC $(date -u +%H:%M:%S)" >> "$LOG"
