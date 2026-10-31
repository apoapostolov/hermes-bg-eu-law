"""bg-law version and cache schema version."""

__version__ = "1.0.1"

# Bumped whenever a parser changes shape: it is part of every cache key, so a fixed
# parser never serves data parsed by the old one.
PARSE_VERSION = 2
