"""
reel_html.py — render a reel from a designed HTML scene instead of PIL drawing.

The PIL renderer (reel.py / reel_render.py) is right for typographic reels: it's
dependency-light and fast. But a product demo needs the actual UI — real layout,
real shadows, real type — and redrawing the app in PIL would both cost days and
drift from the real thing the moment the app changes.

So the scene is authored as a page (frontend/reel-scene.html), driven by a
deterministic seek(t), screenshotted frame by frame with headless Chromium, and
piped into ffmpeg with the music bed muxed in the same pass. No screen recording,
no editor, no human.

JPEG frames rather than PNG: ~45ms/frame vs ~390ms, which is the difference
between a 15-second render and a two-minute one, and h264 re-encodes anyway.
"""

import json
import os
import shutil
import subprocess

W, H = 1080, 1920
# 60, not 30. A camera push from 1.0 to 1.3 over a second is 18 frames at 30fps
# and the steps are visible — screenshot-stepped motion has no motion blur to
# hide them the way a real screen recording does. Doubling the frames is the
# cheapest fix available and Instagram plays 60fps natively.
FPS = 60

SCENE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "frontend", "reel-scene.html")


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise RuntimeError("ffmpeg not found. Install it, or pip install imageio-ffmpeg")


def render(spec: dict, out_path: str, audio_path: str = None, crf: int = 20,
           fps: int = FPS, progress=None) -> str:
    """Render a scene spec to a publish-ready MP4.

    spec drives frontend/reel-scene.html via window.REEL_SPEC — see DEMOS in
    reel_demos.py for the shape.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright missing. pip install playwright && playwright install chromium")

    dur = float(spec.get("dur", 24.0))
    total = int(dur * fps)

    has_audio = bool(audio_path) and os.path.exists(audio_path)
    cmd = [_ffmpeg(), "-y", "-loglevel", "error",
           "-f", "image2pipe", "-vcodec", "mjpeg", "-r", str(fps), "-i", "-"]
    if has_audio:
        cmd += ["-i", audio_path]
    # 'fast', not 'medium': at 1080x1920x60 the encoder competes with the
    # screenshot loop for the same cores, and starving Playwright is what makes
    # a capture time out mid-render. The quality difference at this crf is not
    # visible after Instagram re-encodes anyway.
    cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
            "-maxrate", "8M", "-bufsize", "16M", "-movflags", "+faststart"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "44100", "-shortest"]
    cmd += [out_path]

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--force-color-profile=srgb",
                                              "--disable-lcd-text",
                                              "--hide-scrollbars"])
            page = browser.new_page(viewport={"width": W, "height": H},
                                    device_scale_factor=1)
            # REEL_SPEC must exist before the scene's script runs
            page.add_init_script(f"window.REEL_SPEC = {json.dumps(spec)};")
            page.goto("file:///" + SCENE.replace("\\", "/"))
            page.wait_for_function("window.__ready === true", timeout=15000)
            # let the local @font-face files land, or the first frames fall back
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(400)

            page.set_default_timeout(120_000)
            for i in range(total):
                page.evaluate("t => window.seek(t)", i / fps)
                # One retry: a frame can occasionally stall when the encoder has
                # the CPU, and losing the whole render to a single slow capture
                # after two minutes of work is not a reasonable failure mode.
                try:
                    shot = page.screenshot(type="jpeg", quality=92, timeout=60_000)
                except Exception:
                    page.wait_for_timeout(250)
                    shot = page.screenshot(type="jpeg", quality=92, timeout=120_000)
                proc.stdin.write(shot)
                if progress and i % 30 == 0:
                    progress(i, total)
            browser.close()
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited {proc.returncode}")
    return out_path


def preview(spec: dict, times, out_dir: str) -> list:
    """Grab a few stills instead of a whole video — for checking a scene without
    paying for a full render."""
    from playwright.sync_api import sync_playwright
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-color-profile=srgb", "--hide-scrollbars"])
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.add_init_script(f"window.REEL_SPEC = {json.dumps(spec)};")
        page.goto("file:///" + SCENE.replace("\\", "/"))
        page.wait_for_function("window.__ready === true", timeout=15000)
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(400)
        for t in times:
            page.evaluate("t => window.seek(t)", float(t))
            path = os.path.join(out_dir, f"t{t}.png")
            page.screenshot(path=path)
            paths.append(path)
        browser.close()
    return paths
