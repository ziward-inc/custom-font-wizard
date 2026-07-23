use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use unicode_general_category::{GeneralCategory, get_general_category};
use unicode_script::{Script, UnicodeScript};

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum FontFlavor {
    Ttf,
    Otf,
}

impl FontFlavor {
    pub fn label(self) -> &'static str {
        match self {
            Self::Ttf => "Variable TTF · glyf/gvar",
            Self::Otf => "Variable OTF · CFF2",
        }
    }

    pub fn extension(self) -> &'static str {
        match self {
            Self::Ttf => "ttf",
            Self::Otf => "otf",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum GlyphStatus {
    Visible,
    Blank,
    Missing,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AxisInfo {
    pub tag: String,
    pub minimum: f64,
    pub default: f64,
    pub maximum: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct FontInfo {
    pub path: String,
    pub family: String,
    pub flavor: FontFlavor,
    pub units_per_em: u16,
    pub cmap_count: usize,
    pub axes: Vec<AxisInfo>,
}

impl FontInfo {
    pub fn weight_axis(&self) -> Option<&AxisInfo> {
        self.axes.iter().find(|axis| axis.tag == "wght")
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CodepointCoverage {
    pub codepoint: u32,
    pub base: GlyphStatus,
    pub donor: GlyphStatus,
    pub whitespace: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AnalysisResponse {
    pub base: FontInfo,
    pub donor: FontInfo,
    pub codepoints: Vec<CodepointCoverage>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct AnalyzeRequest {
    pub base_path: String,
    pub donor_path: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct BuildRequest {
    pub base_path: String,
    pub donor_path: String,
    pub output_path: String,
    pub family_name: String,
    pub weight_min: f64,
    pub weight_max: f64,
    pub codepoints: Vec<u32>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct BuildResponse {
    pub output_path: String,
    pub flavor: FontFlavor,
    pub codepoint_count: usize,
    pub base_kept: usize,
    pub donor_repaired: usize,
    pub donor_added: usize,
    pub unavailable: usize,
    pub sample_weights: Vec<f64>,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BuildStep {
    ValidateInputs,
    AnalyzeSources,
    PrepareGlyphs,
    GenerateMasters,
    BuildVariableFont,
    SaveOutput,
    VerifyOutput,
}

impl BuildStep {
    pub const ALL: [Self; 7] = [
        Self::ValidateInputs,
        Self::AnalyzeSources,
        Self::PrepareGlyphs,
        Self::GenerateMasters,
        Self::BuildVariableFont,
        Self::SaveOutput,
        Self::VerifyOutput,
    ];

    pub fn label(self) -> &'static str {
        match self {
            Self::ValidateInputs => "Input validation",
            Self::AnalyzeSources => "Source analysis",
            Self::PrepareGlyphs => "Glyph preparation",
            Self::GenerateMasters => "Generate static masters",
            Self::BuildVariableFont => "Build variable font",
            Self::SaveOutput => "Save output",
            Self::VerifyOutput => "Verify output",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BuildStepStatus {
    Running,
    Completed,
}

#[derive(Clone, Debug)]
pub struct GroupCoverage {
    pub label: String,
    pub codepoints: Vec<u32>,
    pub union_count: usize,
    pub base_visible: usize,
    pub base_blank: usize,
    pub donor_visible: usize,
    pub donor_blank: usize,
    pub repairable: usize,
    pub selected: bool,
    pub locked: bool,
}

impl GroupCoverage {
    pub fn custom_count(&self) -> usize {
        self.codepoints.len()
    }
}

#[derive(Default)]
struct GroupBuilder {
    codepoints: Vec<u32>,
    union_count: usize,
    base_visible: usize,
    base_blank: usize,
    donor_visible: usize,
    donor_blank: usize,
    repairable: usize,
}

pub fn build_groups(analysis: &AnalysisResponse) -> Vec<GroupCoverage> {
    let mut builders: BTreeMap<(u8, String, String), GroupBuilder> = BTreeMap::new();

    for coverage in &analysis.codepoints {
        let Some(character) = char::from_u32(coverage.codepoint) else {
            continue;
        };
        let (order, id, label) = classify(character, coverage.whitespace);
        let builder = builders.entry((order, id, label)).or_default();
        builder.union_count += 1;

        if is_usable(coverage) {
            builder.codepoints.push(coverage.codepoint);
        }
        match coverage.base {
            GlyphStatus::Visible => builder.base_visible += 1,
            GlyphStatus::Blank => builder.base_blank += 1,
            GlyphStatus::Missing => {}
        }
        match coverage.donor {
            GlyphStatus::Visible => builder.donor_visible += 1,
            GlyphStatus::Blank => builder.donor_blank += 1,
            GlyphStatus::Missing => {}
        }
        if coverage.base != GlyphStatus::Visible && coverage.donor == GlyphStatus::Visible {
            builder.repairable += 1;
        }
    }

    builders
        .into_iter()
        .filter_map(|((_, id, label), builder)| {
            if builder.codepoints.is_empty() {
                return None;
            }
            let locked = id == "essentials";
            Some(GroupCoverage {
                label,
                codepoints: builder.codepoints,
                union_count: builder.union_count,
                base_visible: builder.base_visible,
                base_blank: builder.base_blank,
                donor_visible: builder.donor_visible,
                donor_blank: builder.donor_blank,
                repairable: builder.repairable,
                selected: locked,
                locked,
            })
        })
        .collect()
}

fn is_usable(coverage: &CodepointCoverage) -> bool {
    coverage.base == GlyphStatus::Visible
        || coverage.donor == GlyphStatus::Visible
        || (coverage.whitespace
            && (coverage.base != GlyphStatus::Missing || coverage.donor != GlyphStatus::Missing))
}

fn classify(character: char, whitespace: bool) -> (u8, String, String) {
    if whitespace {
        return (0, "essentials".into(), "Essentials · Whitespace".into());
    }

    let category = get_general_category(character);
    if is_number(category) {
        return (4, "numbers".into(), "숫자 Numbers".into());
    }
    if is_punctuation(category) {
        return (5, "punctuation".into(), "문장부호 Punctuation".into());
    }
    if is_symbol(category) {
        return (6, "symbols".into(), "특수문자 Symbols".into());
    }
    if category == GeneralCategory::PrivateUse {
        return (240, "private-use".into(), "Private Use".into());
    }
    if is_control(category) {
        return (230, "control-format".into(), "Control / Format".into());
    }

    match character.script() {
        Script::Hangul => (1, "hangul".into(), "한글 Hangul".into()),
        Script::Latin => (2, "latin".into(), "Latin".into()),
        Script::Hiragana | Script::Katakana => (3, "kana".into(), "일본어 Kana".into()),
        Script::Han => (7, "han".into(), "한자 Han".into()),
        Script::Inherited if is_mark(category) => (8, "marks".into(), "결합문자 Marks".into()),
        Script::Common | Script::Inherited => (220, "common".into(), "Common / Inherited".into()),
        script => {
            let name = format!("{script:?}");
            (100, name.to_lowercase(), name)
        }
    }
}

fn is_number(category: GeneralCategory) -> bool {
    matches!(
        category,
        GeneralCategory::DecimalNumber
            | GeneralCategory::LetterNumber
            | GeneralCategory::OtherNumber
    )
}

fn is_punctuation(category: GeneralCategory) -> bool {
    matches!(
        category,
        GeneralCategory::ConnectorPunctuation
            | GeneralCategory::DashPunctuation
            | GeneralCategory::OpenPunctuation
            | GeneralCategory::ClosePunctuation
            | GeneralCategory::InitialPunctuation
            | GeneralCategory::FinalPunctuation
            | GeneralCategory::OtherPunctuation
    )
}

fn is_symbol(category: GeneralCategory) -> bool {
    matches!(
        category,
        GeneralCategory::MathSymbol
            | GeneralCategory::CurrencySymbol
            | GeneralCategory::ModifierSymbol
            | GeneralCategory::OtherSymbol
    )
}

fn is_mark(category: GeneralCategory) -> bool {
    matches!(
        category,
        GeneralCategory::NonspacingMark
            | GeneralCategory::SpacingMark
            | GeneralCategory::EnclosingMark
    )
}

fn is_control(category: GeneralCategory) -> bool {
    matches!(
        category,
        GeneralCategory::Control
            | GeneralCategory::Format
            | GeneralCategory::Surrogate
            | GeneralCategory::Unassigned
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classifies_requested_groups() {
        assert_eq!(classify('한', false).1, "hangul");
        assert_eq!(classify('A', false).1, "latin");
        assert_eq!(classify('7', false).1, "numbers");
        assert_eq!(classify('あ', false).1, "kana");
        assert_eq!(classify('漢', false).1, "han");
        assert_eq!(classify(' ', true).1, "essentials");
    }

    #[test]
    fn keeps_donor_only_whitespace() {
        let coverage = CodepointCoverage {
            codepoint: 0x2000,
            base: GlyphStatus::Missing,
            donor: GlyphStatus::Blank,
            whitespace: true,
        };

        assert!(is_usable(&coverage));
    }
}
