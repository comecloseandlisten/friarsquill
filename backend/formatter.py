from datetime import timedelta


def _format_timestamp(seconds: float) -> str:
    td = timedelta(seconds=int(seconds))
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _get_speaker_name(speaker_id: str, speaker_names: dict) -> str:
    """
    Get display name for speaker ID, using speaker_names mapping or raw ID.
    """
    if not speaker_id:
        return ""
    return speaker_names.get(speaker_id, speaker_id)


def format_markdown(title: str, source: str, segments: list[dict], summary: str, speaker_names: dict = None) -> str:
    if speaker_names is None:
        speaker_names = {}

    total_duration = segments[-1]["end"] if segments else 0
    word_count = sum(len(s["text"].split()) for s in segments)

    md = []
    md.append(f"# {title}\n")
    md.append(f"**Source:** `{source}`  ")
    md.append(f"**Duration:** {_format_timestamp(total_duration)}  ")
    md.append(f"**Words:** {word_count}  ")
    md.append(f"**Segments:** {len(segments)}\n")
    md.append("---\n")

    # Summary section
    md.append("## Summary\n")
    md.append(summary)
    md.append("\n---\n")

    # Full transcript with timestamps
    md.append("## Full Transcript\n")

    for seg in segments:
        ts = _format_timestamp(seg["start"])
        md.append(f"**[{ts}]** {seg['text']}\n")

    return "\n".join(md)


def format_batch_markdown(
    batch_results: list[dict],
    combined_summary: str,
    speaker_names: dict = None,
) -> str:
    """
    Format batch processing results with per-video sections and combined summary.

    Args:
        batch_results: List of dicts with keys:
            - title: str (video title)
            - source: str (URL or file path)
            - segments: list[dict] (transcript segments)
            - duration: float (total duration in seconds)
        combined_summary: str (overall summary across all videos)
        speaker_names: dict (mapping of SPEAKER_XX to display names)

    Returns:
        Formatted markdown string with per-video sections and combined summary
    """
    if speaker_names is None:
        speaker_names = {}

    md = []

    # Title and metadata
    md.append(f"# Batch Transcription Report\n")
    md.append(f"**Videos processed:** {len(batch_results)}  ")
    total_duration = sum(r.get("duration", 0) for r in batch_results)
    total_words = sum(len(" ".join(seg["text"] for seg in r.get("segments", [])).split()) for r in batch_results)
    md.append(f"**Total duration:** {_format_timestamp(total_duration)}  ")
    md.append(f"**Total words:** {total_words}  \n")
    md.append("---\n")

    # Combined summary section
    md.append("## Overall Summary\n")
    md.append(combined_summary)
    md.append("\n---\n")

    # Per-video sections
    for i, result in enumerate(batch_results, 1):
        title = result.get("title", f"Video {i}")
        source = result.get("source", "unknown")
        segments = result.get("segments", [])
        duration = result.get("duration", 0)

        md.append(f"## Video {i}: {title}\n")
        md.append(f"**Source:** `{source}`  ")
        md.append(f"**Duration:** {_format_timestamp(duration)}  ")
        word_count = len(" ".join(seg["text"] for seg in segments).split())
        md.append(f"**Words:** {word_count}  ")
        md.append(f"**Segments:** {len(segments)}\n")

        # Per-video transcript
        md.append("### Transcript\n")

        for seg in segments:
            ts = _format_timestamp(seg["start"])
            md.append(f"**[{ts}]** {seg['text']}\n")

        md.append("\n")

    return "\n".join(md)
