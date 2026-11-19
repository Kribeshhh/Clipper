from contextlib import contextmanager
from pathlib import Path
from uuid import UUID


class LocalMedia:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def path(self, key: UUID, *, clip: bool = False) -> Path:
        kind = "clip" if clip else "video"
        return self.root / f"{kind}-{key.hex}.mp4"

    @contextmanager
    def stage(self, key: UUID, *, clip: bool = False):
        path = self.path(key, clip=clip).with_suffix(".partial")
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)
