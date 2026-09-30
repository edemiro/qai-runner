"""A video of a run, made from the frames QAi already has.

The report hung a screenshot off every step and left the reader to click
through them, which shows where a run stood and never what happened between:
a spinner that did not stop, a toast that came and went, a page that flashed
back. A recording shows that, next to the steps it belongs to.

Playwright can record a video, but only of a browser context from its first
moment to its last, and the workspace keeps one context open across many runs.
So a run records itself, from the frames the live mirror is already fed: a
browser's screencast, and on a phone the frame each action publishes. They are
written to ffmpeg at a steady rate — the frame that was on screen at each tick
— so the video's clock is the run's clock, and a step's place in the video is
its start time minus the recording's.

Best effort from end to end. No ffmpeg, a frame that will not decode, an
encoder that dies: the run carries on and simply has no video.
"""

import asyncio
import base64
import glob
import io
import os
import shutil
import time
from typing import Any, Callable, Dict, Optional

# Frames a second. The screen of a test changes a few times a second at most,
# and a steady five keeps a five-minute run to a few megabytes.
FPS = 5

# Kilobits a second for the encoder. See FPS.
BITRATE = "300k"


def find_ffmpeg() -> Optional[str]:
    """Playwright's own ffmpeg, which `playwright install` puts beside the
    browsers — the one it records its own videos with — or one on PATH."""
    roots = [
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
        os.path.expanduser("~/Library/Caches/ms-playwright"),
        os.path.expanduser("~/.cache/ms-playwright"),
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        # Newest build first: an older one can be left behind by an upgrade.
        for path in sorted(glob.glob(os.path.join(root, "ffmpeg-*", "ffmpeg*")), reverse=True):
            if os.path.isfile(path):
                return path
    return shutil.which("ffmpeg")


def _size_of(jpeg: bytes) -> Optional[tuple]:
    try:
        from PIL import Image

        return Image.open(io.BytesIO(jpeg)).size
    except Exception:
        return None


class RunRecorder:
    """One run's recording. `start` it as the run opens, `stop` it as it closes."""

    def __init__(self) -> None:
        self.path = ""
        self.started_at = 0.0
        self._ffmpeg: Optional[str] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._pump: Optional["asyncio.Task[None]"] = None
        self._latest: Optional[bytes] = None
        self._queue: Optional[asyncio.Queue] = None
        self._target: Any = None
        self._poll: Optional[Callable[[], Optional[str]]] = None
        self._frames = 0

    @classmethod
    async def start(cls, target: Any, path: str,
                    poll: Optional[Callable[[], Optional[str]]] = None) -> Optional["RunRecorder"]:
        """Start recording `target` into `path`, or None when it cannot be done.

        A browser is read off its screencast, which pushes a frame whenever the
        page paints. Anything else is read through `poll`, which answers the
        newest frame the run has published — on a phone, the one its first
        action takes. The recording starts with the first frame there is, and
        `started_at` says when that was: waiting for a phone's first frame
        costs nothing, where taking one to start with cost three seconds.
        """
        self = cls()
        self._ffmpeg = find_ffmpeg()
        if self._ffmpeg is None:
            print("[recording] no ffmpeg found; runs are recorded as screenshots only")
            return None
        self.path = path
        self._target = target
        self._poll = poll

        casting = getattr(target, "start_screencast", None)
        if casting is not None:
            self._queue = asyncio.Queue(maxsize=2)
            if not await casting(self._queue):
                self._queue = None
            # The screencast sends nothing until the page next paints.
            mirror = getattr(target, "mirror_frame", None)
            first = await mirror() if mirror is not None else None
            if first:
                self._latest = base64.b64decode(first)
        self._pump = asyncio.create_task(self._run())
        return self

    async def _open(self, first: bytes) -> bool:
        size = _size_of(first)
        if size is None:
            return False
        # Even, as the encoder wants, and fixed: a frame of another size is
        # padded or cut to this one rather than stopping the encoder.
        width, height = size[0] + size[0] % 2, size[1] + size[1] % 2
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # The options Playwright records its own videos with, so they are ones
        # its minimal ffmpeg build understands — at a gentler rate.
        self._proc = await asyncio.create_subprocess_exec(
            self._ffmpeg, "-loglevel", "error",
            "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "pipe:0",
            "-y", "-an", "-r", str(FPS),
            "-c:v", "vp8", "-qmin", "0", "-qmax", "50", "-crf", "8",
            "-deadline", "realtime", "-speed", "8", "-b:v", BITRATE, "-threads", "1",
            "-vf", f"pad={width}:{height}:0:0:gray,crop={width}:{height}:0:0",
            self.path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self.started_at = time.time()
        return True

    def _newest(self) -> Optional[bytes]:
        frame = None
        if self._queue is not None:
            while not self._queue.empty():
                frame = self._queue.get_nowait()
        elif self._poll is not None:
            frame = self._poll()
        if frame:
            try:
                self._latest = base64.b64decode(frame)
            except Exception:
                pass
        return self._latest

    async def _run(self) -> None:
        """Write the frame on screen at every tick, catching up after a stall
        so the video keeps the run's own time."""
        try:
            while self._proc is None:
                frame = self._newest()
                if frame and not await self._open(frame):
                    return
                if self._proc is None:
                    await asyncio.sleep(1 / FPS)
            started = time.monotonic()
            while True:
                due = int((time.monotonic() - started) * FPS) + 1
                frame = self._newest()
                while self._frames < due and frame:
                    self._proc.stdin.write(frame)
                    self._frames += 1
                await self._proc.stdin.drain()
                await asyncio.sleep(1 / FPS)
        except (ConnectionResetError, BrokenPipeError):
            # The encoder went away; what it wrote so far is kept.
            pass

    async def stop(self) -> Optional[Dict[str, Any]]:
        """Finish the file. Returns where it is and when it started, or None."""
        if self._pump is not None:
            self._pump.cancel()
            try:
                await self._pump
            except (asyncio.CancelledError, Exception):
                pass
        if self._queue is not None:
            try:
                await self._target.stop_screencast(self._queue)
            except Exception:
                pass
        if self._proc is None:
            return None
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
            await asyncio.wait_for(self._proc.wait(), timeout=15)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
            return None
        if self._proc.returncode != 0 or not os.path.exists(self.path) or not self._frames:
            error = b""
            if self._proc.stderr is not None:
                try:
                    error = await asyncio.wait_for(self._proc.stderr.read(), timeout=2)
                except Exception:
                    pass
            print(f"[recording] no video for {self.path}: {error.decode(errors='replace')[:300]}")
            return None
        return {
            "path": self.path,
            "startedAt": self.started_at,
            "durationMs": int(self._frames * 1000 / FPS),
            "sizeBytes": os.path.getsize(self.path),
        }
