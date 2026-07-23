use std::{
    io::{BufRead, BufReader, Read, Write},
    path::PathBuf,
    process::{Command, Stdio},
    sync::mpsc::Sender,
    thread,
};

use anyhow::{Context, Result, anyhow, bail};
use serde::{Deserialize, Serialize, de::DeserializeOwned};

use crate::model::{
    AnalysisResponse, AnalyzeRequest, BuildRequest, BuildResponse, BuildStep, BuildStepStatus,
};

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum BuildEvent {
    Progress {
        step: BuildStep,
        status: BuildStepStatus,
        message: String,
    },
    Result {
        result: BuildResponse,
    },
    Error {
        message: String,
    },
}

pub fn analyze(request: &AnalyzeRequest) -> Result<AnalysisResponse> {
    run_worker("analyze", request)
}

pub fn stream_build(request: BuildRequest, sender: Sender<BuildEvent>) {
    if let Err(error) = run_build_worker(&request, &sender) {
        let _ = sender.send(BuildEvent::Error {
            message: format!("{error:#}"),
        });
    }
}

fn run_worker<Request, Response>(action: &str, request: &Request) -> Result<Response>
where
    Request: Serialize,
    Response: DeserializeOwned,
{
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let mut child = Command::new("uv")
        .arg("run")
        .arg("--project")
        .arg(&manifest_dir)
        .arg("python")
        .arg("-m")
        .arg("worker.font_worker")
        .arg(action)
        .current_dir(&manifest_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("uv를 실행할 수 없습니다. uv가 PATH에 설치되어 있는지 확인하세요")?;

    let request_json = serde_json::to_vec(request)?;
    let mut stdin = child
        .stdin
        .take()
        .context("worker stdin을 열 수 없습니다")?;
    stdin.write_all(&request_json)?;
    drop(stdin);

    let output = child.wait_with_output()?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
        let message = if stderr.is_empty() {
            format!("font worker가 status {}로 종료되었습니다", output.status)
        } else {
            stderr
        };
        bail!(message);
    }

    serde_json::from_slice(&output.stdout).with_context(|| {
        let stdout = String::from_utf8_lossy(&output.stdout);
        format!("font worker 응답을 해석할 수 없습니다: {stdout}")
    })
}

fn run_build_worker(request: &BuildRequest, sender: &Sender<BuildEvent>) -> Result<()> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let mut child = Command::new("uv")
        .arg("run")
        .arg("--project")
        .arg(&manifest_dir)
        .arg("python")
        .arg("-m")
        .arg("worker.font_worker")
        .arg("build")
        .current_dir(&manifest_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .context("uv를 실행할 수 없습니다. uv가 PATH에 설치되어 있는지 확인하세요")?;

    let request_json = serde_json::to_vec(request)?;
    let mut stdin = child
        .stdin
        .take()
        .context("worker stdin을 열 수 없습니다")?;
    stdin.write_all(&request_json)?;
    drop(stdin);

    let stdout = child
        .stdout
        .take()
        .context("worker stdout을 열 수 없습니다")?;
    let mut stderr = child
        .stderr
        .take()
        .context("worker stderr를 열 수 없습니다")?;
    let stderr_reader = thread::spawn(move || {
        let mut output = String::new();
        stderr.read_to_string(&mut output).map(|_| output)
    });

    let mut final_result = None;
    let mut saw_error = false;
    for line in BufReader::new(stdout).lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let event: BuildEvent = serde_json::from_str(&line)
            .with_context(|| format!("font worker event를 해석할 수 없습니다: {line}"))?;
        match event {
            BuildEvent::Progress {
                step,
                status,
                message,
            } => sender
                .send(BuildEvent::Progress {
                    step,
                    status,
                    message,
                })
                .map_err(|_| anyhow!("build progress receiver가 닫혔습니다"))?,
            BuildEvent::Result { result } => final_result = Some(result),
            BuildEvent::Error { message } => {
                saw_error = true;
                sender
                    .send(BuildEvent::Error { message })
                    .map_err(|_| anyhow!("build progress receiver가 닫혔습니다"))?;
            }
        }
    }

    let status = child.wait()?;
    let stderr = stderr_reader
        .join()
        .map_err(|_| anyhow!("worker stderr reader가 중단되었습니다"))??;
    if !status.success() {
        if saw_error {
            return Ok(());
        }
        let message = if stderr.trim().is_empty() {
            format!("font worker가 status {status}로 종료되었습니다")
        } else {
            stderr.trim().to_owned()
        };
        bail!(message);
    }

    let result = final_result.context("font worker가 build result event를 보내지 않았습니다")?;
    sender
        .send(BuildEvent::Result { result })
        .map_err(|_| anyhow!("build progress receiver가 닫혔습니다"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_streaming_progress_event() {
        let event: BuildEvent = serde_json::from_str(
            r#"{"type":"progress","step":"generate_masters","status":"running","message":"Master 2/8"}"#,
        )
        .expect("progress event should parse");

        assert!(matches!(
            event,
            BuildEvent::Progress {
                step: BuildStep::GenerateMasters,
                status: BuildStepStatus::Running,
                message,
            } if message == "Master 2/8"
        ));
    }

    #[test]
    fn parses_streaming_error_event() {
        let event: BuildEvent = serde_json::from_str(
            r#"{"type":"error","message":"Output extension은 .ttf여야 합니다"}"#,
        )
        .expect("error event should parse");

        assert!(matches!(
            event,
            BuildEvent::Error { message } if message == "Output extension은 .ttf여야 합니다"
        ));
    }

    #[test]
    fn parses_streaming_result_event() {
        let event: BuildEvent = serde_json::from_str(
            r#"{"type":"result","result":{"output_path":"/fonts/Custom-Variable.ttf","flavor":"ttf","codepoint_count":10,"base_kept":8,"donor_repaired":1,"donor_added":1,"unavailable":0,"sample_weights":[100,400,900]}}"#,
        )
        .expect("result event should parse");

        assert!(matches!(
            event,
            BuildEvent::Result { result }
                if result.output_path == "/fonts/Custom-Variable.ttf"
                    && result.codepoint_count == 10
                    && result.sample_weights == vec![100.0, 400.0, 900.0]
        ));
    }
}
