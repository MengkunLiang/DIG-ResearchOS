"""Top-level ResearchOS package metadata and stable public version surface.

The package root intentionally avoids importing runtime components so commands,
tools, and tests can choose their own initialization boundary without cycles.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
