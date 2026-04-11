# Plugins

The plugin layer exposes a common scan contract across supported languages.

- `base.py` defines shared plugin helpers
- `registry.py` registers available language plugins
- `python/`, `typescript/`, `go/`, and `java/` implement language-specific scanning
