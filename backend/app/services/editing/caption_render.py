"""A fixed ASS document. Only the final plain-text Dialogue column varies."""

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
WrapStyle: 0
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,DejaVu Sans,40,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,40,40,150,1
[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""  # noqa: E501


def stamp(seconds):
    cs = round(seconds * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def write_captions(directory, cues):
    with (directory / "captions.ass").open("x", encoding="utf-8") as script:
        script.write(HEADER)
        for cue in cues:
            # Neutralize override blocks, escapes and record separators.
            text = r"\N".join(
                " ".join(line.split()).translate(
                    str.maketrans({"{": "｛", "}": "｝", "\\": "＼"})
                )
                for line in cue["text"].splitlines()
            )
            word = cue.get("emphasis")
            if (
                word
                and word in text
                and all(c.isalnum() or c in "!?.,'-" for c in word)
            ):
                text = text.replace(word, r"{\c&H50E4FF&}" + word + r"{\c&HFFFFFF&}", 1)
            script.write(
                f"Dialogue: 0,{stamp(cue['start'])},{stamp(cue['end'])},"
                f"Default,,0,0,0,,{text}\n"
            )
