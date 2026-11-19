import math
import textwrap
import unicodedata


def prepare_cues(transcript, start, end):
    cues = []
    for segment in transcript or []:
        a, b = segment["start"], segment["end"]
        if not (
            math.isfinite(a) and math.isfinite(b) and a < b and a < end and b > start
        ):
            continue
        text = " ".join(segment["text"].split())
        text = "".join(c for c in text if not unicodedata.category(c).startswith("C"))
        lines = textwrap.wrap(text, width=28, break_on_hyphens=False)
        pages = ["\n".join(lines[i : i + 2]) for i in range(0, len(lines), 2)]
        # Segment-only transcripts have no word alignment. Divide long utterances
        # before clipping so a trim retains just the pages intersecting the export.
        total = sum(map(len, pages))
        used = 0
        for page in pages:
            left = max(start, a + (b - a) * used / total)
            used += len(page)
            right = min(end, a + (b - a) * used / total)
            if left < right:
                cues.append(
                    {
                        "start": round(left - start, 6),
                        "end": round(right - start, 6),
                        "text": page,
                    }
                )
    return cues
