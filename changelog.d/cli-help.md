### Added

- **`marv help` (`marv -h` / `marv --help`).** A curated help screen in the
  marv-mlx style: the tagline, one usage line per command, and how to reach the
  full option lists. All three spellings print exactly the same text; the root
  group's built-in help option is replaced by an eager `-h`/`--help` flag, so
  `marv run --help` and friends keep their full Typer option help.
- **`marv version`.** Prints `marv <version>`, matching the existing eager
  `marv --version`.
