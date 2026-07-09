pub mod chat;
pub mod server;

use crate::config::Config;
use anyhow::Result;
use clap::Args;

#[derive(Args)]
pub struct McpArgs {
    #[arg(long, help = "Run over stdio (default)")]
    pub stdio: bool,
    #[arg(long, default_value = "6969", help = "HTTP port for Streamable HTTP transport")]
    pub port: u16,
}

pub async fn run(args: McpArgs, cfg: Config) -> Result<()> {
    server::run(args, cfg).await
}
