//! `patcha fetch-models` — download the FastVLM captioner model up front.
//!
//! The daemon fetches it automatically on first run; this exists for pre-seeding
//! a machine and for recovering when `ENABLE_MODEL_AUTO_DOWNLOAD=false`.

use anyhow::Result;
use clap::Args;

use crate::config::Config;
use crate::perception::model_fetch;

#[derive(Args, Debug)]
pub struct FetchModelsArgs {
    #[arg(long, help = "Re-download even if the model is already complete")]
    pub force: bool,
}

pub async fn run(args: FetchModelsArgs, _cfg: Config) -> Result<()> {
    let dir = model_fetch::user_model_dir();

    if args.force && dir.exists() {
        std::fs::remove_dir_all(&dir)?;
    }

    if model_fetch::is_complete(&dir) {
        println!("FastVLM model already present at {}", dir.display());
        return Ok(());
    }

    println!(
        "Downloading FastVLM model to {} (~810 MB)...",
        dir.display()
    );
    model_fetch::ensure_fastvlm(&dir).await?;
    println!("Done.");
    Ok(())
}
