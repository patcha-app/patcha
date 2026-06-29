use crate::{config::Config, llm::client::PatchaApiClient};
use anyhow::Result;
use clap::Args;

#[derive(Args)]
pub struct LoginArgs {
    #[arg(long)]
    pub email: Option<String>,
}

#[derive(Args)]
pub struct LogoutArgs {}

#[derive(Args)]
pub struct UpdateArgs {}

#[derive(Args)]
pub struct ModelsArgs {}

pub async fn run_login(args: LoginArgs, cfg: Config) -> Result<()> {
    let email = match args.email {
        Some(e) => e,
        None => dialoguer::Input::<String>::new()
            .with_prompt("Email")
            .interact_text()?,
    };
    let password = dialoguer::Password::new()
        .with_prompt("Password")
        .interact()?;

    let client = PatchaApiClient::new(&cfg);
    match client.login(&email, &password).await {
        Ok(true) => println!("Logged in as {email}."),
        Ok(false) => {
            eprintln!("Login failed — check your email and password.");
            std::process::exit(1);
        }
        Err(e) => {
            eprintln!("Login error: {e}");
            std::process::exit(1);
        }
    }
    Ok(())
}

pub async fn run_logout(_args: LogoutArgs, cfg: Config) -> Result<()> {
    let client = PatchaApiClient::new(&cfg);
    client.logout().await?;
    println!("Logged out.");
    Ok(())
}

pub async fn run_update(_args: UpdateArgs, cfg: Config) -> Result<()> {
    let client = PatchaApiClient::new(&cfg);
    let current = env!("CARGO_PKG_VERSION");
    match client.check_update(current).await {
        Some(info) => {
            let latest = info
                .get("latest_version")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            println!("Update available: {current} → {latest}");
            if let Some(notes) = info.get("release_notes").and_then(|v| v.as_str()) {
                println!("{notes}");
            }
        }
        None => println!("patcha {current} is up to date."),
    }
    Ok(())
}

pub async fn run_models(_args: ModelsArgs, cfg: Config) -> Result<()> {
    let client = PatchaApiClient::new(&cfg);
    let models = client.list_models().await;
    if models.is_empty() {
        println!("No models available (are you logged in?)");
    } else {
        println!("Available models:");
        for m in &models {
            let name = m
                .get("id")
                .or_else(|| m.get("name"))
                .and_then(|v| v.as_str())
                .unwrap_or("?");
            println!("  {name}");
        }
    }
    Ok(())
}
