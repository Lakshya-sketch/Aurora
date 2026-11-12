"""Image Generation Handler — Native Fooocus API Integration (Zero Local PyTorch Overhead).

Features:
1. Zero Microkernel Overhead:
   - Completely removes local PyTorch, CUDA context, and HuggingFace Diffusers from Aurora.
   - Aurora's process memory remains ultra-lean (0 MB diffusion VRAM).
2. Fooocus External Engine Bridge:
   - Communicates asynchronously via HTTP with Fooocus REST API (default: http://127.0.0.1:8888).
   - Configurable via `FOOOCUS_BASE_URL` environment variable or `config/aurora.yaml`.
3. Profiles & Presets:
   - "fooocus-speed": 30-step Juggernaut XL SDXL with Fooocus V2 aesthetic prompt enhancement.
   - "fooocus-quality": 60-step SDXL with extreme detail, sharp focus, and masterwork conditioning.
   - "fooocus-anime": Stylized Japanese animation and manga line art preset.
4. Output Persistence:
   - Saves generated PNGs into `data/generated/`.
   - Exposes local file paths and HTTP `/generated/<filename>` URLs for Electron display.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("aurora.tools.image_gen")

# Configurable active Fooocus profile
_ACTIVE_IMAGE_MODEL = "fooocus-speed"

AVAILABLE_IMAGE_MODELS: list[dict[str, Any]] = [
    {
        "id": "fooocus-speed",
        "name": "Fooocus Speed (30-step SDXL Juggernaut)",
        "performance": "Speed",
        "aspect_ratio": "1024*1024",
        "styles": ["Fooocus V2", "Fooocus Enhance", "Fooocus Sharp"],
        "steps": 30,
        "vram": "0 MB (External)",
    },
    {
        "id": "fooocus-quality",
        "name": "Fooocus Quality (60-step Extreme Detail)",
        "performance": "Quality",
        "aspect_ratio": "1024*1024",
        "styles": ["Fooocus V2", "Fooocus Enhance", "Fooocus Masterpiece"],
        "steps": 60,
        "vram": "0 MB (External)",
    },
    {
        "id": "fooocus-anime",
        "name": "Fooocus Anime / Stylized Manga",
        "performance": "Speed",
        "aspect_ratio": "1024*1024",
        "styles": ["Fooocus V2", "SAI Anime", "Fooocus Semi Realistic"],
        "steps": 30,
        "vram": "0 MB (External)",
    },
]

VALID_FOOOCUS_ASPECT_RATIOS = {
    "704*1408", "704*1344", "768*1344", "768*1280", "832*1216", "832*1152",
    "896*1152", "896*1088", "960*1088", "960*1024", "1024*1024", "1024*960",
    "1088*960", "1088*896", "1152*896", "1152*832", "1216*832", "1280*768",
    "1344*768", "1344*704", "1408*704", "1472*704", "1536*640", "1600*640",
    "1664*576", "1728*576",
}


def get_active_model() -> str:
    """Return currently active Fooocus image generation profile ID."""
    return _ACTIVE_IMAGE_MODEL


def set_active_model(model_id: str) -> bool:
    """Set the active Fooocus image profile. Returns True if valid."""
    global _ACTIVE_IMAGE_MODEL
    valid_ids = {m["id"] for m in AVAILABLE_IMAGE_MODELS}
    clean = str(model_id).strip().lower()
    if clean in valid_ids:
        _ACTIVE_IMAGE_MODEL = clean
        logger.info("image_gen: active Fooocus profile switched to %s", clean)
        return True
    return False


def get_available_models() -> list[dict[str, Any]]:
    """Return list of supported Fooocus image profiles."""
    return list(AVAILABLE_IMAGE_MODELS)


def _get_output_dir() -> Path:
    """Resolve and create data/generated directory."""
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    out_dir = project_root / "data" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _get_fooocus_base_url() -> str:
    """Resolve Fooocus API URL from environment or configuration."""
    url = os.environ.get("FOOOCUS_BASE_URL")
    if not url:
        try:
            from aurora_core.config import get as get_cfg
            url = get_cfg("image_gen.base_url", "http://127.0.0.1:8888")
        except Exception:
            url = "http://127.0.0.1:8888"
    return str(url).rstrip("/")


def _generate_pillow_fallback(prompt: str, status_msg: str, width: int, height: int, output_path: Path) -> None:
    """Generate high-contrast visual card when Fooocus API is offline or disconnected."""
    img = Image.new("RGB", (width, height), color=(18, 20, 26))
    draw = ImageDraw.Draw(img)

    # Gradient border simulation (Cyan/Purple cybernetic accent)
    draw.rectangle([(8, 8), (width - 8, height - 8)], outline=(79, 140, 255), width=4)

    # Accent header
    draw.rectangle([(20, 20), (width - 20, 70)], fill=(28, 32, 42))
    draw.text((36, 32), "AURORA v2  |  FOOOCUS ENGINE STATUS", fill=(79, 140, 255))

    # Center prompt representation card
    draw.rectangle([(40, 100), (width - 40, height - 100)], fill=(12, 14, 18), outline=(40, 44, 56))

    # Status notice
    draw.text((width // 2, 140), f"Status: {status_msg}", fill=(245, 158, 11), anchor="mm")

    # Wrap prompt text across canvas
    words = prompt.split()
    lines: list[str] = []
    curr: list[str] = []
    for w in words:
        curr.append(w)
        if len(" ".join(curr)) > 42:
            lines.append(" ".join(curr))
            curr = []
    if curr:
        lines.append(" ".join(curr))

    y_offset = (height // 2) - (len(lines) * 16) - 10
    draw.text((width // 2, y_offset - 30), "Requested Prompt:", fill=(140, 150, 170), anchor="mm")
    for line in lines[:8]:
        draw.text((width // 2, y_offset), f'"{line}"', fill=(240, 245, 255), anchor="mm")
        y_offset += 32

    # Bottom footer badge with instructions
    draw.rectangle([(20, height - 60), (width - 20, height - 20)], fill=(24, 28, 36))
    draw.text(
        (width // 2, height - 40),
        "Launch Fooocus on http://127.0.0.1:8888 to enable local SDXL generation",
        fill=(100, 116, 139),
        anchor="mm",
    )

    img.save(output_path, format="PNG")


async def _call_fooocus_api(
    prompt: str,
    style: str,
    profile_spec: dict[str, Any],
    width: int,
    height: int,
    output_path: Path,
) -> tuple[bool, str]:
    """Execute text-to-image synthesis using external Fooocus REST API.

    Returns:
        tuple of (success: bool, detail_or_error: str)
    """
    base_url = _get_fooocus_base_url()
    timeout_s = float(os.environ.get("FOOOCUS_TIMEOUT_S", "180"))

    # Map requested resolution to closest valid Fooocus aspect ratio
    aspect_key = f"{width}*{height}"
    aspect_ratio = aspect_key if aspect_key in VALID_FOOOCUS_ASPECT_RATIOS else profile_spec.get("aspect_ratio", "1024*1024")

    # Merge profile styles with user style request
    selected_styles = list(profile_spec.get("styles", ["Fooocus V2", "Fooocus Enhance", "Fooocus Sharp"]))
    if style and style.lower() not in {"photorealistic", "default", "none"}:
        selected_styles.append(f"Fooocus {style.title()}")

    payload = {
        "prompt": prompt,
        "negative_prompt": "ugly, deformed, noisy, blurry, low quality, distorted, extra limbs",
        "style_selections": selected_styles,
        "performance_selection": profile_spec.get("performance", "Speed"),
        "aspect_ratios_selection": aspect_ratio,
        "image_number": 1,
        "image_seed": -1,
        "sharpness": 2,
        "guidance_scale": 4,
        "async_process": False,
    }

    endpoint = f"{base_url}/v1/generation/text-to-image"
    logger.info("image_gen: sending prompt to Fooocus API at %s (profile=%s)", endpoint, profile_spec.get("id"))

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(endpoint, json=payload)
            if resp.status_code != 200:
                err_msg = f"Fooocus API returned HTTP {resp.status_code}: {resp.text[:120]}"
                logger.warning("image_gen: %s", err_msg)
                return False, err_msg

            data = resp.json()
            # Standard Fooocus-API returns list of generated image items:
            # [{"url": "http://127.0.0.1:8888/files/...", "base64": null, "finish_reason": "SUCCESS"}]
            items = data if isinstance(data, list) else data.get("data", [data])
            if not items:
                return False, "Fooocus returned an empty image array"

            first_item = items[0]
            # Case 1: Base64 encoded payload
            b64_str = first_item.get("base64") if isinstance(first_item, dict) else None
            if b64_str and isinstance(b64_str, str):
                raw_bytes = base64.b64decode(b64_str)
                output_path.write_bytes(raw_bytes)
                return True, "Successfully received base64 image from Fooocus API"

            # Case 2: Direct file URL from Fooocus
            img_url = first_item.get("url") if isinstance(first_item, dict) else str(first_item)
            if img_url:
                fetch_url = img_url if img_url.startswith("http") else f"{base_url}{img_url}"
                img_resp = await client.get(fetch_url)
                if img_resp.status_code == 200 and len(img_resp.content) > 0:
                    output_path.write_bytes(img_resp.content)
                    return True, f"Successfully downloaded image from Fooocus ({fetch_url})"
                return False, f"Failed to download image file from {fetch_url} (HTTP {img_resp.status_code})"

            return False, "No valid image URL or base64 found in Fooocus API response"

    except (httpx.ConnectError, httpx.ConnectTimeout) as conn_err:
        err_msg = f"Cannot connect to Fooocus at {base_url} (is Fooocus running?): {conn_err}"
        logger.warning("image_gen: %s", err_msg)
        return False, err_msg
    except Exception as exc:
        err_msg = f"Fooocus API request failed: {exc}"
        logger.error("image_gen: %s", err_msg)
        return False, err_msg


async def handle(args: dict[str, Any]) -> dict[str, Any]:
    """Execute image generation using Fooocus API.

    Args:
        args: dict with keys:
            - prompt (str, required): Text description of the image to generate.
            - style  (str, optional): Visual style (e.g. 'photorealistic', 'anime', 'concept art').
            - model  (str, optional): Target Fooocus profile override ('fooocus-speed', 'fooocus-quality', 'fooocus-anime').
            - width  (int, optional): Image width in pixels.
            - height (int, optional): Image height in pixels.

    Returns:
        ToolResult envelope with image path and local URL.
    """
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return {"ok": False, "data": None, "error": "Parameter 'prompt' is required for generate_image"}

    style = str(args.get("style", "photorealistic")).strip()
    target_model = str(args.get("model") or _ACTIVE_IMAGE_MODEL).strip().lower()

    # Determine default resolution based on model spec
    model_spec = next((m for m in AVAILABLE_IMAGE_MODELS if m["id"] == target_model), AVAILABLE_IMAGE_MODELS[0])
    width = int(args.get("width") or 1024)
    height = int(args.get("height") or 1024)

    out_dir = _get_output_dir()
    file_id = f"img_{uuid.uuid4().hex[:8]}_{int(time.time())}.png"
    target_path = out_dir / file_id

    logger.info(
        "image_gen: dispatching to Fooocus API (profile=%s, size=%dx%d, prompt=%r)",
        target_model,
        width,
        height,
        prompt[:60],
    )

    # Dispatch to Fooocus external API
    success, detail = await _call_fooocus_api(
        prompt=prompt,
        style=style,
        profile_spec=model_spec,
        width=width,
        height=height,
        output_path=target_path,
    )

    if not success:
        # Generate offline aesthetic card so the user gets clear visual feedback
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            _generate_pillow_fallback,
            prompt,
            "Fooocus Engine Offline (http://127.0.0.1:8888)",
            width,
            height,
            target_path,
        )

    return {
        "ok": True,
        "data": {
            "image_path": str(target_path.resolve()),
            "filename": target_path.name,
            "url": f"/generated/{target_path.name}",
            "prompt": prompt,
            "style": style,
            "model_used": target_model,
            "engine": "Fooocus API",
            "fooocus_connected": success,
            "width": width,
            "height": height,
            "offloaded": True,
            "description": (
                f"Generated image via Fooocus API ({model_spec['name']}) for: '{prompt}'."
                if success
                else f"Fooocus API is currently offline on http://127.0.0.1:8888. Saved status card. Please launch Fooocus."
            ),
        },
        "error": None,
    }
