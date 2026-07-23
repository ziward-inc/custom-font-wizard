use ratatui::{
    Frame,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Cell, Clear, Paragraph, Row, Table, TableState, Wrap},
};

use crate::{
    app::{App, BuildProgressItem, BuildProgressStatus, Screen},
    model::GroupCoverage,
};

const ACCENT: Color = Color::Cyan;

pub fn render(frame: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(8),
            Constraint::Length(3),
        ])
        .split(frame.area());

    render_header(frame, app, chunks[0]);
    match app.screen {
        Screen::Sources => render_sources(frame, app, chunks[1]),
        Screen::Groups => render_groups(frame, app, chunks[1]),
        Screen::Settings => render_settings(frame, app, chunks[1]),
        Screen::Output => render_output(frame, app, chunks[1]),
        Screen::Progress => render_progress(frame, app, chunks[1]),
        Screen::Done => render_done(frame, app, chunks[1]),
    }
    render_footer(frame, app, chunks[2]);

    if app.busy {
        render_busy(frame, app);
    }
}

fn render_header(frame: &mut Frame, app: &App, area: Rect) {
    let step = match app.screen {
        Screen::Sources => "1 Sources",
        Screen::Groups => "2 Select",
        Screen::Settings => "3 Configure",
        Screen::Output => "4 Output",
        Screen::Progress => "5 Build Progress",
        Screen::Done => "6 Done",
    };
    let line = Line::from(vec![
        Span::styled(
            " Custom Font Wizard ",
            Style::default()
                .fg(Color::Black)
                .bg(ACCENT)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw("  "),
        Span::styled(step, Style::default().fg(ACCENT)),
    ]);
    frame.render_widget(
        Paragraph::new(line).block(Block::default().borders(Borders::BOTTOM)),
        area,
    );
}

fn render_sources(frame: &mut Frame, app: &App, area: Rect) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .margin(1)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(2),
            Constraint::Min(1),
        ])
        .split(area);
    render_input(
        frame,
        chunks[0],
        "Base Variable Font · Enter to choose",
        selected_path(&app.base_path),
        app.source_focus == 0,
    );
    render_input(
        frame,
        chunks[1],
        "Donor Variable Font · Enter to choose",
        selected_path(&app.donor_path),
        app.source_focus == 1,
    );
    render_action(
        frame,
        chunks[2],
        "Analyze Fonts",
        app.source_focus == 2,
        !app.base_path.is_empty() && !app.donor_path.is_empty(),
    );
    frame.render_widget(
        Paragraph::new("Enter: 선택/analysis · Tab/↑/↓: 이동 · Backspace: 선택 지우기 · Esc: quit")
            .style(Style::default().fg(Color::DarkGray)),
        chunks[3],
    );
}

fn render_groups(frame: &mut Frame, app: &App, area: Rect) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(5),
            Constraint::Min(5),
            Constraint::Length(3),
            Constraint::Length(2),
        ])
        .split(area);

    if let Some(analysis) = &app.analysis {
        let base_axis = analysis.base.weight_axis();
        let donor_axis = analysis.donor.weight_axis();
        let base_range = axis_range(base_axis);
        let donor_range = axis_range(donor_axis);
        let metadata = vec![
            Line::from(format!(
                "Base   {} · {} · wght {} · {} cmap",
                analysis.base.family,
                analysis.base.flavor.label(),
                base_range,
                analysis.base.cmap_count
            )),
            Line::from(format!(
                "Donor  {} · {} · wght {} · {} cmap",
                analysis.donor.family,
                analysis.donor.flavor.label(),
                donor_range,
                analysis.donor.cmap_count
            )),
            Line::from("Space: 선택 · A: 전체 · N: 해제 · Tab/↓: button · Esc: sources"),
        ];
        frame.render_widget(
            Paragraph::new(metadata).block(Block::default().borders(Borders::BOTTOM)),
            chunks[0],
        );
    }

    let compact = chunks[1].width < 100;
    let rows = app
        .groups
        .iter()
        .map(move |group| group_row(group, compact));
    let (header, widths) = if compact {
        (
            Row::new(["선택", "Group", "합집합", "Base V/B", "Donor V/B", "보완"]),
            vec![
                Constraint::Length(5),
                Constraint::Length(18),
                Constraint::Length(8),
                Constraint::Length(13),
                Constraint::Length(13),
                Constraint::Length(8),
            ],
        )
    } else {
        (
            Row::new([
                "선택",
                "Group",
                "합집합",
                "Base visible/blank",
                "Donor visible/blank",
                "보완 가능",
                "Custom",
            ]),
            vec![
                Constraint::Length(5),
                Constraint::Length(24),
                Constraint::Length(10),
                Constraint::Length(22),
                Constraint::Length(22),
                Constraint::Length(10),
                Constraint::Length(10),
            ],
        )
    };
    let header = header
        .style(Style::default().fg(ACCENT).add_modifier(Modifier::BOLD))
        .bottom_margin(1);
    let table = Table::new(rows, widths)
        .header(header)
        .row_highlight_style(
            Style::default()
                .bg(Color::DarkGray)
                .add_modifier(Modifier::BOLD),
        )
        .highlight_symbol("▶ ")
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title("Unicode coverage"),
        );
    let selected_row = (!app.group_action_focused).then_some(app.group_index);
    let mut state = TableState::default().with_selected(selected_row);
    frame.render_stateful_widget(table, chunks[1], &mut state);
    render_action(
        frame,
        chunks[2],
        "Configure Font",
        app.group_action_focused,
        true,
    );
    frame.render_widget(Paragraph::new("↑/↓: 이동 · Enter: button 실행"), chunks[3]);
}

fn render_settings(frame: &mut Frame, app: &App, area: Rect) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .margin(1)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Min(1),
        ])
        .split(area);
    render_input(
        frame,
        chunks[0],
        "Family name",
        &app.family_name,
        app.settings_focus == 0,
    );
    render_input(
        frame,
        chunks[1],
        "wght minimum",
        &app.weight_min,
        app.settings_focus == 1,
    );
    render_input(
        frame,
        chunks[2],
        "wght maximum",
        &app.weight_max,
        app.settings_focus == 2,
    );
    render_action(
        frame,
        chunks[3],
        "Continue to Output",
        app.settings_focus == 3,
        true,
    );

    let selected_groups = app.groups.iter().filter(|group| group.selected).count();
    let selected_codepoints = app
        .groups
        .iter()
        .filter(|group| group.selected)
        .map(GroupCoverage::custom_count)
        .sum::<usize>();
    frame.render_widget(
        Paragraph::new(format!(
            "{selected_groups} groups · {selected_codepoints} codepoints · range 밖은 Base 경계로 clamp\nTab/↑/↓: 이동 · Enter: 다음 field/button 실행 · Ctrl+U: field 지우기 · Esc: groups"
        )),
        chunks[4],
    );
}

fn render_output(frame: &mut Frame, app: &App, area: Rect) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .margin(1)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Min(1),
        ])
        .split(area);
    render_input(
        frame,
        chunks[0],
        "Output path · editable",
        selected_path(&app.output_path),
        app.output_focus == 0,
    );
    render_action(
        frame,
        chunks[1],
        "Build Font",
        app.output_focus == 1,
        !app.output_path.trim().is_empty(),
    );
    frame.render_widget(
        Paragraph::new(
            "지정된 Output path에 직접 build합니다 · Tab/↑/↓: 이동 · Enter: 다음 field/button 실행 · Ctrl+U: field 지우기 · Esc: configure",
        ),
        chunks[2],
    );
}

fn render_progress(frame: &mut Frame, app: &App, area: Rect) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(9),
            Constraint::Min(5),
            Constraint::Length(2),
        ])
        .split(area);
    let steps = app.build_steps.iter().map(build_progress_line);
    frame.render_widget(
        Paragraph::new(steps.collect::<Vec<_>>()).block(
            Block::default()
                .borders(Borders::ALL)
                .title("Build progress"),
        ),
        chunks[0],
    );

    let visible_lines = chunks[1].height.saturating_sub(2) as usize;
    let end = app
        .build_logs
        .len()
        .saturating_sub(app.build_log_scroll.min(app.build_logs.len()));
    let start = end.saturating_sub(visible_lines);
    let log_lines = if start == end {
        vec![Line::from(Span::styled(
            "<waiting for worker event>",
            Style::default().fg(Color::DarkGray),
        ))]
    } else {
        app.build_logs[start..end]
            .iter()
            .map(|message| {
                let color = if message.starts_with("오류:") {
                    Color::Red
                } else {
                    Color::Gray
                };
                Line::from(Span::styled(message.as_str(), Style::default().fg(color)))
            })
            .collect()
    };
    let log_title = if app.build_log_scroll == 0 {
        "Build log · read-only · latest".to_owned()
    } else {
        format!(
            "Build log · read-only · {} lines above latest",
            app.build_log_scroll
        )
    };
    frame.render_widget(
        Paragraph::new(log_lines)
            .block(Block::default().borders(Borders::ALL).title(log_title))
            .wrap(Wrap { trim: false }),
        chunks[1],
    );

    let help = if app.build_error.is_some() {
        "Build 실패 · ↑/↓/PageUp/PageDown: log · Enter/Esc: output"
    } else {
        "Build 진행 중 · ↑/↓/PageUp/PageDown: log · Ctrl+C: quit"
    };
    frame.render_widget(Paragraph::new(help), chunks[2]);
}

fn build_progress_line(item: &BuildProgressItem) -> Line<'static> {
    let (symbol, color, modifier) = match item.status {
        BuildProgressStatus::Pending => ("[ ]", Color::DarkGray, Modifier::empty()),
        BuildProgressStatus::Running => ("[▶]", Color::Yellow, Modifier::BOLD),
        BuildProgressStatus::Completed => ("[✓]", Color::Green, Modifier::empty()),
        BuildProgressStatus::Failed => ("[✗]", Color::Red, Modifier::BOLD),
    };
    Line::from(vec![
        Span::styled(
            format!(" {symbol} "),
            Style::default().fg(color).add_modifier(modifier),
        ),
        Span::styled(item.step.label(), Style::default().fg(color)),
    ])
}

fn render_done(frame: &mut Frame, app: &App, area: Rect) {
    let text = if let Some(result) = &app.build_result {
        vec![
            Line::from(Span::styled(
                "Variable Font build 완료",
                Style::default()
                    .fg(Color::Green)
                    .add_modifier(Modifier::BOLD),
            )),
            Line::from(""),
            Line::from(format!("Output          {}", result.output_path)),
            Line::from(format!("Format          {}", result.flavor.label())),
            Line::from(format!("Codepoints      {}", result.codepoint_count)),
            Line::from(format!("Base kept       {}", result.base_kept)),
            Line::from(format!("Donor repaired  {}", result.donor_repaired)),
            Line::from(format!("Donor added     {}", result.donor_added)),
            Line::from(format!("Unavailable     {}", result.unavailable)),
            Line::from(format!("Master samples  {:?}", result.sample_weights)),
            Line::from(""),
            Line::from("Enter 또는 Q: quit"),
        ]
    } else {
        vec![Line::from("Build 결과가 없습니다")]
    };
    frame.render_widget(
        Paragraph::new(text)
            .block(Block::default().borders(Borders::ALL).title("Result"))
            .wrap(Wrap { trim: false }),
        area,
    );
}

fn render_footer(frame: &mut Frame, app: &App, area: Rect) {
    let color = if app.status.starts_with("오류") {
        Color::Red
    } else if app.busy {
        Color::Yellow
    } else {
        Color::Gray
    };
    frame.render_widget(
        Paragraph::new(app.status.as_str())
            .style(Style::default().fg(color))
            .block(Block::default().borders(Borders::TOP)),
        area,
    );
}

fn render_busy(frame: &mut Frame, app: &App) {
    let width = frame.area().width.min(64);
    let height = 5;
    let area = centered_rect(width, height, frame.area());
    frame.render_widget(Clear, area);
    frame.render_widget(
        Paragraph::new(app.status.as_str())
            .alignment(ratatui::layout::Alignment::Center)
            .block(Block::default().borders(Borders::ALL).title("Working")),
        area,
    );
}

fn render_input(frame: &mut Frame, area: Rect, title: &str, value: &str, focused: bool) {
    let style = if focused {
        Style::default().fg(ACCENT)
    } else {
        Style::default()
    };
    frame.render_widget(
        Paragraph::new(value).style(style).block(
            Block::default()
                .borders(Borders::ALL)
                .title(title)
                .border_style(style),
        ),
        area,
    );
}

fn render_action(frame: &mut Frame, area: Rect, label: &str, focused: bool, enabled: bool) {
    let style = if !enabled {
        Style::default().fg(Color::DarkGray)
    } else if focused {
        Style::default().fg(ACCENT).add_modifier(Modifier::BOLD)
    } else {
        Style::default()
    };
    frame.render_widget(
        Paragraph::new(label)
            .alignment(ratatui::layout::Alignment::Center)
            .style(style)
            .block(Block::default().borders(Borders::ALL).border_style(style)),
        area,
    );
}

fn selected_path(path: &str) -> &str {
    if path.is_empty() {
        "<not selected>"
    } else {
        path
    }
}

fn group_row(group: &GroupCoverage, compact: bool) -> Row<'static> {
    let selected = if group.locked {
        "[•]"
    } else if group.selected {
        "[✓]"
    } else {
        "[ ]"
    };
    let custom = if group.selected {
        group.custom_count().to_string()
    } else {
        "excluded".into()
    };
    let mut cells = vec![
        Cell::from(selected.to_owned()),
        Cell::from(group.label.clone()),
        Cell::from(group.union_count.to_string()),
        Cell::from(format!("{} / {}", group.base_visible, group.base_blank)),
        Cell::from(format!("{} / {}", group.donor_visible, group.donor_blank)),
        Cell::from(group.repairable.to_string()),
    ];
    if !compact {
        cells.push(Cell::from(custom));
    }
    Row::new(cells)
}

fn axis_range(axis: Option<&crate::model::AxisInfo>) -> String {
    axis.map(|axis| {
        format!(
            "{}–{}",
            format_number(axis.minimum),
            format_number(axis.maximum)
        )
    })
    .unwrap_or_else(|| "missing".into())
}

fn format_number(value: f64) -> String {
    if value.fract() == 0.0 {
        format!("{value:.0}")
    } else {
        value.to_string()
    }
}

fn centered_rect(width: u16, height: u16, area: Rect) -> Rect {
    Rect {
        x: area.x + area.width.saturating_sub(width) / 2,
        y: area.y + area.height.saturating_sub(height) / 2,
        width,
        height,
    }
}
