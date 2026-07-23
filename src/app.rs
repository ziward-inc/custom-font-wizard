use std::{
    collections::BTreeSet,
    io::Stdout,
    path::PathBuf,
    sync::mpsc::{self, Receiver, TryRecvError},
    thread,
    time::Duration,
};

use anyhow::{Result, anyhow};
use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyModifiers};
use ratatui::{Terminal, backend::CrosstermBackend};
use rfd::FileDialog;

use crate::{
    model::{
        AnalysisResponse, AnalyzeRequest, BuildRequest, BuildResponse, BuildStep, BuildStepStatus,
        GroupCoverage, build_groups,
    },
    ui, worker,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Screen {
    Sources,
    Groups,
    Settings,
    Output,
    Progress,
    Done,
}

#[derive(Clone, Copy, Debug)]
enum PendingTask {
    Analyze,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BuildProgressStatus {
    Pending,
    Running,
    Completed,
    Failed,
}

#[derive(Clone, Debug)]
pub struct BuildProgressItem {
    pub step: BuildStep,
    pub status: BuildProgressStatus,
}

pub struct App {
    pub screen: Screen,
    pub base_path: String,
    pub donor_path: String,
    pub source_focus: usize,
    pub analysis: Option<AnalysisResponse>,
    pub groups: Vec<GroupCoverage>,
    pub group_index: usize,
    pub group_action_focused: bool,
    pub family_name: String,
    pub weight_min: String,
    pub weight_max: String,
    pub output_path: String,
    pub settings_focus: usize,
    pub output_focus: usize,
    pub build_steps: Vec<BuildProgressItem>,
    pub build_logs: Vec<String>,
    pub build_log_scroll: usize,
    pub build_error: Option<String>,
    pub build_result: Option<BuildResponse>,
    pub status: String,
    pub busy: bool,
    should_quit: bool,
    pending: Option<PendingTask>,
    build_receiver: Option<Receiver<worker::BuildEvent>>,
}

impl App {
    pub fn new() -> Self {
        Self {
            screen: Screen::Sources,
            base_path: String::new(),
            donor_path: String::new(),
            source_focus: 0,
            analysis: None,
            groups: Vec::new(),
            group_index: 0,
            group_action_focused: false,
            family_name: "Custom Font".into(),
            weight_min: String::new(),
            weight_max: String::new(),
            output_path: String::new(),
            settings_focus: 0,
            output_focus: 0,
            build_steps: initial_build_steps(),
            build_logs: Vec::new(),
            build_log_scroll: 0,
            build_error: None,
            build_result: None,
            status: "Base와 Donor Variable Font를 선택하세요".into(),
            busy: false,
            should_quit: false,
            pending: None,
            build_receiver: None,
        }
    }

    pub fn run(&mut self, terminal: &mut Terminal<CrosstermBackend<Stdout>>) -> Result<()> {
        while !self.should_quit {
            self.poll_build_events();
            terminal.draw(|frame| ui::render(frame, self))?;

            if let Some(task) = self.pending.take() {
                self.execute(task);
                continue;
            }

            if event::poll(Duration::from_millis(200))?
                && let Event::Key(key) = event::read()?
                && key.kind == event::KeyEventKind::Press
            {
                self.handle_key(key);
            }
        }
        Ok(())
    }

    fn execute(&mut self, task: PendingTask) {
        let result = match task {
            PendingTask::Analyze => self.execute_analyze(),
        };
        self.busy = false;
        if let Err(error) = result {
            self.status = format!("오류: {error:#}");
        }
    }

    fn execute_analyze(&mut self) -> Result<()> {
        let request = AnalyzeRequest {
            base_path: self.base_path.clone(),
            donor_path: self.donor_path.clone(),
        };
        let analysis = worker::analyze(&request)?;
        let weight_axis = analysis
            .base
            .weight_axis()
            .ok_or_else(|| anyhow!("Base에 wght axis가 없습니다"))?;
        self.weight_min = format_number(weight_axis.minimum);
        self.weight_max = format_number(weight_axis.maximum);
        self.output_path.clear();
        self.settings_focus = 0;
        self.output_focus = 0;
        self.groups = build_groups(&analysis);
        self.analysis = Some(analysis);
        self.group_index = 0;
        self.group_action_focused = false;
        self.screen = Screen::Groups;
        self.status = "Unicode group을 선택하고 Configure Font button으로 이동하세요".into();
        Ok(())
    }

    fn start_build(&mut self) -> Result<()> {
        if self.output_path.trim().is_empty() {
            return Err(anyhow!("Output path가 비어 있습니다"));
        }
        let weight_min = self.weight_min.parse::<f64>()?;
        let weight_max = self.weight_max.parse::<f64>()?;
        let codepoints = self.selected_codepoints();
        if codepoints.is_empty() {
            return Err(anyhow!("선택된 Unicode group이 없습니다"));
        }

        let request = BuildRequest {
            base_path: self.base_path.clone(),
            donor_path: self.donor_path.clone(),
            output_path: self.output_path.clone(),
            family_name: self.family_name.trim().to_owned(),
            weight_min,
            weight_max,
            codepoints,
        };

        self.build_steps = initial_build_steps();
        self.build_logs.clear();
        self.build_log_scroll = 0;
        self.build_error = None;
        self.build_result = None;
        let (sender, receiver) = mpsc::channel();
        thread::spawn(move || worker::stream_build(request, sender));
        self.build_receiver = Some(receiver);
        self.screen = Screen::Progress;
        self.status = "Build를 시작했습니다".into();
        Ok(())
    }

    fn poll_build_events(&mut self) {
        let mut events = Vec::new();
        let mut disconnected = false;
        if let Some(receiver) = &self.build_receiver {
            loop {
                match receiver.try_recv() {
                    Ok(event) => events.push(event),
                    Err(TryRecvError::Empty) => break,
                    Err(TryRecvError::Disconnected) => {
                        disconnected = true;
                        break;
                    }
                }
            }
        }

        let mut terminal_event = false;
        for event in events {
            terminal_event |= matches!(
                event,
                worker::BuildEvent::Result { .. } | worker::BuildEvent::Error { .. }
            );
            self.apply_build_event(event);
        }
        if disconnected
            && !terminal_event
            && self.build_result.is_none()
            && self.build_error.is_none()
        {
            self.fail_build("Build worker 연결이 예기치 않게 종료되었습니다".into());
        }
        if disconnected || terminal_event {
            self.build_receiver = None;
        }
    }

    fn apply_build_event(&mut self, event: worker::BuildEvent) {
        match event {
            worker::BuildEvent::Progress {
                step,
                status,
                message,
            } => {
                if let Some(item) = self.build_steps.iter_mut().find(|item| item.step == step) {
                    item.status = match status {
                        BuildStepStatus::Running => BuildProgressStatus::Running,
                        BuildStepStatus::Completed => BuildProgressStatus::Completed,
                    };
                }
                self.status.clone_from(&message);
                self.push_build_log(message);
            }
            worker::BuildEvent::Result { result } => {
                let output_path = result.output_path.clone();
                self.build_result = Some(result);
                self.screen = Screen::Done;
                self.status = format!("완료: {output_path}");
                self.push_build_log(format!("Build 완료 · {output_path}"));
            }
            worker::BuildEvent::Error { message } => self.fail_build(message),
        }
    }

    fn fail_build(&mut self, message: String) {
        let failed_index = self
            .build_steps
            .iter()
            .find(|item| item.status == BuildProgressStatus::Running)
            .map(|item| item.step)
            .or_else(|| {
                self.build_steps
                    .iter()
                    .find(|item| item.status == BuildProgressStatus::Pending)
                    .map(|item| item.step)
            });
        if let Some(failed_step) = failed_index
            && let Some(item) = self
                .build_steps
                .iter_mut()
                .find(|item| item.step == failed_step)
        {
            item.status = BuildProgressStatus::Failed;
        }
        self.build_error = Some(message.clone());
        self.status = format!("오류: {message}");
        self.push_build_log(format!("오류: {message}"));
    }

    fn push_build_log(&mut self, message: String) {
        const MAX_BUILD_LOG_LINES: usize = 200;

        if self.build_log_scroll > 0 {
            self.build_log_scroll += 1;
        }
        self.build_logs.push(message);
        if self.build_logs.len() > MAX_BUILD_LOG_LINES {
            self.build_logs.remove(0);
            self.build_log_scroll = self.build_log_scroll.saturating_sub(1);
        }
    }

    fn selected_codepoints(&self) -> Vec<u32> {
        let mut selected = BTreeSet::new();
        for group in self.groups.iter().filter(|group| group.selected) {
            selected.extend(group.codepoints.iter().copied());
        }
        selected.into_iter().collect()
    }

    fn handle_key(&mut self, key: KeyEvent) {
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            if self.screen == Screen::Progress && self.build_receiver.is_some() {
                self.status = "Build 진행 중에는 종료할 수 없습니다".into();
                return;
            }
            self.should_quit = true;
            return;
        }
        match self.screen {
            Screen::Sources => self.handle_sources_key(key),
            Screen::Groups => self.handle_groups_key(key),
            Screen::Settings => self.handle_settings_key(key),
            Screen::Output => self.handle_output_key(key),
            Screen::Progress => self.handle_progress_key(key),
            Screen::Done => {
                if matches!(key.code, KeyCode::Esc | KeyCode::Char('q') | KeyCode::Enter) {
                    self.should_quit = true;
                }
            }
        }
    }

    fn handle_sources_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Esc => self.should_quit = true,
            KeyCode::Tab | KeyCode::Down => self.source_focus = (self.source_focus + 1) % 3,
            KeyCode::BackTab | KeyCode::Up => self.source_focus = (self.source_focus + 2) % 3,
            KeyCode::Enter if self.source_focus < 2 => self.pick_source_font(),
            KeyCode::Enter if self.base_path.is_empty() || self.donor_path.is_empty() => {
                self.status = "오류: Base와 Donor를 모두 선택해야 합니다".into();
            }
            KeyCode::Enter => {
                self.busy = true;
                self.status = "Font를 분석하고 있습니다…".into();
                self.pending = Some(PendingTask::Analyze);
            }
            KeyCode::Backspace | KeyCode::Delete if self.source_focus == 0 => {
                self.base_path.clear();
                self.status = "Base 선택을 지웠습니다".into();
            }
            KeyCode::Backspace | KeyCode::Delete if self.source_focus == 1 => {
                self.donor_path.clear();
                self.status = "Donor 선택을 지웠습니다".into();
            }
            _ => {}
        }
    }

    fn pick_source_font(&mut self) {
        let title = if self.source_focus == 0 {
            "Select Base Variable Font"
        } else {
            "Select Donor Variable Font"
        };
        let Some(path) = FileDialog::new()
            .set_title(title)
            .add_filter("Variable Font", &["ttf", "otf"])
            .pick_file()
        else {
            self.status = "Font 선택을 취소했습니다".into();
            return;
        };

        if self.source_focus == 0 {
            self.base_path = path.to_string_lossy().into_owned();
            self.source_focus = 1;
            self.status = "Base를 선택했습니다. Donor를 선택하세요".into();
        } else {
            self.donor_path = path.to_string_lossy().into_owned();
            self.source_focus = 2;
            self.status = "Donor를 선택했습니다. Font를 분석할 수 있습니다".into();
        }
    }

    fn handle_groups_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Esc => {
                self.screen = Screen::Sources;
                self.status = "Base와 Donor를 수정할 수 있습니다".into();
            }
            KeyCode::Char('q') => self.should_quit = true,
            KeyCode::Tab => self.group_action_focused = true,
            KeyCode::BackTab => self.group_action_focused = false,
            KeyCode::Up if self.group_action_focused => self.group_action_focused = false,
            KeyCode::Up => self.group_index = self.group_index.saturating_sub(1),
            KeyCode::Down if self.group_action_focused => {}
            KeyCode::Down => {
                if self.group_index + 1 < self.groups.len() {
                    self.group_index += 1;
                } else {
                    self.group_action_focused = true;
                }
            }
            KeyCode::Char(' ') if !self.group_action_focused => {
                if let Some(group) = self.groups.get_mut(self.group_index)
                    && !group.locked
                {
                    group.selected = !group.selected;
                }
            }
            KeyCode::Char('a') => {
                for group in &mut self.groups {
                    group.selected = true;
                }
            }
            KeyCode::Char('n') => {
                for group in &mut self.groups {
                    group.selected = group.locked;
                }
            }
            KeyCode::Enter if self.group_action_focused => {
                self.screen = Screen::Settings;
                self.status = "Font 설정을 확인하세요".into();
            }
            _ => {}
        }
    }

    fn handle_settings_key(&mut self, key: KeyEvent) {
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('u') {
            if self.settings_focus < 3 {
                self.focused_setting_mut().clear();
            }
            return;
        }
        match key.code {
            KeyCode::Esc => {
                self.screen = Screen::Groups;
                self.status = "Unicode group 선택으로 돌아왔습니다".into();
            }
            KeyCode::Tab | KeyCode::Down => self.settings_focus = (self.settings_focus + 1) % 4,
            KeyCode::BackTab | KeyCode::Up => self.settings_focus = (self.settings_focus + 3) % 4,
            KeyCode::Enter if self.settings_focus < 3 => self.settings_focus += 1,
            KeyCode::Enter => {
                let Some(analysis) = &self.analysis else {
                    self.status = "오류: Font analysis 결과가 없습니다".into();
                    return;
                };
                self.output_path = default_output_path(
                    &analysis.base.path,
                    &self.family_name,
                    analysis.base.flavor.extension(),
                )
                .to_string_lossy()
                .into_owned();
                self.output_focus = 0;
                self.screen = Screen::Output;
                self.status = "Output path를 확인하고 Font를 build하세요".into();
            }
            KeyCode::Backspace if self.settings_focus < 3 => {
                self.focused_setting_mut().pop();
            }
            KeyCode::Char(character) if self.settings_focus < 3 => {
                self.focused_setting_mut().push(character);
            }
            _ => {}
        }
    }

    fn handle_output_key(&mut self, key: KeyEvent) {
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('u') {
            if self.output_focus == 0 {
                self.output_path.clear();
            }
            return;
        }
        match key.code {
            KeyCode::Esc => {
                self.screen = Screen::Settings;
                self.status = "Font 설정으로 돌아왔습니다".into();
            }
            KeyCode::Tab | KeyCode::Down | KeyCode::BackTab | KeyCode::Up => {
                self.output_focus = (self.output_focus + 1) % 2;
            }
            KeyCode::Enter if self.output_focus == 0 => self.output_focus = 1,
            KeyCode::Enter if self.output_path.trim().is_empty() => {
                self.status = "오류: Output path가 비어 있습니다".into();
            }
            KeyCode::Enter => {
                if let Err(error) = self.start_build() {
                    self.status = format!("오류: {error:#}");
                }
            }
            KeyCode::Backspace if self.output_focus == 0 => {
                self.output_path.pop();
            }
            KeyCode::Char(character) if self.output_focus == 0 => {
                self.output_path.push(character);
            }
            _ => {}
        }
    }

    fn handle_progress_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Up | KeyCode::PageUp => {
                let amount = if key.code == KeyCode::PageUp { 5 } else { 1 };
                let maximum = self.build_logs.len().saturating_sub(1);
                self.build_log_scroll = (self.build_log_scroll + amount).min(maximum);
            }
            KeyCode::Down | KeyCode::PageDown => {
                let amount = if key.code == KeyCode::PageDown { 5 } else { 1 };
                self.build_log_scroll = self.build_log_scroll.saturating_sub(amount);
            }
            KeyCode::Esc | KeyCode::Enter if self.build_error.is_some() => {
                self.screen = Screen::Output;
                self.status = "Output 설정으로 돌아왔습니다".into();
            }
            _ => {}
        }
    }

    fn focused_setting_mut(&mut self) -> &mut String {
        match self.settings_focus {
            0 => &mut self.family_name,
            1 => &mut self.weight_min,
            _ => &mut self.weight_max,
        }
    }
}

fn format_number(value: f64) -> String {
    if value.fract() == 0.0 {
        format!("{value:.0}")
    } else {
        value.to_string()
    }
}

fn default_output_path(base_path: &str, family_name: &str, extension: &str) -> PathBuf {
    let mut output_path = PathBuf::from(base_path);
    output_path.set_file_name(format!("{}-Variable.{extension}", family_name.trim()));
    output_path
}

fn initial_build_steps() -> Vec<BuildProgressItem> {
    BuildStep::ALL
        .into_iter()
        .map(|step| BuildProgressItem {
            step,
            status: BuildProgressStatus::Pending,
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn starts_without_default_font_paths() {
        let app = App::new();

        assert!(app.base_path.is_empty());
        assert!(app.donor_path.is_empty());
    }

    #[test]
    fn default_output_path_uses_family_name_next_to_base_font() {
        assert_eq!(
            default_output_path("/fonts/BaseVariable.ttf", "Custom Family", "ttf"),
            PathBuf::from("/fonts/Custom Family-Variable.ttf")
        );
        assert_eq!(
            default_output_path("/fonts/BaseVariable.otf", "Custom Family", "otf"),
            PathBuf::from("/fonts/Custom Family-Variable.otf")
        );
    }

    #[test]
    fn groups_advance_only_from_action_button() {
        let mut app = App::new();
        app.screen = Screen::Groups;

        app.handle_groups_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(app.screen, Screen::Groups);

        app.handle_groups_key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE));
        app.handle_groups_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(app.screen, Screen::Settings);
    }

    #[test]
    fn output_path_is_set_from_configured_family_name() {
        let mut app = App::new();
        app.family_name = "Configured Family".into();
        app.analysis = Some(test_analysis("/fonts/BaseVariable.ttf"));
        app.settings_focus = 3;

        app.handle_settings_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));

        assert_eq!(app.screen, Screen::Output);
        assert_eq!(app.output_path, "/fonts/Configured Family-Variable.ttf");
        assert_eq!(app.output_focus, 0);
        assert!(!app.busy);
        assert!(app.pending.is_none());
    }

    #[test]
    fn output_path_is_editable_and_invalid_build_stays_in_output() {
        let mut app = App::new();
        app.screen = Screen::Output;
        app.weight_min = "100".into();
        app.weight_max = "900".into();

        app.handle_output_key(KeyEvent::new(KeyCode::Char('b'), KeyModifiers::NONE));
        assert_eq!(app.output_path, "b");
        assert!(!app.busy);

        app.output_focus = 1;
        app.handle_output_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(app.screen, Screen::Output);
        assert_eq!(app.status, "오류: 선택된 Unicode group이 없습니다");
        assert!(app.build_receiver.is_none());
    }

    #[test]
    fn progress_events_mark_the_failed_step_and_keep_logs() {
        let mut app = App::new();
        app.screen = Screen::Progress;

        app.apply_build_event(worker::BuildEvent::Progress {
            step: BuildStep::ValidateInputs,
            status: BuildStepStatus::Completed,
            message: "Input validation 완료".into(),
        });
        app.apply_build_event(worker::BuildEvent::Progress {
            step: BuildStep::AnalyzeSources,
            status: BuildStepStatus::Running,
            message: "Source analysis 중".into(),
        });
        app.apply_build_event(worker::BuildEvent::Error {
            message: "Donor wght range 오류".into(),
        });

        assert_eq!(app.build_steps[0].status, BuildProgressStatus::Completed);
        assert_eq!(app.build_steps[1].status, BuildProgressStatus::Failed);
        assert_eq!(app.build_logs.len(), 3);
        assert_eq!(app.build_error.as_deref(), Some("Donor wght range 오류"));

        app.handle_progress_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
        assert_eq!(app.screen, Screen::Output);
    }

    #[test]
    fn completed_build_automatically_advances_to_done_phase() {
        use crate::model::FontFlavor;

        let mut app = App::new();
        app.screen = Screen::Progress;
        app.apply_build_event(worker::BuildEvent::Result {
            result: BuildResponse {
                output_path: "/fonts/Custom-Variable.ttf".into(),
                flavor: FontFlavor::Ttf,
                codepoint_count: 10,
                base_kept: 8,
                donor_repaired: 1,
                donor_added: 1,
                unavailable: 0,
                sample_weights: vec![100.0, 400.0, 900.0],
            },
        });

        assert_eq!(app.screen, Screen::Done);
        assert_eq!(app.status, "완료: /fonts/Custom-Variable.ttf");
    }

    #[test]
    fn active_build_cannot_quit_and_leave_the_worker_running() {
        let mut app = App::new();
        let (_sender, receiver) = mpsc::channel();
        app.screen = Screen::Progress;
        app.build_receiver = Some(receiver);

        app.handle_key(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL));

        assert!(!app.should_quit);
        assert_eq!(app.status, "Build 진행 중에는 종료할 수 없습니다");
    }

    fn test_analysis(base_path: &str) -> AnalysisResponse {
        use crate::model::{AxisInfo, FontFlavor, FontInfo};

        let font = |path: &str, family: &str| FontInfo {
            path: path.into(),
            family: family.into(),
            flavor: FontFlavor::Ttf,
            units_per_em: 1_000,
            cmap_count: 0,
            axes: vec![AxisInfo {
                tag: "wght".into(),
                minimum: 100.0,
                default: 400.0,
                maximum: 900.0,
            }],
        };

        AnalysisResponse {
            base: font(base_path, "Base"),
            donor: font("/fonts/DonorVariable.ttf", "Donor"),
            codepoints: Vec::new(),
        }
    }

    #[test]
    fn analyze_requires_both_font_selections() {
        let mut app = App::new();
        app.source_focus = 2;

        app.handle_sources_key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));

        assert!(!app.busy);
        assert!(app.pending.is_none());
        assert_eq!(app.status, "오류: Base와 Donor를 모두 선택해야 합니다");
    }
}
