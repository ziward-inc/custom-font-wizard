# Custom Font Wizard

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

Custom Font Wizard 是一个 Rust TUI：它分析两个 Variable Font 的 `cmap` 并集，并仅使用选定的 Unicode group 创建新的 Variable Font。如果 Base glyph 有有效 outline，则保留它；如果 Base glyph 缺失或为空，则使用 Donor glyph 补齐。

## 支持范围

- Variable TTF（`glyf` + `gvar`）Base + Variable TTF Donor → Variable TTF output
- Variable OTF（`CFF2`）Base + Variable OTF Donor → Variable OTF output
- 不支持混合 TTF/OTF、static font 和 WOFF/WOFF2。
- Base 和 Donor 都必须具有 `wght` axis。output 仅保留 `wght` axis，其他 axis 固定为 source default。

## 运行

需要 Rust toolchain 和 [uv](https://docs.astral.sh/uv/)。Python 由 `uv` 根据 `pyproject.toml` 和 `uv.lock` 管理。

### One-line installer

需要先安装 Rust toolchain 和 `uv`。installer 会将最新 GitHub Release source 安装到 `~/.local/share/custom-font-wizard`，并创建 `~/.local/bin/custom-font-wizard`。

`curl`:

```sh
curl -fsSL https://raw.githubusercontent.com/ziward-inc/custom-font-wizard/main/install.sh | sh
```

`wget`:

```sh
wget -qO- https://raw.githubusercontent.com/ziward-inc/custom-font-wizard/main/install.sh | sh
```

安装后，如果 `~/.local/bin` 已包含在 `PATH` 中，可运行：

```sh
custom-font-wizard
```

### 从 Source 运行

```sh
uv sync --dev
cargo run
```

首次启动时，Base 和 Donor 均未选择。在任一 field 按 `Enter` 可打开 native file picker。在 macOS 上可以通过 Finder 选择 font。

## 使用方法

### 1. 选择并分析 Font

在 Base 和 Donor field 分别按 `Enter`，打开 native file picker。只显示 `.ttf` 和 `.otf`，且两个 input 必须使用相同 format。完成选择后，移至 `Analyze Fonts` 并按 `Enter`。

![选择 Base 和 Donor Variable Font](docs/screenshots/01-source-selection.svg)

取消 picker 不会清除当前选择。要更换已选 font，请在对应 field 再按一次 `Enter`。按 `Backspace` 或 `Delete` 可完全清除选择。

### 2. 选择 Unicode group

analysis 完成后，会从 Base 和 Donor 的 `cmap` 并集自动生成 Unicode group。使用 `↑`/`↓` 移动，使用 `Space` 选择要包含在 output 中的 group。为保证正确 spacing，始终包含 `Essentials · Whitespace`。

![选择 Unicode group coverage](docs/screenshots/02-unicode-groups.svg)

compact table 中的 `Base V/B` 和 `Donor V/B` 分别表示 visible/blank glyph 数量。`Fillable` 表示可由 Donor 填补的 Base blank 或 missing glyph 数量。完成后移至 `Configure Font` 并按 `Enter`。

### 3. 配置 Font

输入 family name 和所需的 `wght` minimum/maximum，然后移至 `Continue to Output` 并按 `Enter`。可以使用与 Base/Donor 相同的 family name，但建议使用唯一名称，以避免 OS font cache 或已安装原始 font 冲突。

### 4. 配置 Output

Output path 会自动设置为 Base font 所在 directory 中的 `[family name]-Variable.[ext]`，也可在 field 中直接修改。extension 为与 input format 对应的 `.ttf` 或 `.otf`。

移至 `Build Font` 并按 `Enter`，即可在指定 Output path 直接 build，无需单独的 `Save As` dialog，随后进入 Build progress 页面。

### 5. Build progress

按顺序显示 Input validation、source analysis、glyph preparation、source variation merge 或 static master 创建、Variable Font 创建、output 保存和 output 验证。进行中的步骤为 `[▶]`，完成为 `[✓]`，失败为 `[✗]`。

read-only Build log panel 会显示 variation merge 或各 master 的进度和必要 log。使用 `↑`/`↓` 或 `PageUp`/`PageDown` 查看之前的 log。失败时会停留在此页面；按 `Enter` 或 `Esc` 返回 Output 配置。成功时自动进入结果页面。

### 6. 结果

完成页面会显示从 Base 保留的 glyph 数、从 Donor 恢复或新增的 glyph 数、因两个 font 均无法提供而排除的数量，以及使用的 weight sample。

### Key

- Source：`Tab`/`↑`/`↓` 移动，`Enter` 选择 font 或开始 analysis，`Backspace`/`Delete` 清除选择，`Esc` 退出
- Unicode group：`↑`/`↓` 移动，`Space` 选择，`A` 全选，`N` 全部取消，`Tab` 移至 button，`Enter` 执行，`Esc` 返回上一步
- Font setting / Output：`Tab`/`↑`/`↓` 在 field/button 间移动，`Ctrl+U` 清除 field，`Enter` 移至下一个 field 或执行 button，`Esc` 返回上一步
- Build progress：`↑`/`↓`/`PageUp`/`PageDown` 滚动 log；失败后 `Enter`/`Esc` 返回 Output 配置
- 未在 Build 时可用 `Ctrl+C` 退出。

80-column terminal 使用 compact coverage table。更宽的 terminal 还会显示 `Custom` 的预计 codepoint 数。

## Coverage 和 blank 判定

group 不是固定的 `unicode-range`；它们根据 `Base cmap ∪ Donor cmap` 中实际 codepoint 的 Unicode script/category 分类生成。Hangul、Latin、Numbers、Punctuation、Symbols、Kana、Han、Marks、其他发现的 script 和 Private Use 仅在需要时显示。

每个 group 显示：

- 并集 codepoint 数
- Base visible/blank 数
- Donor visible/blank 数
- 可由 visible Donor glyph 填补的 Base blank 或 missing 数

TTF glyph 根据是否有 contour/component 判断为 blank；OTF glyph 根据计算出的 outline bounds 判断。Unicode whitespace 即使没有 outline 也视为有效 glyph：优先使用 Base mapping，Base 没有 mapping 时保留 Donor mapping。`.notdef` 和 subset dependency 会自动保留。

选定 codepoint 的 source 优先级为：

1. visible Base glyph
2. Base 为 blank 或 missing 时使用 visible Donor glyph
3. 两个 font 都不能提供可用 glyph 时，从 output 排除

## Weight range 和 clamp

请求的 minimum/maximum 定义 output `wght` axis range。如果请求 range 超出 Base range，区间内的 outline 会 clamp 到最近的 Base 边界值。例如，当 Base 为 `300–900` 而 output 为 `100–900` 时，`100–300` 使用 Base `300` outline。

当 TTF output range 位于 Base range 内，且 source 没有单独的 metric/axis variation table 时，应用会将 subset source `fvar/gvar` normalize 到 output axis 后直接合并。该路径会保留 source outline interpolation，不会创建不必要的 intermediate `gvar` tuple，并为每个 weight 创建 `fvar` named instance 和 `STAT AxisValue`。

当需要 clamp 或无法直接合并 source variation table 时，应用会在请求边界、Base default、实际区间边界、每 100 个单位的 weight，以及 Base/Donor named instance weight 处创建 static master，然后通过 `fontTools.varLib` 重新构建。在此 fallback 中，sample 之间的 outline 是新的 interpolation 结果，不能保证与 source 隐藏的 variation breakpoint 完全一致。

## Layout 和 variation 处理

- TTF 和 OTF 都会合并 subset dependency glyph 以及 Base/Donor 的 `GSUB`、`GPOS`、`GDEF`。OTF static master（包括 CID-keyed CFF）会先 normalize 为 dehinted name-keyed CFF，再合并 layout 和 outline，并使用 `fontTools.varLib` 构建 CFF2 output。
- Base 和 Donor 的 weight-dependent `GSUB FeatureVariations` 与 `GPOS FeatureVariations` 会针对 output `wght` condition 重新构建。保留完整的 alternate feature lookup graph，包括 `SingleSubst`、`MultipleSubst`、`AlternateSubst`、`LigatureSubst`、positioning、contextual lookup 和 Extension lookup。
- 重建 positioning 值时，会将 GPOS `VariationIndex` 引用的 `GDEF VarStore` region 和 `avar` kink 用作 breakpoint。direct TTF path 会直接合并 source `gvar/fvar`，仅替换此 GPOS/GDEF 结果，因此保留 source outline variation 结构。

## 验证

```sh
cargo fmt --check
cargo test
cargo clippy --all-targets -- -D warnings
uv run ruff format worker tests --check
uv run ruff check worker tests
uv run ty check
uv run python -m unittest discover -s tests -v
```
