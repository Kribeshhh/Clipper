from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.editing.caption_render import write_captions
from app.services.editing.command import render_args
from app.services.editing.validation import validate_export
from app.services.media import run_media


def render(
    source,
    target,
    start,
    end,
    progress,
    *,
    edit=None,
    byte_limit=None,
    expect_audio=None,
):
    # Both paths come from LocalMedia. Never resolve client-provided filenames.
    if (
        not source.is_absolute()
        or not target.is_absolute()
        or source.suffix != ".mp4"
        or not source.is_file()
        or source.is_symlink()
        or source.resolve().parent != target.parent.resolve()
        or target.exists()
        or target.is_symlink()
        or target.suffix != ".partial"
    ):
        raise ValueError("Invalid render paths")
    with TemporaryDirectory(prefix="clip-render-", dir=target.parent) as work:
        if edit and edit["cues"]:
            write_captions(Path(work), edit["cues"])
        run_media(
            "ffmpeg",
            render_args(source, target, start, end, edit, has_audio=expect_audio),
            progress,
            cwd=work,
            byte_limit=byte_limit,
        )
    return validate_export(
        target,
        sum(b - a for a, b in edit["spans"])
        if edit and "spans" in edit
        else end - start,
        bool(edit),
        expect_audio,
    )
