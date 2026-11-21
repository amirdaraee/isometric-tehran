# Painting the map

The map starts as a 3D render (`pipeline/render.py`). Painting turns it into an inked illustration, block by
block, with the same model and method as [Isometric Luxembourg](https://github.com/amirdaraee/isometric-luxembourg).
Anyone with a big enough GPU, or a few dollars of rented GPU time on their own account, can paint a block and
send it in. This page explains how painting works, what it needs, and the whole flow from claiming a block to
seeing it on the map.

**Status:** the pipeline is ready and tested with a stand-in model; no block has been painted yet. The first
block, b00 (Golestan Palace and the Arg), is the paint pilot (ROADMAP.md, stage 2): the maintainer paints it
first, to set the quality bar and settle the open questions below. Until b00 is accepted, please try the dry run
and send feedback, but hold off painting.

## Contents

- [How painting works](#how-painting-works)
- [Hardware, time and cost](#hardware-time-and-cost)
- [Setup](#setup)
- [Try it without a GPU: the dry run](#try-it-without-a-gpu-the-dry-run)
- [Paint a block: claim, paint, submit](#paint-a-block-claim-paint-submit)
- [Prompts](#prompts)
- [Judging quality](#judging-quality)
- [Review (maintainers)](#review-maintainers)
- [Licences](#licences)
- [Files](#files)

## How painting works

**The model.** [Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511), an image-editing
diffusion model, with a style LoRA trained for Isometric Luxembourg (inked, warm, hand-drawn; style v2, step
2000). An optional 8-step "Lightning" LoRA ([lightx2v](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning))
makes it about three times faster; Luxembourg's day map was painted with it. A second "light" LoRA turns a painted
day block into its golden-hour or night version. The weights are downloaded from the Hugging Face Hub on first use
(`pipeline/paint/paint.json` names the repos).

**The scale.** The model paints 1024 px windows. 600 render px (150 m) become 512 model px, the scale the style
was trained at, so a window covers about 300 m.

**Whole-block painting** (`paint/md_core.py`, MultiDiffusion with a shifted grid, after
[Pixel Singapore](https://www.yadongxie.com/blog/how-pixel-singapore-was-made)). The whole block is one latent
image. At every denoising step, overlapping 1024 px windows (768 px apart) each predict their part, conditioned on
their crop of the render (outlined in red, as in training) and the layout map (one flat colour per kind of
surface). The predictions are blended with weights that fade towards each window's edges, and one step moves the
whole block. Every other step the window grid shifts by half a window, so no window edge stays in place and no
seam can form inside a block.

**Blocks.** The map (20480 × 10240 px) is cut into blocks of 4800 render px (4096 model px) that overlap by
1200 px, exactly one window. That is smaller than Luxembourg's 6000 px blocks, so a block fits an evening on a
24 GB card, and there are more blocks to share out. Block b00 is centred on Golestan Palace (`openingPlace` in
`city.json`), and the others are numbered outward from it. Blocks with almost no map in them are left out: 15
blocks in all.

**Neighbours are held.** A block is painted after the overlapping blocks with lower numbers. Their pixels are
held (RePaint, adapted to flow matching): after every step the known part is reset to the finished paint, noised
to the current level, so the new block grows out of its neighbours. A 64 px free band lets the new paint blend in,
and the finished pixels are feathered back at the end. The empty map edge is held too, so it costs nothing.

**Resume.** The state is saved after every step. A run that stops (Ctrl-C, a deadline, a pod that ends) carries
on from the last step when you run the same command again.

**Putting blocks together.** Accepted blocks are laid over the render in order with a 600 px feathered edge
(`paint/blend.py`, used by `build_tiles.py` and `build_variant_tiles.py`). Neighbours hold the same pixels in
their overlap, so the fade is invisible; towards unpainted areas the paint fades into the render.

## Hardware, time and cost

Painting needs an NVIDIA GPU with CUDA. The painter has three memory profiles (`--memory`):

| Profile | GPU memory | Cards | How | Status |
|---|---|---|---|---|
| `full` | 80 GB | H100, H200, A100 80GB | everything on the GPU, bf16 | the Luxembourg setup |
| `offload` | 48 GB, plus 64 GB system RAM | RTX 6000 Ada, L40S, RTX A6000 | one part on the GPU at a time (the transformer alone is about 41 GB) | estimate, not yet run |
| `nf4` | 24 GB, plus 64 GB system RAM | RTX 4090, RTX 3090 | the transformer in 4 bits (bitsandbytes), one part at a time | estimate, not yet run; slightly softer than bf16 |

Weights: about 58 GB to download the first time (transformer about 41 GB, text encoder about 17 GB, by their
parameter counts), so keep about 80 GB of disk free for the Hugging Face cache and the environment.

**Measured on Isometric Luxembourg** (H200, 6000 px blocks with 49 windows per step):

| Setting | Seconds per window | Painting time | Cost |
|---|---|---|---|
| Lightning, 8 steps, no guidance (day) | 0.85 to 1.15 | 5.9 min per block | about $0.75 per block on Modal |
| 25 steps, guidance 3 | 1.44 | 31 min per block | about $3 |
| Light LoRA, 15 steps, guidance 3 (versions) | 1.0 to 1.3 | 2 to 11 min (partly empty blocks) | $0.17 to $0.73 on RunPod at $3.99/h |

Peak GPU memory was not logged in Luxembourg; the painter now records it (`peak_gpu_gb` in the status file and
the submission). **Measured on Tehran's b00** (RunPod A100 80GB PCIe, `full`): 2.32 s per window, 9.6 min of
painting for 244 model calls, 63.6 GB peak GPU memory; about 1 minute of setup and 3 minutes to download and load
the model, so 13 minutes from pod start to canvas.

**Estimated for a Tehran block** (4800 px; 25 or 36 windows per step, about 244 model calls for a full block at
8 steps, fewer where neighbours are held or the map is empty). The H200 and A100 speeds are measured; every other row is
an estimate from relative GPU speed, including about 4 minutes to load the model:

| GPU | Profile | Minutes per day block |
|---|---|---|
| H200 | full | about 8 (speed measured) |
| H100 80GB | full | about 9 |
| A100 80GB | full | about 13 (speed measured) |
| L40S, RTX 6000 Ada | offload | about 18 |
| RTX A6000 | offload | about 24 |
| RTX 4090 | nf4 | about 28 |
| RTX 3090 | nf4 | about 45 |

`python paint/paint.py estimate b03` prints this for a given block (it counts the windows that really run).
Golden and night use 15 steps with guidance: about 3 times as long.

**On a rented GPU** (RunPod): one pod on your own account, about 15 minutes of setup (installing, downloading the
weights) plus the painting, so about 25 to 30 minutes for a day block: roughly $1 to $1.50 at $2 to $3 per hour.
Prices change; `paint.py run` prints the expected cost and the cap before renting anything.

## Setup

    git clone https://github.com/<you>/isometric-tehran    # your fork
    cd isometric-tehran/pipeline
    uv venv && uv pip install -r pyproject.toml --extra fetch --extra dev      # planning, dry run, review
    uv pip install -r pyproject.toml --extra paint                           # the painter (local GPU only)
    uv pip install -r pyproject.toml --extra paint-nf4                       # and for 24 GB cards

Then render the map, so the painter gets the sharp render and the layout map (README, "How the map is made"):

    .venv/bin/python fetch_data.py all && .venv/bin/python build_scene.py && .venv/bin/python render.py

For a rented GPU you only need the first install line, ssh and rsync, an SSH key (`~/.ssh/id_ed25519`, or set
`PAINT_SSH_KEY`), and a [RunPod](https://www.runpod.io) account with credit and **your own API key** in your
environment:

    export RUNPOD_API_KEY=...        # from runpod.io > Settings > API keys; never commit it, never share it

The key is read from the environment only and sent to RunPod's API in a request header. The project never asks
for it and never has a shared key: you pay for your own GPU time.

The style and light LoRAs are on the Hugging Face Hub at
[amirdaraee/isometric-ink-style](https://huggingface.co/amirdaraee/isometric-ink-style) and download on first use.
To work offline, put the `.safetensors` files in a folder and set `PAINT_LORA_DIR` to it.

## Try it without a GPU: the dry run

    cd pipeline
    .venv/bin/python paint/paint.py dry-run

About ten seconds on a laptop CPU, with no GPU, no weights, no network and no torch. It runs every step of a
contribution on two small blocks round Golestan Palace, with a stand-in model that "paints" a posterised copy of
the render: plan, prepare inputs, paint (pausing after 3 steps and resuming), submit (checksum and submission
JSON), review (download, checksum, position, held pixels, seams), paint the neighbour holding the first block,
blend both into the map with feathered edges, and build a small tile pyramid. Everything goes into
`data/work/paint-dry/`; `before_after.jpg` shows the render and the stand-in's result side by side. The same flow
runs in CI (`tests/test_paint_dry_run.py`).

## Paint a block: claim, paint, submit

### 1. Pick and claim a block

    .venv/bin/python paint/blocks.py plan       # data/work/blocks.json and blocks.jpg (a map of the blocks)
    .venv/bin/python paint/blocks.py status     # done, claimed, ready or waiting, per block

Pick a **ready** block: free, with every earlier neighbour done. Open a
[Paint a block](https://github.com/amirdaraee/isometric-tehran/issues/new?template=paint-block.yml) issue with the
block id, the version (day, golden or night), your hardware and when you expect to finish. The maintainer adds
your claim to `pipeline/paint/claims.json` (or you can send a pull request adding it yourself):

    {"block": "b03", "version": "day", "by": "your-github-user", "since": "2026-11-08", "issue": 12}

A claim with no submission after 14 days is shown as stale and may be passed on; just say on the issue if you
need longer. Golden and night versions of a block need its day version first.

### 2. Paint it

On your own GPU (free):

    .venv/bin/python paint/paint.py run b03 --backend local                 # 80 GB card
    .venv/bin/python paint/paint.py run b03 --backend local --memory nf4    # 24 GB card

On a rented GPU on your RunPod account (paid):

    .venv/bin/python paint/paint.py run b03 --backend runpod                # prints the cost, rents nothing
    .venv/bin/python paint/paint.py run b03 --backend runpod --yes          # rents one pod and paints
    #   --max-price 3.0   the most you will pay per hour (default $3.00); dearer pods are refused
    #   --gpu 'NVIDIA A100 80GB PCIe'   GPU types to try, in order (repeat it; default paint.json runpod.gpuTypes)
    .venv/bin/python paint/paint.py run b05 b07 --backend runpod --yes --max-cost 2   # several blocks, one pod

Several blocks in one `run` are painted in order on **one pod**: the weights are downloaded once (3 to 9 minutes
on RunPod), and each block is prepared after the ones before it came back, so it holds them. Each block moves the
pod's deadline to its own, `--max-cost` refuses to start a block that could take the pod past that many dollars,
and the session stops at the first block that fails.

`run` downloads the accepted canvases of the block's neighbours (checking their checksums), writes the painter's
inputs into `data/work/paint/b03/` (render crop, layout map, held neighbours, prompts), paints, and cleans up the
result (magenta specks in versions, the pool colour). The painted canvas is `data/work/paint/b03/canvas_day.png`,
4096 px square.

What happens on RunPod: one pod is created on your account (the first available GPU from `paint.json` →
`runpod.gpuTypes`, all 80 GB cards). The code and the block's inputs are copied to it over SSH, the pod installs
the painter, downloads the weights from Hugging Face and paints. The pod **terminates itself at a deadline** (the
expected time × 1.5 + 15 minutes), whatever happens to your computer; your computer deletes it as soon as the job
ends, on errors and on Ctrl-C. The deadline × the pod's price is the most a run can cost, and it is printed
before anything is rented. If a run pauses at the deadline, the saved state is copied back and the same command
resumes it. If your computer crashes mid-run, check https://www.runpod.io/console/pods once.

Look at the canvas before you submit it (see [Judging quality](#judging-quality)). To paint it again with
another seed, delete `data/work/paint/b03/state_day/` and run again with `--force`.

### 3. Submit it

The canvas is about 20 to 40 MB, too big for git. It goes in a release on **your fork**; the pull request only
adds a small JSON file that says where it is, its checksum and how it was painted.

    .venv/bin/python paint/paint.py submit b03 --by your-github-user --upload

This writes `pipeline/paint/submissions/b03-day.json` (checksum, size, block position, the neighbours it held,
model and settings, time and peak GPU memory) and, with `--upload`, creates a release `paint-b03-day` on your
fork with the canvas as `b03-day.png` (using your own `gh` login). Without `--upload` it prints the `gh` command;
with `--url` you can point at another GitHub release (only `https://github.com/<user>/<repo>/releases/download/...`
URLs are accepted).

Then open a pull request that adds only that JSON file, and link your issue. The `pipeline` check validates it,
and the `repo-checks` job fails a submission pull request that changes anything other than
`pipeline/paint/submissions/*.json`: send code or prompt changes in a separate pull request.
By submitting you confirm that you painted it with this pipeline and offer it under the art licence (the
submission says `"licence": "CC BY-NC 4.0", "agree": true`).

### 4. Review and merge

The maintainer runs the checks below, looks at the block on the map, and merges. Merged means accepted: the block
shows as done, and the blocks next to it become ready. The maintainer then copies the canvas into this
repository's `paint-blocks` release, so the art does not depend on your fork staying up, and rebuilds the tiles.

## Prompts

- `pipeline/paint/prompts/day.txt` is the base prompt. Its first sentence is the style model's training prompt
  and must stay as it is; `{style}` is replaced with the trigger the LoRA was trained with (`trigger` in
  `paint.json`). The rest describes old Tehran: brick, turquoise and yellow tiles, iwans, domes, courtyards with
  pools, plane trees.
- `golden.txt` and `night.txt` are the light LoRA's prompts for the versions.
- `pipeline/paint/prompts/landmarks.json` holds a hint per landmark (the slugs of `pipeline/landmarks.json`). A
  window that contains a landmark's pin gets that hint after the base prompt, so the Shams-ol-Emareh window is
  told about its two towers, and a window of plain houses is not. Hints are the easiest thing to improve: describe
  what is seen from above and the south-east (shape, material, colour), one or two sentences, and send a pull
  request.

The style model learnt from a fixed training prompt, so long or very different prompts may weaken the style.
Change one thing at a time and compare.

## Judging quality

Put the canvas next to the render crop (`data/work/paint/b03/src.png`) and check:

- **Follows the render.** Every building where the render has one, the same footprint and height; streets,
  squares and parks in place. No invented buildings, pools or trees over streets.
- **Base-art rules.** Nothing that moves: no cars, people or birds. Still water. Summer trees.
- **Style.** Inked outlines, warm colours, readable roofs and courtyards; it should look like Isometric
  Luxembourg's drawing, not a photo or a 3D render.
- **No artefacts.** No red outline left at the edges, no text or watermarks, no smears or melted buildings, no
  magenta specks.
- **Landmarks** recognisable: Golestan Palace's long pool and Shams-ol-Emareh's towers, the mosques' domes and
  iwans, the bazaar's vaulted roofs.
- **Neighbours.** Where it meets finished blocks, no line, colour jump or doubled building.

## Review (maintainers)

**Run the review tools only from a trusted checkout of `main`, never from the pull request's branch.** A pull
request is someone else's code: checking it out and running `paint/review.py` (or any script, or `pytest`) from it
would run whatever it contains on your machine. Copy in only the submission JSON, which the tools treat as
untrusted data:

    git checkout main && git pull
    git fetch origin pull/<PR number>/head:pr-<PR number>
    git show pr-<PR number>:pipeline/paint/submissions/b03-day.json > /tmp/b03-day.json
    cd pipeline
    .venv/bin/python paint/review.py check /tmp/b03-day.json                         # download, verify, check
    .venv/bin/python paint/review.py fetch && .venv/bin/python paint/review.py tiles  # after merging
    .venv/bin/python paint/review.py publish b03 --yes                               # copy to this repo's release

First confirm that the pull request changes only `pipeline/paint/submissions/*.json` (the `repo-checks` job
fails it otherwise; CI runs on `pull_request`, with a read-only token and no secrets, never
`pull_request_target`).

What the tools accept from a submission: a block id of the current plan (`b` and two digits), a known version,
a file named `<block>-<version>.json`, and a canvas URL that is an https GitHub release download (redirects
followed only to GitHub's asset hosts). Every file they write must resolve inside `data/work/paint/`.

`check` downloads the canvas (refusing anything over 120 MB or with the wrong checksum) and checks:

- **size and plan:** 4096 × 4096, painted against the current block plan, at the block's position;
- **position:** the empty map edge exactly where the render has it, and the drawing's edges lined up with the
  render's (phase correlation, under 2 px of shift);
- **held neighbours:** away from the free band, the canvas equals the accepted neighbours (a large difference
  means it was painted against other neighbours);
- **seams:** across the held area's edge, colour (CIELAB) does not jump more than it changes nearby, along a
  straight run (`paint/blend.py`);
- magenta specks, for the versions.

It writes a report and a preview with the held edge and any seams marked to `data/work/paint/review/`, and exits
1 if a check fails. The checks catch technical problems; quality is judged by eye on the map: `review.py tiles`
rebuilds the tiles with every accepted block, then `cd web && npm run dev`.

After merging, `publish` uploads the canvas to the `paint-blocks` release of this repository with `gh`; put the
URL it prints into the submission JSON in a follow-up commit. When the painted tiles pass the 150 MB budget of
`scripts/check_repo.py`, they move to release assets or object storage (README, "Publishing").

## Licences

| What | Licence |
|---|---|
| Base model, Qwen-Image-Edit-2511 (Qwen team, Alibaba) | Apache-2.0 |
| Lightning LoRA (lightx2v) | Apache-2.0 (see its model card) |
| Style and light LoRAs ([amirdaraee/isometric-ink-style](https://huggingface.co/amirdaraee/isometric-ink-style)) | CC BY-NC 4.0 |
| Painted blocks and the painted map | CC BY-NC 4.0 ([LICENSE-ART.md](../LICENSE-ART.md)) |
| The painting code (`pipeline/paint/`) | MIT ([LICENSE](../LICENSE)) |

The style LoRAs are for non-commercial use, like the art. **By submitting a painted block you agree that it is
published under CC BY-NC 4.0** as part of Isometric Tehran, with credit to the project; your name stays in the
submission file and the git history. The map is made from open data, whose attribution travels with the art
(README, "Data and credits").

## Files

| File | What |
|---|---|
| `pipeline/paint/paint.json` | model and LoRA repos, block size, versions (steps, guidance), RunPod settings, review thresholds |
| `pipeline/paint/blocks.py` | `plan`, `status`, `prepare`, `fetch` |
| `pipeline/paint/paint.py` | `estimate`, `run` (local or RunPod), `submit`, `dry-run` |
| `pipeline/paint/painter.py` | paints one prepared block (runs where the GPU is) |
| `pipeline/paint/md_core.py` | whole-block painting: windows, shifted grid, held pixels, resume (numpy) |
| `pipeline/paint/qwen.py` | the real model and its memory profiles (torch, diffusers) |
| `pipeline/paint/stub.py` | the CPU stand-in for the dry run and tests |
| `pipeline/paint/runpod.py`, `pod_run.sh` | one pod on your own RunPod account; the pod's side with its deadline |
| `pipeline/paint/post.py` | despeckle and pool colour |
| `pipeline/paint/blend.py` | feathered blending and the review checks |
| `pipeline/paint/review.py` | `check`, `fetch`, `blend`, `tiles`, `publish` |
| `pipeline/paint/claims.json`, `submissions/` | who paints what, and the accepted blocks |
| `pipeline/paint/prompts/` | base prompts and landmark hints |
| `pipeline/build_variant_tiles.py` | golden and night tile pyramids from their painted blocks |
