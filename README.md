# gjson.py — Fast Iterative JSON Parser

A lightweight, high-performance JSON parser written in pure Python. Uses an **iterative stack-based approach** instead of recursion to avoid stack overflow on deeply nested data, and supports **streaming/buffered parsing** for large files.

## Features

- **No recursion** — stack-based parsing, safe for deeply nested JSON
- **Streaming support** — parse large JSON files in chunks without loading the entire file into memory
- **Trailing comma tolerance** — accepts `{"a": 1,}` and `[1, 2,]` without errors
- **Event-driven API** — iterate over SAX-style events (`start_map`, `end_map`, `start_array`, `end_array`, `map_key`, `value`)
- **Multiple input types** — accepts `str`, `bytes`, `bytearray`, or file-like objects

## Classes & Functions

| Name | Description |
|---|---|
| `FastJSONParser` | Fastest parser. Parses a full JSON string/bytes in-memory into a Python `dict`/`list`. |
| `IterativeJSONParser` | Event-driven parser for in-memory strings. Yields SAX-style events. |
| `IterativeBufferedJSONParser` | ⚗️ **Experimental** — Event-driven chunked file parser (64 KB chunks). Still under development, not ready for production use. |
| `events_to_object(generator)` | Converts an event stream into a complete Python `dict` or `list`. |
| `parse_base(generator)` | Converts an event stream into `(path, event, value)` tuples with dot-notation paths. |

## Usage

### Parse a JSON string (fastest)

```python
from gjson import FastJSONParser

parser = FastJSONParser()
result = parser.parse('{"name": "Alice", "age": 30}')
print(result)  # {'name': 'Alice', 'age': 30}
```

### Parse a large JSON file (streaming) ⚗️

> [!WARNING]
> `IterativeBufferedJSONParser` is currently **experimental** and still being improved. It may have bugs or edge cases. Avoid using it in production — use `FastJSONParser` instead for now.

```python
from gjson import IterativeBufferedJSONParser, events_to_object

parser = IterativeBufferedJSONParser(chunk_size=64 * 1024)
result = events_to_object(parser.parse("large_file.json"))
print(result)
```

### Use event-driven API

```python
from gjson import IterativeJSONParser

for event, value in IterativeJSONParser().parse('{"a": [1, 2, 3]}'):
    print(event, value)
```

**Output:**
```
start_map None
map_key a
start_array None
value 1
value 2
value 3
end_array None
end_map None
```

### Get dot-notation paths

```python
from gjson import IterativeJSONParser, parse_base

for path, event, value in parse_base(IterativeJSONParser().parse('{"a": {"b": 1}}')):
    print(path, event, value)
```

## Requirements

- Python 3.6+
- No third-party dependencies (uses only the standard library)

## Notes

- `IterativeBufferedJSONParser` is **experimental** and not yet ready for production use. It is planned for large-file scenarios but is still being refined.
- `FastJSONParser` ignores any trailing data after the root JSON object closes.
- All parsers detect and reject invalid UTF-8 BOM sequences.
