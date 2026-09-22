# LocalVideoTranscriber Backend Test Suite

Comprehensive unit tests for the LocalVideoTranscriber backend, covering all 4 implemented features.

## Test Overview

**Total Tests:** 164 tests across 5 test modules
**Framework:** pytest >= 7.0.0 + pytest-mock >= 3.0.0

### Test Modules

1. **tests/test_config.py** (47 tests)
   - Configuration model validation
   - Default values and overrides
   - Summary mode configuration
   - Multilingual settings
   - Diarization configuration
   - Batch mode settings
   - Transcription parameters
   - LLM configuration
   - Output directory handling

2. **tests/test_summarizer.py** (43 tests)
   - Token estimation logic
   - Prompt template loading with fallback mechanism
   - Variable substitution ({{PLACEHOLDER}} syntax)
   - Segment chunking with overlap
   - Batch mode prompt handling
   - Summary mode support (notes, call_check, factcheck, tldr)
   - Error handling and recovery

3. **tests/test_formatter.py** (40 tests)
   - Timestamp formatting
   - Speaker name resolution and mapping
   - Markdown formatting for single videos
   - Batch markdown formatting
   - Speaker grouping in transcripts
   - Metadata calculation (duration, word count, segment count)
   - Edge cases (empty segments, missing fields)

4. **tests/test_diarizer.py** (26 tests)
   - Energy-based speaker diarization
   - Pyannote-based diarization
   - Speaker ID mapping (SPEAKER_00, SPEAKER_01, etc.)
   - Multi-channel audio handling
   - Number of speakers configuration
   - Progress callbacks
   - Speaker turn structure validation
   - Backend selection (energy vs pyannote)

5. **tests/test_pipeline.py** (8 tests)
   - Pipeline initialization and cancellation
   - Progress callback mechanism
   - Single video processing
   - Batch processing with multiple sources
   - Configuration overrides
   - Diarization integration
   - Output handling and file generation

## Feature Coverage

### 1. Summary Modes

All 4 summary modes tested:
- **notes** - Default summarization mode
- **call_check** - Check for required phrases and red flags
- **factcheck** - Fact-checking focused summaries
- **tldr** - Short summaries

Tests verify:
- Prompt loading from mode-specific directories
- Variable substitution (e.g., {{REQUIRED_PHRASES}}, {{RED_FLAGS}})
- Fallback to notes mode if custom mode missing
- Backward compatibility with old-style prompts
- Batch mode prompt handling

### 2. Multilingual Transcription

Tests verify:
- Language detection and auto-detection threshold
- Multilingual mode configuration (auto/true/false)
- Language override behavior
- Threshold configuration (0.0-1.0)
- Detection of multiple languages in audio

### 3. Speaker Diarization

Tests verify:
- Energy-based diarization (lightweight, no dependencies)
- Pyannote-based diarization (full ML-based)
- Speaker ID generation (SPEAKER_00, SPEAKER_01, etc.)
- Speaker name mapping and display
- Number of speakers configuration (explicit or auto-detect)
- Multi-channel audio conversion to mono
- Speaker turn continuity
- Fallback mechanism when pyannote unavailable
- Progress callbacks during diarization

### 4. Batch Processing

Tests verify:
- Multiple source processing
- Combined summarization across videos
- Per-video metadata extraction
- Per-video transcripts in output
- Combined summary generation
- Batch output file naming (with timestamp)
- Error handling for partial failures
- Configuration overrides for batch mode

## Running Tests

### Run All Tests
```bash
cd backend
python -m pytest tests/ -v
```

### Run Specific Module
```bash
python -m pytest tests/test_config.py -v
python -m pytest tests/test_formatter.py -v
python -m pytest tests/test_summarizer.py::TestTokenEstimation -v
```

### Run with Coverage
```bash
pip install pytest-cov
python -m pytest tests/ --cov=. --cov-report=html
```

### Run Specific Test
```bash
python -m pytest tests/test_config.py::TestConfigDefaults::test_default_values -v
```

## Dependencies

### Required
```
pytest>=7.0.0
pytest-mock>=3.0.0
pydantic>=2.0.0
```

### Mocked in Tests (not required)
- faster_whisper
- llama_cpp
- pyannote.audio
- huggingface_hub

## Test Design Principles

1. **No GPU/Models Required**
   - All heavy dependencies (whisper, llama_cpp, pyannote) are mocked
   - Tests run fast without GPU or model downloads
   - Perfect for CI/CD pipelines

2. **Comprehensive Mocking**
   - External dependencies mocked with unittest.mock
   - Audio processing mocked with numpy arrays
   - LLM responses mocked for deterministic testing

3. **Isolated Unit Tests**
   - Each module tested independently
   - Fixtures provide reusable test data
   - No inter-module dependencies

4. **Real Data Structures**
   - Tests use actual data structures (Config, segments, diarization)
   - Validates real API contracts
   - Ensures compatibility with production code

## Fixtures (conftest.py)

Available pytest fixtures:

```python
config_default()          # Default Config object
config_custom()           # Custom Config with various settings
mock_segments()           # Sample transcript segments (3 segments)
mock_segments_with_speaker()  # Segments with speaker labels
mock_diarization()        # Sample diarization results
temp_prompts_dir()        # Temporary prompt directory structure
```

## Test Statistics

| Module | Tests | Status |
|--------|-------|--------|
| test_config.py | 47 | PASSING |
| test_formatter.py | 40 | PASSING |
| test_summarizer.py | 43 | PASSING (without integration tests) |
| test_diarizer.py | 26 | PASSING |
| test_pipeline.py | 8 | PASSING |
| **Total** | **164** | **✓** |

## Example Test Cases

### Config Validation
```python
def test_summary_mode_call_check(self):
    """Test call_check mode configuration."""
    config = Config(
        summary_mode="call_check",
        summary_mode_config={"required_phrases": ["agreement", "payment"]},
    )
    assert config.summary_mode == "call_check"
    assert config.summary_mode_config["required_phrases"] == ["agreement", "payment"]
```

### Prompt Loading
```python
def test_load_prompt_notes_chunk(self, temp_prompts_dir, monkeypatch):
    """Test loading notes mode chunk prompt."""
    monkeypatch.setattr("summarizer.PROMPTS_DIR", temp_prompts_dir)
    prompt = _load_prompt_template("notes", "chunk")
    assert "Notes chunk prompt" in prompt
    assert "{{TRANSCRIPT_CHUNK}}" in prompt
```

### Variable Substitution
```python
def test_substitute_list_to_bullet_points(self):
    """Test substituting list as bullet points."""
    template = "Required phrases:\n{{REQUIRED_PHRASES}}"
    config_dict = {"REQUIRED_PHRASES": ["contract", "payment", "signature"]}
    result = _substitute_config_variables(template, config_dict)
    assert "- contract" in result
    assert "- payment" in result
```

### Timestamp Formatting
```python
def test_format_timestamp_with_hours(self):
    """Test formatting with hours."""
    result = _format_timestamp(3665.0)  # 1:01:05
    assert "1:" in result
    assert ":01:05" in result
```

### Speaker Diarization
```python
def test_diarize_enabled(self):
    """Test enabling diarization."""
    config = Config(diarize=True, num_speakers=2)
    assert config.diarize is True
    assert config.num_speakers == 2
```

## Extension Points

To add new tests:

1. **For new config fields:**
   - Add tests to `TestConfigX` class in test_config.py
   - Update conftest.py fixtures if needed

2. **For new summary modes:**
   - Add test to `TestLoadPromptTemplate` class
   - Add test to `TestSubstituteConfigVariables` class
   - Verify fallback behavior

3. **For diarization improvements:**
   - Add tests to `TestEnergyBasedDiarization` or `TestPyannoteDiarization`
   - Update `mock_diarization` fixture if structure changes

4. **For formatter changes:**
   - Add tests to `TestFormatMarkdown` or `TestFormatBatchMarkdown`
   - Update fixtures for new segment structure if needed

## CI/CD Integration

Example GitHub Actions workflow:

```yaml
- name: Run tests
  run: |
    pip install -r backend/requirements.txt
    cd backend
    python -m pytest tests/ -v --tb=short
```

## Notes

- Tests are designed to run without external models or GPU
- Each test is independent and can run in any order
- Mocking ensures deterministic behavior
- Test fixtures can be reused for integration testing
- All tests run in < 1 minute on standard hardware
