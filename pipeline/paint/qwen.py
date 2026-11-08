"""The real painting model: Qwen-Image-Edit-2511 with the inked style LoRA, on a CUDA GPU (pip extra `paint`).

Ported from Isometric Luxembourg's mdpaint.py. Same model, LoRA, prompt, scale and inputs; the window blending
lives in md_core.py. Each window is conditioned on its render crop outlined in red (2 px) and, by day, the layout
map; the conditioning for every window is computed once, before painting, so the text encoder and the transformer
never need to be on the GPU at the same time (that is what lets the smaller memory profiles work).

Memory profiles (--memory), peak GPU memory as estimated for one 1024 px window with two condition images:
  full     everything on the GPU, bf16. About 60 GB of weights: an 80 GB card (H100, A100 80GB, H200).
           Measured on Isometric Luxembourg (H200): 0.85-1.15 s per window with Lightning (8 steps, no guidance),
           about 1.45 s per window at 25 steps with guidance (two model calls per window).
  offload  bf16, one part at a time on the GPU (diffusers model offload): the transformer alone is about 41 GB,
           so a 48 GB card (RTX 6000 Ada, A6000, L40S), and 64 GB of system memory. Estimate, not yet measured.
  nf4      the transformer in 4 bits (bitsandbytes, about 12 GB) and model offload: a 24 GB card (RTX 3090, 4090),
           and 64 GB of system memory. Slower and slightly softer than bf16. Estimate, not yet measured.
"""
from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw

from paint.md_core import blend_tiles, grid

WIN = 1024


class QwenModel:
    name = "qwen"
    window = WIN

    def __init__(self, repo: str, lora: str, extra_lora: str | None = None, memory: str = "full",
                 mu: float | None = None):
        import torch
        from diffusers import QwenImageEditPlusPipeline

        if not torch.cuda.is_available():
            raise SystemExit("no CUDA GPU: the real painter needs one (try the dry run: paint.py dry-run)")
        self.torch = torch
        self.memory = memory
        self.mu = mu
        dt = torch.bfloat16
        kw = {}
        if memory == "nf4":
            from diffusers import BitsAndBytesConfig, QwenImageTransformer2DModel

            q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=dt)
            kw["transformer"] = QwenImageTransformer2DModel.from_pretrained(
                repo, subfolder="transformer", quantization_config=q, torch_dtype=dt)
        pipe = QwenImageEditPlusPipeline.from_pretrained(repo, torch_dtype=dt, **kw)
        pipe.load_lora_weights(os.path.dirname(lora), weight_name=os.path.basename(lora), adapter_name="style")
        names = ["style"]
        if extra_lora:
            pipe.load_lora_weights(os.path.dirname(extra_lora), weight_name=os.path.basename(extra_lora),
                                   adapter_name="extra")
            names.append("extra")
            pipe.set_adapters(names, [1.0] * len(names))
        assert "style" in pipe.transformer.peft_config, "style weights did not attach"
        if memory == "full":
            pipe.to("cuda")
        else:
            pipe.enable_model_cpu_offload()
        self.pipe = pipe
        self.dev = "cuda"
        self.channels = pipe.transformer.config.in_channels // 4
        self.cache: dict = {}
        C = self.channels
        self.lat_mean = torch.tensor(pipe.vae.config.latents_mean).view(1, C, 1, 1, 1)
        self.lat_std = torch.tensor(pipe.vae.config.latents_std).view(1, C, 1, 1, 1)

    # --- schedule ---------------------------------------------------------------------------------------------
    def sigmas(self, steps: int) -> list[float]:
        from diffusers.pipelines.qwenimage.pipeline_qwenimage_edit_plus import calculate_shift

        sc = self.pipe.scheduler.config
        mu = self.mu
        if mu is None:  # the pipeline's dynamic shift for one 1024 px window
            mu = calculate_shift((WIN // 16) ** 2, sc.get("base_image_seq_len", 256), sc.get("max_image_seq_len", 4096),
                                 sc.get("base_shift", 0.5), sc.get("max_shift", 1.15))
        self.pipe.scheduler.set_timesteps(sigmas=np.linspace(1.0, 1 / steps, steps), device=self.dev, mu=mu)
        sig = [float(s) for s in self.pipe.scheduler.sigmas]
        self.timesteps = self.pipe.scheduler.timesteps
        assert len(sig) == steps + 1 and sig[-1] == 0.0, sig
        return sig

    # --- conditioning, once per window ------------------------------------------------------------------------
    def condition(self, keys, crops, prompt, cfg: float) -> None:
        from diffusers.pipelines.qwenimage.pipeline_qwenimage_edit_plus import (
            CONDITION_IMAGE_SIZE,
            calculate_dimensions,
        )

        torch, pipe = self.torch, self.pipe
        self.cfg = cfg
        cw, ch = calculate_dimensions(CONDITION_IMAGE_SIZE, 1.0)
        t = WIN // 8
        keep = "cpu" if self.memory != "full" else self.dev
        for n, key in enumerate(keys):
            imgs = crops(key)
            win = imgs[0].copy()
            d = ImageDraw.Draw(win)  # full-view training examples are outlined in red all round (2 px)
            for k in range(2):
                d.rectangle((k, k, WIN - 1 - k, WIN - 1 - k), outline=(255, 0, 0))
            imgs = [win] + imgs[1:]
            c_imgs = [pipe.image_processor.resize(im, ch, cw) for im in imgs]
            v_imgs = [pipe.image_processor.preprocess(im, WIN, WIN).unsqueeze(2) for im in imgs]
            with torch.inference_mode():
                pe, pm = pipe.encode_prompt(image=c_imgs, prompt=prompt(key), device=self.dev, max_sequence_length=512)
                ne = nm = None
                if cfg > 1:
                    ne, nm = pipe.encode_prompt(image=c_imgs, prompt=" ", device=self.dev, max_sequence_length=512)
                _, il = pipe.prepare_latents(v_imgs, 1, self.channels, WIN, WIN, pe.dtype, self.dev, None,
                                             latents=torch.zeros(1))
            mv = lambda a: None if a is None else a.to(keep)  # noqa: E731
            self.cache[key] = (mv(pe), mv(pm), mv(ne), mv(nm), mv(il), [[(1, t // 2, t // 2)] * (1 + len(imgs))])
            if n % 10 == 0:
                print(f"conditioned {n + 1}/{len(keys)} windows", flush=True)

    def velocity(self, key, xw: np.ndarray, i: int) -> np.ndarray:
        torch, pipe = self.torch, self.pipe
        t = WIN // 8
        pe, pm, ne, nm, il, shapes = (None if a is None else (a.to(self.dev) if hasattr(a, "to") else a)
                                      for a in self.cache[key])
        C = self.channels
        with torch.inference_mode():
            lat = pipe._pack_latents(torch.from_numpy(np.ascontiguousarray(xw))[None].to(self.dev, torch.bfloat16),
                                     1, C, t, t)
            inp = torch.cat([lat, il], dim=1)
            ts = (self.timesteps[i] / 1000).expand(1).to(lat.dtype)

            def run(e, m, tag):
                with pipe.transformer.cache_context(tag):
                    return pipe.transformer(hidden_states=inp, timestep=ts, guidance=None,
                                            encoder_hidden_states_mask=m, encoder_hidden_states=e, img_shapes=shapes,
                                            attention_kwargs={}, return_dict=False)[0][:, : lat.size(1)]
            v = run(pe, pm, "cond")
            if self.cfg > 1:
                nv = run(ne, nm, "uncond")
                comb = nv + self.cfg * (v - nv)
                v = comb * (torch.norm(v, dim=-1, keepdim=True) / torch.norm(comb, dim=-1, keepdim=True))
            v = pipe._unpack_latents(v, WIN, WIN, 8)[0, :, 0]
        return v.float().cpu().numpy()

    # --- VAE, in overlapping 1024 px pieces, blended ----------------------------------------------------------
    def encode(self, img: Image.Image) -> np.ndarray:
        torch, pipe = self.torch, self.pipe
        W, H = img.width // 8, img.height // 8
        t = WIN // 8
        tiles = []
        for y, x in grid(H, W, t, t * 3 // 4, False):
            crop = img.crop((x * 8, y * 8, x * 8 + WIN, y * 8 + WIN))
            tt = pipe.image_processor.preprocess(crop, WIN, WIN).unsqueeze(2).to(self.dev, torch.bfloat16)
            with torch.inference_mode():
                z = pipe._encode_vae_image(tt, None)[0, :, 0]
            tiles.append((y, x, z.float().cpu().numpy()))
        return blend_tiles((self.channels, H, W), tiles, t, t // 4)

    def decode(self, z: np.ndarray) -> np.ndarray:
        torch, pipe = self.torch, self.pipe
        C, H, W = z.shape
        t = WIN // 8
        tiles = []
        for y, x in grid(H, W, t, t * 3 // 4, False):
            zt = torch.from_numpy(np.ascontiguousarray(z[:, y:y + t, x:x + t]))[None, :, None]
            zt = (zt * self.lat_std + self.lat_mean).to(self.dev, pipe.vae.dtype)
            with torch.inference_mode():
                im = pipe.vae.decode(zt, return_dict=False)[0][0, :, 0]
            tiles.append((y * 8, x * 8, ((im.float().cpu().numpy() + 1) / 2).clip(0, 1)))
        out = blend_tiles((3, H * 8, W * 8), tiles, WIN, WIN // 4)
        return (out.transpose(1, 2, 0) * 255).round().astype(np.uint8)

    def peak_memory_gb(self) -> float:
        return round(self.torch.cuda.max_memory_allocated() / 2**30, 1)
