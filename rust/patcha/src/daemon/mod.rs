pub mod r#loop;

use crate::config::Config;
use anyhow::Result;
use clap::{Args, Subcommand};

#[derive(Args)]
pub struct DaemonArgs {
    #[command(subcommand)]
    pub command: DaemonCommand,
}

#[derive(Subcommand)]
pub enum DaemonCommand {
    #[command(name = "start")]
    Start,
    #[command(name = "stop")]
    Stop,
    #[command(name = "status")]
    Status,
}

pub async fn run(args: DaemonArgs, cfg: Config) -> Result<()> {
    match args.command {
        DaemonCommand::Start => r#loop::start(cfg).await,
        DaemonCommand::Stop => r#loop::stop(cfg).await,
        DaemonCommand::Status => r#loop::status(cfg).await,
    }
}
