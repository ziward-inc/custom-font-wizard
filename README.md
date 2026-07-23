# Custom Font Wizard

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

Custom Font Wizard is a Rust TUI that analyzes the union of the `cmap` tables in two Variable Fonts and creates a new Variable Font containing only the selected Unicode groups. It retains a Base glyph when it has a valid outline, and fills a missing or blank Base glyph with the Donor glyph.

## Supported scope

- Variable TTF (`glyf` + `gvar`) Base + Variable TTF Donor → Variable TTF output
- Variable OTF (`CFF2`) Base + Variable OTF Donor → Variable OTF output
- Mixed TTF/OTF inputs, static fonts, and WOFF/WOFF2 are not supported.
- Both Base and Donor must contain a `wght` axis. The output retains only the `wght` axis; all other axes are fixed at their source defaults.

## Run

Rust toolchain and [uv](https://docs.astral.sh/uv/) are required. Python is managed by `uv` according to `pyproject.toml` and `uv.lock`.

### One-line installer

Rust toolchain and `uv` must be installed. The installer places the latest GitHub Release source in `~/.local/share/custom-font-wizard` and creates `~/.local/bin/custom-font-wizard`.

`curl`:

```sh
curl -fsSL https://raw.githubusercontent.com/ziward-inc/custom-font-wizard/main/install.sh | sh
```

`wget`:

```sh
wget -qO- https://raw.githubusercontent.com/ziward-inc/custom-font-wizard/main/install.sh | sh
```

After installation, if `~/.local/bin` is in your `PATH`, run:

```sh
custom-font-wizard
```

### Run from source

```sh
uv sync --dev
cargo run
```

On first launch, Base and Donor are unselected. Press `Enter` in either field to open the native file picker. On macOS, fonts can be selected through Finder.

## Usage

### 1. Select and analyze fonts

Press `Enter` in the Base and Donor fields to open the native file picker. Only `.ttf` and `.otf` files are shown, and both inputs must use the same format. When both selections are complete, move to `Analyze Fonts` and press `Enter`.

![Base and Donor Variable Font selection](docs/screenshots/01-source-selection.svg)

Canceling a picker preserves the current selection. To change a selected font, press `Enter` in that field again. Press `Backspace` or `Delete` to clear it completely.

### 2. Select Unicode groups

After analysis, Unicode groups are generated automatically from the union of the Base and Donor `cmap` tables. Use `↑`/`↓` to move and `Space` to select groups for the output. `Essentials · Whitespace` is always included for correct spacing.

![Unicode group coverage selection](docs/screenshots/02-unicode-groups.svg)

In the compact table, `Base V/B` and `Donor V/B` mean visible/blank glyph counts. `Fillable` is the number of blank or missing Base glyphs that can be supplied by the Donor. When finished, move to `Configure Font` and press `Enter`.

### 3. Configure the font

Enter a family name and the desired `wght` minimum and maximum, then move to `Continue to Output` and press `Enter`. The Base or Donor family name can be used, but a unique family name is recommended to avoid OS font-cache or installed-font conflicts.

### 4. Configure output

The output path defaults to `[family name]-Variable.[ext]` in the Base font directory and can be edited directly. The extension is `.ttf` or `.otf`, matching the input format.

Move to `Build Font` and press `Enter` to build directly at the specified output path without a separate `Save As` dialog, then proceed to the Build progress screen.

### 5. Build progress

The app shows input validation, source analysis, glyph preparation, source variation merge or static-master generation, Variable Font generation, output saving, and output validation. The current step is `[▶]`, completed steps are `[✓]`, and failed steps are `[✗]`.

The read-only Build log panel shows variation merge or per-master progress and required logs. Use `↑`/`↓` or `PageUp`/`PageDown` to inspect earlier logs. On failure, this screen remains open; press `Enter` or `Esc` to return to Output configuration. On success, the result screen opens automatically.

### 6. Result

The completed screen displays the number of glyphs retained from Base, recovered or added from Donor, excluded because neither font could provide them, and the weight samples used.

### Keys

- Source: `Tab`/`↑`/`↓` move, `Enter` select a font or analyze, `Backspace`/`Delete` clear a selection, `Esc` quit
- Unicode group: `↑`/`↓` move, `Space` select, `A` select all, `N` clear all, `Tab` move to buttons, `Enter` activate, `Esc` go back
- Font configuration / Output: `Tab`/`↑`/`↓` move among fields and buttons, `Ctrl+U` clear a field, `Enter` move to the next field or activate a button, `Esc` go back
- Build progress: `↑`/`↓`/`PageUp`/`PageDown` scroll logs; after failure, `Enter`/`Esc` returns to Output configuration
- When no build is in progress, use `Ctrl+C` to quit.

An 80-column terminal uses the compact coverage table. Wider terminals also display the expected codepoint count for `Custom`.

## Coverage and blank detection

Groups are not fixed `unicode-range` values. They are created by classifying the actual codepoints in `Base cmap ∪ Donor cmap` by Unicode script/category. Hangul, Latin, Numbers, Punctuation, Symbols, Kana, Han, Marks, other discovered scripts, and Private Use appear only when needed.

Each group shows:

- Number of union codepoints
- Base visible/blank count
- Donor visible/blank count
- Number of Base blank or missing glyphs that a visible Donor glyph can fill

TTF glyphs are considered blank based on contours/components; OTF glyphs are considered blank based on computed outline bounds. Unicode whitespace is treated as a valid glyph even without an outline: Base mapping takes precedence, and Donor mapping is retained when Base has no mapping. `.notdef` and subset dependencies are preserved automatically.

For selected codepoints, source priority is:

1. Visible Base glyph
2. Visible Donor glyph when Base is blank or missing
3. Omit from output when neither font can provide a usable glyph

## Weight range and clamping

The requested minimum and maximum define the output `wght` axis range. If the requested range extends beyond the Base range, outlines in that interval are clamped to the nearest Base boundary. For example, when Base is `300–900` and the output is `100–900`, the `100–300` interval uses the Base `300` outline.

When a TTF output range is inside the Base range and the source has no separate metric/axis variation tables, the app normalizes the subsetted source `fvar`/`gvar` to the output axis and combines them directly. This preserves source outline interpolation without creating unnecessary intermediate `gvar` tuples, and creates weight-specific `fvar` named instances and `STAT AxisValue` entries.

When clamping is needed or source variation tables cannot be combined directly, the app creates static masters at requested boundaries, the Base default, actual interval boundaries, 100-unit weights, and Base/Donor named-instance weights, then rebuilds with `fontTools.varLib`. In this fallback, interpolation between samples is newly generated and is not guaranteed to exactly match hidden source variation breakpoints.

## Layout and variation handling

- For both TTF and OTF, subset dependency glyphs and Base/Donor `GSUB`, `GPOS`, and `GDEF` are merged. OTF static masters, including CID-keyed CFF, are normalized to dehinted name-keyed CFF before layout and outlines are merged and a CFF2 output is built with `fontTools.varLib`.
- Weight-dependent `GSUB FeatureVariations` and `GPOS FeatureVariations` from Base and Donor are reconstructed for output `wght` conditions. The complete alternate feature lookup graph is retained, including `SingleSubst`, `MultipleSubst`, `AlternateSubst`, `LigatureSubst`, positioning, contextual lookups, and Extension lookups.
- `GDEF VarStore` regions referenced by GPOS `VariationIndex` values and `avar` kinks are used as breakpoints when reconstructing positioning values. The direct TTF path combines source `gvar`/`fvar` directly and replaces only this GPOS/GDEF result, preserving the source outline variation structure.

## Verify

```sh
cargo fmt --check
cargo test
cargo clippy --all-targets -- -D warnings
uv run ruff format worker tests --check
uv run ruff check worker tests
uv run ty check
uv run python -m unittest discover -s tests -v
```

## License

Copyright 2026 Ziward, Inc. Licensed under the [Apache License, Version 2.0](LICENSE).
