# LocalVideoTranscriber Backend Test Suite

Comprehensive unit tests for the LocalVideoTranscriber backend.

## Quick Start

### Install Test Dependencies

```bash
pip install pytest>=7.0.0 pytest-mock>=3.0.0 pydantic>=2.0.0
```

Or from requirements.txt:

```bash
pip install -r requirements.txt
```

### Run All Tests

```bash
cd /path/to/backend
python -m pytest tests/ -v
```

### Run Specific Test Module

```bash
python -m pytest tests/test_config.py -v
python -m pytest tests/test_formatter.py -v
python -m pytest tests/test_summarizer.py -v
python -m pytest tests/test_diarizer.py -v
python -m pytest tests/test_pipeline.py -v
```

## Test Coverage

### tests/test_config.py (47 tests)
Tests the Config model including:
- Default values for all configuration parameters
- Summary modes (notes, call_check, factcheck, tldr)
- Multilingual configuration and thresholds
- Diarization settings (enabled/disabled, backend selection, speaker count)
- Batch processing mode
- LLM and transcription parameters
- Output directory configuration

### tests/test_summarizer.py (43 tests)
Tests the summarizer module including:
- Token estimation logic
- Prompt template loading with fallback mechanism
- Variable substitution ({{PLACEHOLDER}} syntax)
- Segment chunking with configurable overlap
- Batch mode support
- Error handling and recovery

### tests/test_formatter.py (40 tests)
Tests the formatter module including:
- Timestamp formatting (HH:MM:SS conversion)
- Speaker name resolution and mapping
- Markdown formatting for single videos
- Batch markdown formatting for multiple videos
- Speaker grouping in transcripts
- Metadata calculation
- Edge cases and error handling

### tests/test_diarizer.py (26 tests)
Tests the diarizer module including:
- Energy-based speaker diarization
- Pyannote-based speaker diarization
- Speaker ID mapping and formatting
- Multi-channel audio handling
- Number of speakers configuration
- Progress callbacks
- Backend selection

### tests/test_pipeline.py (8 tests)
Tests the pipeline module including:
- Pipeline initialization
- Progress callback mechanism
- Single video processing
- Batch processing with multiple sources
- Configuration overrides
- Output handling

## Test Features

### No External Dependencies Required
All tests run without:
- GPU or CUDA
- Downloaded models
- Audio files
- External services

Heavy dependencies are mocked for fast, reliable testing.

### Comprehensive Mocking
- `faster_whisper` - Speech transcription
- `llama_cpp` - LLM inference
- `pyannote.audio` - Speaker diarization
- `pydub` - Audio processing
- `huggingface_hub` - Model downloads

### Shared Test Fixtures (conftest.py)
Reusable fixtures include:
- `config_default()` - Default Config object
- `config_custom()` - Custom Config with settings
- `mock_segments()` - Sample transcript segments
- `mock_segments_with_speaker()` - Segments with speakers
- `mock_diarization()` - Sample diarization results
- `temp_prompts_dir()` - Temporary prompt directory

## Common Test Commands

### Run tests with verbose output
```bash
python -m pytest tests/ -v
```

### Run specific test class
```bash
python -m pytest tests/test_config.py::TestConfigDefaults -v
```

### Run specific test
```bash
python -m pytest tests/test_config.py::TestConfigDefaults::test_default_values -v
```

### Run with coverage report
```bash
pip install pytest-cov
python -m pytest tests/ --cov=. --cov-report=html
```

### Run with short traceback
```bash
python -m pytest tests/ -v --tb=short
```

### Run only failing tests
```bash
python -m pytest tests/ --lf -v
```

## Test Organization

```
tests/
├── __init__.py              # Package marker
├── conftest.py              # Shared fixtures
├── test_config.py           # Config model tests
├── test_summarizer.py       # Summarizer module tests
├── test_formatter.py        # Formatter module tests
├── test_diarizer.py         # Diarizer module tests
└── test_pipeline.py         # Pipeline module tests
```

## Test Statistics

Total: 164 tests across 5 modules

| Module | Count |
|--------|-------|
| test_config.py | 47 |
| test_formatter.py | 40 |
| test_summarizer.py | 43 |
| test_diarizer.py | 26 |
| test_pipeline.py | 8 |

## Integration with CI/CD

Example GitHub Actions workflow:

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - run: |
          pip install -r backend/requirements.txt
          cd backend
          python -m pytest tests/ -v --tb=short
```

## Debugging Tests

### Enable verbose logging
```bash
python -m pytest tests/ -vv -s
```

### Run single test with pdb on failure
```bash
python -m pytest tests/test_config.py::TestConfigDefaults::test_default_values -vv --pdb
```

### Show print statements
```bash
python -m pytest tests/ -s
```

## Adding New Tests

### 1. Add test to existing module
```python
def test_new_feature(self):
    """Test description."""
    config = Config(new_param="value")
    assert config.new_param == "value"
```

### 2. Add new fixture to conftest.py
```python
@pytest.fixture
def new_fixture():
    """Description of fixture."""
    return {"key": "value"}
```

### 3. Use fixture in test
```python
def test_with_fixture(self, new_fixture):
    """Test using new fixture."""
    assert new_fixture["key"] == "value"
```

## Performance

All 164 tests run in < 1 second on standard hardware.

## Troubleshooting

### Module import errors
```bash
# Ensure pydantic is installed
pip install pydantic>=2.0.0
```

### pytest not found
```bash
# Install pytest
pip install pytest>=7.0.0 pytest-mock>=3.0.0
```

### Temp directory cleanup issues
Some tests may leave temporary files. Clean up with:
```bash
rm -rf /tmp/pytest-*
```

## Further Reading

- See `TESTING.md` for detailed test documentation
- See individual test files for specific test implementations
- See `conftest.py` for available fixtures
