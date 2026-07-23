# Custom Font Wizard

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

Custom Font Wizard は、2つの Variable Font の `cmap` 和集合を解析し、選択した Unicode group だけを含む新しい Variable Font を作成する Rust TUI です。Base glyph に有効な outline があればそれを維持し、Base glyph が欠落または blank の場合は Donor glyph で補います。

## 対応範囲

- Variable TTF（`glyf` + `gvar`）Base + Variable TTF Donor → Variable TTF output
- Variable OTF（`CFF2`）Base + Variable OTF Donor → Variable OTF output
- TTF/OTF の混在、static font、WOFF/WOFF2 は未対応です。
- Base と Donor はどちらも `wght` axis を持つ必要があります。output には `wght` axis だけを残し、他の axis は source default に固定されます。

## 実行

Rust toolchain と [uv](https://docs.astral.sh/uv/) が必要です。Python は `pyproject.toml` と `uv.lock` に従って `uv` が管理します。

### One-line installer

Rust toolchain と `uv` をインストールしてください。installer は最新の GitHub Release source を `~/.local/share/custom-font-wizard` に配置し、`~/.local/bin/custom-font-wizard` を作成します。

`curl`:

```sh
curl -fsSL https://raw.githubusercontent.com/ziward-inc/custom-font-wizard/main/install.sh | sh
```

`wget`:

```sh
wget -qO- https://raw.githubusercontent.com/ziward-inc/custom-font-wizard/main/install.sh | sh
```

インストール後、`~/.local/bin` が `PATH` に含まれていれば、次の command で実行できます。

```sh
custom-font-wizard
```

### Source から実行

```sh
uv sync --dev
cargo run
```

初回起動時は Base と Donor が未選択です。各 field で `Enter` を押すと native file picker が開きます。macOS では Finder で font を選択できます。

## 使い方

### 1. Font の選択と analysis

Base と Donor field でそれぞれ `Enter` を押して native file picker を開きます。`.ttf` と `.otf` だけが表示され、両方の input は同じ format である必要があります。選択後に `Analyze Fonts` へ移動し、`Enter` を押します。

![Base と Donor Variable Font の選択](docs/screenshots/01-source-selection.svg)

picker をキャンセルしても現在の選択は維持されます。選択済み font を変更するには対象 field で再び `Enter` を押します。完全に消去するには `Backspace` または `Delete` を押します。

### 2. Unicode group の選択

analysis 後、Base と Donor の `cmap` 和集合から Unicode group が自動生成されます。`↑`/`↓` で移動し、`Space` で output に含める group を選択します。正しい spacing のため、`Essentials · Whitespace` は常に含まれます。

![Unicode group coverage の選択](docs/screenshots/02-unicode-groups.svg)

compact table の `Base V/B` と `Donor V/B` は visible/blank glyph 数です。`Fillable` は Donor で補える Base の blank または missing glyph 数です。完了したら `Configure Font` へ移動して `Enter` を押します。

### 3. Font の設定

family name と希望する `wght` minimum/maximum を入力し、`Continue to Output` へ移動して `Enter` を押します。Base/Donor と同じ family name も使えますが、OS font cache やインストール済み original との競合を避けるため、固有の family name を推奨します。

### 4. Output の設定

Output path は Base font と同じ directory の `[family name]-Variable.[ext]` に自動設定され、field で直接変更できます。前の phase で入力した family name と input format に対応する `.ttf` または `.otf` extension が使われます。

`Build Font` へ移動して `Enter` を押すと、別の `Save As` dialog なしで指定した Output path に直接 build し、Build progress 画面へ移動します。

### 5. Build progress

Input validation、source analysis、glyph preparation、source variation merge または static master 作成、Variable Font 作成、output 保存、output 検証を順に表示します。実行中は `[▶]`、完了は `[✓]`、失敗は `[✗]` です。

read-only Build log panel には variation merge または master ごとの進行状況と必要な log が表示されます。`↑`/`↓` または `PageUp`/`PageDown` で以前の log を確認できます。失敗時はこの画面に残り、`Enter` または `Esc` で Output 設定に戻れます。成功時は結果画面へ自動移動します。

### 6. 結果

完了画面には、Base から維持した数、Donor で復元または追加した数、どちらの font でも使えず除外した数、使用した weight sample が表示されます。

### Key

- Source: `Tab`/`↑`/`↓` で移動、`Enter` で font 選択または analysis、`Backspace`/`Delete` で選択を消去、`Esc` で終了
- Unicode group: `↑`/`↓` で移動、`Space` で選択、`A` で全選択、`N` で全解除、`Tab` で button へ移動、`Enter` で実行、`Esc` で前の段階へ戻る
- Font setting / Output: `Tab`/`↑`/`↓` で field/button 間を移動、`Ctrl+U` で field を消去、`Enter` で次の field または button を実行、`Esc` で前の段階へ戻る
- Build progress: `↑`/`↓`/`PageUp`/`PageDown` で log を移動。失敗後は `Enter`/`Esc` で Output 設定へ戻る
- Build 実行中でない場合は `Ctrl+C` で終了できます。

80-column terminal では compact coverage table を使用します。より広い terminal では `Custom` の想定 codepoint 数も表示します。

## Coverage と blank 判定

group は固定の `unicode-range` ではなく、実際の `Base cmap ∪ Donor cmap` の codepoint を Unicode script/category で分類して生成します。Hangul、Latin、Numbers、Punctuation、Symbols、Kana、Han、Marks、その他に見つかった script、Private Use は必要な場合だけ表示されます。

各 group は次を表示します。

- 和集合の codepoint 数
- Base の visible/blank 数
- Donor の visible/blank 数
- visible Donor glyph で補える Base blank または missing 数

TTF glyph は contour/component の有無で、OTF glyph は計算された outline bounds の有無で blank と判定します。Unicode whitespace は outline がなくても有効な glyph として扱います。Base mapping を優先し、Base に mapping がない場合は Donor mapping を維持します。`.notdef` と subset dependency は自動的に保持されます。

選択した codepoint の source 優先順位は次のとおりです。

1. visible Base glyph
2. Base が blank または missing の場合は visible Donor glyph
3. どちらの font も使用できない場合は output から除外

## Weight range と clamp

要求した minimum/maximum が output `wght` axis range になります。要求 range が Base range の外へ出る場合、その区間の outline は最も近い Base 境界値に clamp されます。たとえば Base が `300–900` で output が `100–900` の場合、`100–300` は Base `300` outline と同じです。

TTF output range が Base range 内にあり、source に別の metric/axis variation table がない場合、subset した source `fvar/gvar` を output axis に normalize して直接結合します。この経路では不要な intermediate `gvar` tuple を作らず source outline interpolation を保持し、weight ごとの `fvar` named instance と `STAT AxisValue` を作成します。

clamp が必要な場合、または source variation table を直接結合できない場合は、要求境界、Base default、実際の区間境界、100 単位の weight、Base/Donor named instance の weight で static master を作成し、`fontTools.varLib` で再構成します。この fallback では sample 間の outline は新しい interpolation 結果であり、source の隠れた variation breakpoint と完全に同じとは保証されません。

## Layout と variation 処理

- TTF と OTF の両方で subset dependency glyph および Base/Donor の `GSUB`、`GPOS`、`GDEF` を merge します。CID-keyed CFF を含む OTF static master は dehinted name-keyed CFF に normalize してから layout と outline を merge し、`fontTools.varLib` で CFF2 output を構成します。
- Base と Donor の weight-dependent `GSUB FeatureVariations` と `GPOS FeatureVariations` は output `wght` condition に合わせて再構成されます。`SingleSubst`、`MultipleSubst`、`AlternateSubst`、`LigatureSubst`、positioning、contextual lookup、Extension lookup を含む完全な alternate feature lookup graph を保持します。
- GPOS `VariationIndex` が参照する `GDEF VarStore` region と `avar` kink を breakpoint として positioning 値を再構成します。direct TTF path は source `gvar/fvar` を直接結合した後、この GPOS/GDEF 結果だけを置き換えるため、source outline variation 構造を保持します。

## 検証

```sh
cargo fmt --check
cargo test
cargo clippy --all-targets -- -D warnings
uv run ruff format worker tests --check
uv run ruff check worker tests
uv run ty check
uv run python -m unittest discover -s tests -v
```
