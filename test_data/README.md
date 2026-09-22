# Test Data for Integration Benchmarks

Place real audio/video files in the directories below to run the full integration
benchmark suite. The tests auto-discover files — you only need to drop them in.

## Directory Layout

```
test_data/
├── short/              # < 60 s   — smoke tests, quick feedback loop
├── medium/             # 1-10 min — typical user clips
├── long/               # 10+ min  — stress / long-video-chunking path
├── multilingual/       # mixed-language audio (e.g. EN+RU, EN+ES)
├── noisy/              # background noise, low bitrate, bad mic
├── multi_speaker/      # 2+ speakers for diarization tests
├── edge_cases/         # silence, single word, corrupt headers, etc.
├── formats/            # format variety: mp3, mp4, webm, ogg, mkv, m4a, flac
└── reference_transcripts/  # ground-truth .txt files (see below)
```

## Supported File Extensions

`.wav .mp3 .mp4 .m4a .webm .ogg .opus .mkv .flac .aac .wma .avi .mov`

## Reference Transcripts (optional, for WER / accuracy)

For any test file `test_data/<category>/foo.mp3` you can create a ground-truth
transcript at `test_data/reference_transcripts/foo.txt` (same stem, `.txt`).

- One line per utterance, plain text, no timestamps.
- Used to compute **Word Error Rate (WER)** and character-level accuracy.
- If no reference exists, quality metrics are skipped (no failure).

## Recommended Starter Pack

| File | Put in | Why |
|------|--------|-----|
| 30 s English clip (clear speech) | `short/` | Baseline smoke |
| 30 s Russian clip | `short/` | Non-English fast-path |
| 5 min podcast excerpt | `medium/` | Typical use case |
| 5 min phone call (2 speakers) | `multi_speaker/` | Diarization |
| 2 min noisy recording | `noisy/` | Noise robustness |
| 15+ min lecture | `long/` | Long-video chunking |
| Clip with EN+RU mixed | `multilingual/` | Multilingual detection |
| Same clip in .mp3, .wav, .webm | `formats/` | Format parity |
| 1 s silence-only WAV | `edge_cases/` | Empty-result handling |
| Truncated/corrupt .mp4 | `edge_cases/` | Error resilience |

## Running the Suite

```bash
cd backend
python -m pytest tests/integration/ -v --tb=short

# Specific category only:
python -m pytest tests/integration/ -v -k "short"

# Generate HTML metrics report:
python tests/integration/run_benchmarks.py --html
```

Results are written to `backend/tests/integration/reports/`.
