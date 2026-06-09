use crate::{
    db::activity_graph::ActivityGraph,
    models::GraphNode,
};
use anyhow::Result;
use chrono::{DateTime, Utc};

// ---------------------------------------------------------------------------
// Public API (mirrors Python patcha/db/retrieval/graph_context.py)
// ---------------------------------------------------------------------------

pub fn get_activity_context(
    graph: &ActivityGraph,
    app: Option<&str>,
    time: Option<&str>,
    direction: &str,
    count: usize,
) -> Result<String> {
    let at = parse_time(time);
    let header = format!("# Activity context{}", anchor_header(app, time));
    let anchor = graph.find_event(app, at)?;
    let Some(anchor) = anchor else {
        return Ok(format!("{header}\nNo matching event found in the activity graph."));
    };

    let mut lines: Vec<String> = Vec::new();

    if direction == "before" || direction == "both" {
        let before = graph.events_before(&anchor.id, count)?;
        for node in before.into_iter().rev() {
            lines.push(format!("  {}", format_node(&node)));
        }
    }

    lines.push(format!("> {}", format_node(&anchor)));

    if direction == "after" || direction == "both" {
        let after = graph.events_after(&anchor.id, count)?;
        for node in after {
            lines.push(format!("  {}", format_node(&node)));
        }
    }

    if let Ok(Some(session_id)) = graph.session_of(&anchor.id) {
        let session_node = graph.get_node(&session_id)?;
        let apps = graph.apps_in_session(&session_id).unwrap_or_default();
        let mut sorted_apps = apps;
        sorted_apps.sort();
        let span = session_node
            .as_ref()
            .map(|n| {
                let start = n
                    .props
                    .get("start_ts")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                hhmm(start)
            })
            .unwrap_or_else(|| "??:??".into());
        lines.push(format!(
            "\nSession: {span} | apps: {}",
            sorted_apps.join(", ")
        ));
    }

    Ok(format!("{header}\n{}", lines.join("\n")))
}

pub fn get_session(
    graph: &ActivityGraph,
    app: Option<&str>,
    time: Option<&str>,
) -> Result<String> {
    let at = parse_time(time);
    let header = format!("# Session{}", anchor_header(app, time));
    let anchor = graph.find_event(app, at)?;
    let Some(anchor) = anchor else {
        return Ok(format!("{header}\nNo matching event found in the activity graph."));
    };

    let session_id = match graph.session_of(&anchor.id)? {
        Some(id) => id,
        None => return Ok(format!("{header}\nAnchor event has no session.")),
    };

    let session_node = graph.get_node(&session_id)?;
    let span = session_node
        .as_ref()
        .map(|n| {
            let start = n
                .props
                .get("start_ts")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            hhmm(start)
        })
        .unwrap_or_else(|| "??:??".into());

    let mut apps = graph.apps_in_session(&session_id).unwrap_or_default();
    apps.sort();

    let event_ids = graph.events_in_session(&session_id).unwrap_or_default();
    let mut events: Vec<GraphNode> = event_ids
        .iter()
        .filter_map(|id| graph.get_node(id).ok().flatten())
        .collect();
    events.sort_by_key(|n| {
        n.props
            .get("ts_ms")
            .and_then(|v| v.as_i64())
            .unwrap_or(0)
    });

    let mut lines = vec![
        format!("Span: {span}"),
        format!("Apps: {}", apps.join(", ")),
        format!("Events ({}):", events.len()),
    ];
    for node in &events {
        lines.push(format!("  {}", format_node(node)));
    }

    Ok(format!("{header}\n{}", lines.join("\n")))
}

pub fn find_connected(
    graph: &ActivityGraph,
    file: Option<&str>,
    project: Option<&str>,
    url: Option<&str>,
    app: Option<&str>,
) -> Result<String> {
    let (target, target_type, label) = if let Some(f) = file {
        (f, "File", format!("file '{f}'"))
    } else if let Some(p) = project {
        (p, "Project", format!("project '{p}'"))
    } else if let Some(u) = url {
        (u, "URL", format!("url '{u}'"))
    } else if let Some(a) = app {
        (a, "App", format!("app '{a}'"))
    } else {
        return Ok("# Connected activity\nProvide one of: file, project, url, app.".into());
    };

    let header = format!("# Connected activity — {label}");
    let mut events = graph.events_touching(target, target_type)?;
    if events.is_empty() {
        return Ok(format!("{header}\nNo events connected to this {target_type}."));
    }

    events.sort_by_key(|n| {
        n.props
            .get("ts_ms")
            .and_then(|v| v.as_i64())
            .unwrap_or(0)
    });

    let mut lines = vec![format!("Events ({}):", events.len())];
    for node in &events {
        lines.push(format!("  {}", format_node(node)));
    }

    Ok(format!("{header}\n{}", lines.join("\n")))
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn parse_time(value: Option<&str>) -> Option<DateTime<Utc>> {
    value.and_then(|s| {
        DateTime::parse_from_rfc3339(&s.replace('Z', "+00:00"))
            .ok()
            .map(|dt| dt.with_timezone(&Utc))
    })
}

fn hhmm(ts: &str) -> String {
    if ts.len() >= 16 {
        format!("{} {}", &ts[..10], &ts[11..16])
    } else {
        "??:??".into()
    }
}

fn format_node(node: &GraphNode) -> String {
    let p = &node.props;
    let ts = p.get("timestamp").and_then(|v| v.as_str()).unwrap_or("");
    let etype = p
        .get("type")
        .and_then(|v| v.as_str())
        .unwrap_or(node.label.as_str());

    let detail = if let Some(gist) = p.get("gist").and_then(|v| v.as_str()) {
        gist.to_owned()
    } else {
        let app = p.get("app_name").and_then(|v| v.as_str()).unwrap_or("");
        let title = p
            .get("window_title")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let proj = p.get("project").and_then(|v| v.as_str()).unwrap_or("");
        if !title.is_empty() {
            format!("{app} — {title}")
        } else if !app.is_empty() {
            app.to_owned()
        } else if !proj.is_empty() {
            proj.to_owned()
        } else {
            String::new()
        }
    };

    format!("[{}] {}: {}", hhmm(ts), etype, detail).trim_end().to_owned()
}

fn anchor_header(app: Option<&str>, time: Option<&str>) -> String {
    let mut parts = Vec::new();
    if let Some(a) = app {
        parts.push(format!("app={a}"));
    }
    if let Some(t) = time {
        parts.push(format!("time={t}"));
    }
    if parts.is_empty() {
        " (most recent)".into()
    } else {
        format!(" ({})", parts.join(", "))
    }
}
