"""Paint on a GPU rented from RunPod, on your own account: one pod per run, terminated at the end.

You need: a RunPod account with credit, your own API key in the environment (RUNPOD_API_KEY; create one at
https://www.runpod.io/console/user/settings), an SSH key pair (PAINT_SSH_KEY, default ~/.ssh/id_ed25519; its public
half is given to the pod), and ssh and rsync. The key is only read from the environment and sent to RunPod's API in
a request header; it is never written to a file or passed on a command line.

What a run does (paint.py run --backend runpod):
  1. creates one pod (paint.json runpod: image, GPU types in order of preference, community or secure cloud) and
     refuses it if it costs more per hour than --max-price;
  2. copies the painter code and the block's inputs to it (ssh and rsync, through a per-pod known_hosts file:
     trust on first use);
  3. starts pod_run.sh there, which installs the painter, downloads the weights from Hugging Face, paints, and
     terminates its own pod at the deadline whatever happens to your laptop;
  4. polls progress, copies the canvas (and the saved state, if it paused) back, and deletes the pod.

Safety: the cost cap is the deadline (estimate x 1.5 + 15 min) x the pod's price, printed before anything is
rented, and nothing is rented without --yes. The pod deletes itself at the deadline; the laptop deletes it as soon
as the job ends, on Ctrl-C and on errors. If the laptop dies, check https://www.runpod.io/console/pods.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import ROOT
from paint import config

API = "https://rest.runpod.io/v1"
POD_DIR = Path(os.environ.get("PAINT_POD_DIR", Path.home() / ".cache" / "isometric-tehran" / "pods"))
REMOTE = "/workspace/repo"


def api_key() -> str:
    k = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not k:
        raise SystemExit("set RUNPOD_API_KEY to your own RunPod API key (https://www.runpod.io/console/user/settings)")
    return k


def _call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 (fixed https endpoint)
            txt = r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"RunPod API {method} {path}: {e.code} {e.read().decode()[:300]}") from None
    return json.loads(txt) if txt.strip() else {}


def ssh_key() -> tuple[Path, str]:
    key = Path(os.environ.get("PAINT_SSH_KEY", Path.home() / ".ssh" / "id_ed25519")).expanduser()
    pub = key.with_name(key.name + ".pub")
    if not key.exists() or not pub.exists():
        raise SystemExit(f"no SSH key pair at {key} (+ .pub): make one with ssh-keygen -t ed25519, or set PAINT_SSH_KEY")
    return key, pub.read_text().strip()


class Pod:
    def __init__(self, name: str, max_price: float):
        self.name, self.max_price = name, max_price
        self.id: str | None = None
        self.price = 0.0
        self.t0 = time.time()
        self.cfg = POD_DIR / f"{name}.ssh"

    # --- lifecycle ----------------------------------------------------------------------------------------------
    def create(self) -> None:
        rp = config.settings()["runpod"]
        key, pub = ssh_key()
        last = None
        for gpu in rp["gpuTypes"]:
            for cloud in rp["cloudTypes"]:
                body = {"name": self.name, "imageName": rp["image"], "gpuTypeIds": [gpu], "gpuCount": 1,
                        "cloudType": cloud, "containerDiskInGb": rp["containerDiskGb"], "volumeInGb": 0,
                        "ports": ["22/tcp"], "supportPublicIp": True, "env": {"PUBLIC_KEY": pub}}
                try:
                    pod = _call("POST", "/pods", body)
                except RuntimeError as e:
                    last = e
                    print(f"  {gpu} ({cloud.lower()}): not available", flush=True)
                    continue
                self.id = pod["id"]
                self.t0 = time.time()
                self.price = float(pod.get("costPerHr") or 0)
                print(f"pod {self.id}: {gpu}, {cloud.lower()} cloud, ${self.price:.2f}/h", flush=True)
                if self.price > self.max_price or self.price <= 0:
                    print(f"  over --max-price ${self.max_price:.2f}/h (or no price given): deleting it", flush=True)
                    self.delete()
                    continue
                self._ssh_config(key)
                return
        raise SystemExit(f"no GPU available at up to ${self.max_price:.2f}/h ({last})")

    def _ssh_config(self, key: Path) -> None:
        ip = port = None
        for _ in range(60):  # up to 10 min for the pod to get a public address
            p = _call("GET", f"/pods/{self.id}")
            ip, port = p.get("publicIp"), (p.get("portMappings") or {}).get("22")
            if ip and port:
                break
            time.sleep(10)
        if not (ip and port):
            raise RuntimeError("the pod got no public SSH address")
        POD_DIR.mkdir(parents=True, exist_ok=True)
        kh = POD_DIR / f"{self.name}.known_hosts"
        kh.unlink(missing_ok=True)  # a new pod: trust its key on first use, check it on every later connection
        self.cfg.write_text(f"Host pod\n  HostName {ip}\n  Port {port}\n  User root\n  IdentityFile {key}\n"
                            f"  StrictHostKeyChecking accept-new\n  UserKnownHostsFile {kh}\n")

    def delete(self) -> None:
        if not self.id:
            return
        for _ in range(5):
            try:
                _call("DELETE", f"/pods/{self.id}")
                print(f"pod {self.id} deleted; about ${self.cost():.2f} spent", flush=True)
                self.id = None
                return
            except Exception as e:  # noqa: BLE001 (retry anything; the pod also deletes itself at its deadline)
                print("  could not delete the pod yet:", e, flush=True)
                time.sleep(10)
        print(f"COULD NOT DELETE POD {self.id}: delete it at https://www.runpod.io/console/pods", flush=True)

    def cost(self) -> float:
        return (time.time() - self.t0) / 3600 * self.price

    # --- remote work ----------------------------------------------------------------------------------------------
    def ssh(self, cmd: str, capture: bool = False):
        args = ["ssh", "-F", str(self.cfg), "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=30", "pod", cmd]
        for _ in range(4):
            r = subprocess.run(args, capture_output=True, text=True)
            if r.returncode != 255:  # 255: ssh itself failed, retry
                return r.stdout if capture else r.returncode
            time.sleep(10)
        return "" if capture else 255

    def rsync(self, src: str, dest: str, extra: list[str] | None = None) -> int:
        for _ in range(4):
            rc = subprocess.call(["rsync", "-a", "--partial", "--timeout=120", *(extra or []), "-e",
                                  f"ssh -F {self.cfg}", src, dest])
            if rc == 0:
                return 0
            time.sleep(5)
        return rc

    def wait_ssh(self, wait_s: int = 900) -> None:
        t = time.time()
        while time.time() - t < wait_s:
            if self.ssh("true") == 0:
                return
            time.sleep(15)
        raise RuntimeError("the pod did not answer on SSH")


def run(block_dir: Path, version: str, painter_args: list[str], hours: float, max_price: float) -> int:
    """Paint one prepared block on a new pod; returns the painter's exit code (0 done, 3 paused)."""
    bid = block_dir.name
    pod = Pod(f"isometric-tehran-{bid}-{version}", max_price)
    rel = block_dir.resolve().relative_to(ROOT)
    try:
        pod.create()
        pod.wait_ssh()
        deadline = int(time.time() + hours * 3600)
        pod.ssh(f"mkdir -p {REMOTE}/pipeline {REMOTE}/{rel}")
        pod.rsync(str(ROOT / "city.json"), f"pod:{REMOTE}/")
        pod.rsync(str(ROOT / "pipeline" / "common.py"), f"pod:{REMOTE}/pipeline/")
        pod.rsync(str(ROOT / "pipeline" / "pyproject.toml"), f"pod:{REMOTE}/pipeline/")
        pod.rsync(str(ROOT / "pipeline" / "paint") + "/", f"pod:{REMOTE}/pipeline/paint/",
                  ["--exclude", "__pycache__", "--exclude", "submissions"])
        pod.rsync(str(block_dir) + "/", f"pod:{REMOTE}/{rel}/", ["--exclude", "live_*"])
        args = " ".join(shlex.quote(a) for a in [str(deadline), f"{REMOTE}/{rel}", version, *painter_args])
        pod.ssh(f"rm -f /workspace/rc; setsid nohup bash {REMOTE}/pipeline/paint/pod_run.sh {args} "
                f"> /workspace/pod_run.out 2>&1 < /dev/null &")
        print(f"painting on the pod; deadline {time.strftime('%H:%M', time.localtime(deadline))} "
              f"(cap ${hours * pod.price:.2f}). Ctrl-C deletes the pod.", flush=True)
        last = ""
        while True:
            time.sleep(30)
            rc = str(pod.ssh("cat /workspace/rc 2>/dev/null", capture=True)).strip()
            line = str(pod.ssh("tail -n 1 /workspace/run.log 2>/dev/null", capture=True)).strip()
            if line and line != last:
                print(f"  [{pod.cost():.2f} $] {line}", flush=True)
                last = line
            if rc:
                break
            if time.time() > deadline + 300:
                print("past the deadline: stopping", flush=True)
                rc = "124"
                break
        back = ["--include", "canvas_*.png", "--include", "live_*/", "--include", "live_*/*", "--include", "state_*/",
                "--include", "state_*/*", "--exclude", "*"]
        pod.rsync(f"pod:{REMOTE}/{rel}/", str(block_dir) + "/", back)
        pod.rsync("pod:/workspace/run.log", str(block_dir / f"pod_{version}.log"))
        return int(rc)
    finally:
        pod.delete()
