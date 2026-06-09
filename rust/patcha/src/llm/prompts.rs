use anyhow::Result;
use minijinja::Environment;
use std::sync::OnceLock;

// Bake templates in at compile time — no runtime file I/O needed.
const SYSTEM_TEMPLATE: &str = include_str!("../../prompts/system.j2");
const USER_TEMPLATE: &str = include_str!("../../prompts/user.j2");

fn env() -> &'static Environment<'static> {
    static ENV: OnceLock<Environment<'static>> = OnceLock::new();
    ENV.get_or_init(|| {
        let mut e = Environment::new();
        e.add_template("system.j2", SYSTEM_TEMPLATE)
            .expect("system.j2 parse error");
        e.add_template("user.j2", USER_TEMPLATE)
            .expect("user.j2 parse error");
        e
    })
}

/// Render the system prompt (system.j2).
pub fn render_system() -> Result<String> {
    let tmpl = env().get_template("system.j2")?;
    Ok(tmpl
        .render(minijinja::Value::UNDEFINED)?
        .trim()
        .to_owned())
}

/// Render a named macro from user.j2 with the given keyword arguments.
///
/// `kwargs` must be a `serde_json::Value::Object`; each key becomes a template
/// variable and a keyword argument to the macro call.
///
/// # Example
/// ```rust
/// let result = render_user("task_analysis", serde_json::json!({
///     "activities": "...",
///     "duration_minutes": 45,
///     "activity_count": 10,
///     "start_time": "09:00",
///     "end_time": "09:45",
///     "primary_category": "Coding",
///     "primary_project": "patcha",
///     "accomplishment_count": 5,
/// }))?;
/// ```
pub fn render_user(macro_name: &str, kwargs: serde_json::Value) -> Result<String> {
    let wrapper = build_macro_wrapper("user.j2", macro_name, &kwargs);
    let ctx = minijinja::Value::from_serialize(&kwargs);
    let result = env().render_str(&wrapper, ctx)?;
    Ok(result.trim().to_owned())
}

/// Build a one-shot wrapper template that imports `macro_name` from `template`
/// and calls it with all kwargs keys as named arguments.
///
/// Example output for macro_name="task_analysis", keys=["activities","duration_minutes"]:
/// ```jinja
/// {% from 'user.j2' import task_analysis %}{{ task_analysis(activities=activities, duration_minutes=duration_minutes) }}
/// ```
fn build_macro_wrapper(template: &str, macro_name: &str, kwargs: &serde_json::Value) -> String {
    let args = if let serde_json::Value::Object(map) = kwargs {
        map.keys()
            .map(|k| format!("{k}={k}"))
            .collect::<Vec<_>>()
            .join(", ")
    } else {
        String::new()
    };

    format!(
        "{{% from '{template}' import {macro_name} -%}}{{{{ {macro_name}({args}) }}}}"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn system_prompt_renders() {
        let s = render_system().unwrap();
        assert!(!s.is_empty(), "system prompt should not be empty");
    }

    #[test]
    fn render_user_category_summary() {
        let result = render_user(
            "category_summary",
            serde_json::json!({
                "category_name": "Coding",
                "event_count": 5,
                "summaries": ["wrote tests", "fixed a bug"],
                "extra_count": 0,
            }),
        )
        .unwrap();
        assert!(result.contains("Coding"), "output should mention Coding");
    }

    #[test]
    fn render_user_overall_summary() {
        let result = render_user(
            "overall_summary",
            serde_json::json!({
                "date_str": "2026-06-03",
                "total_events": 42,
                "top_categories_str": "Coding, Research",
                "projects_str": "patcha",
            }),
        )
        .unwrap();
        assert!(result.contains("2026-06-03"));
    }
}
