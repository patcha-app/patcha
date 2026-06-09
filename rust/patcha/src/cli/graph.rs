use crate::{
    config::Config,
    db::{Db, graph::KnowledgeGraph},
};
use anyhow::Result;
use clap::Args;
use std::sync::Arc;

#[derive(Args)]
pub struct AnalyzeGraphArgs {
    #[arg(long, default_value = "7", help = "Days to look back")]
    pub days: u32,
}

#[derive(Args)]
pub struct SearchGraphArgs {
    pub query: String,
    #[arg(short, long, default_value = "10")]
    pub limit: usize,
}

#[derive(Args)]
pub struct GraphStatsArgs {}

pub async fn run_analyze(args: AnalyzeGraphArgs, cfg: Config) -> Result<()> {
    let db = Db::open(&cfg.db_path)?;
    let kg = KnowledgeGraph::new(db);
    let stats = kg.get_graph_stats()?;

    println!("Knowledge graph (last {} days):", args.days);
    println!("{}", "─".repeat(50));

    let entities = stats.get("total_entities").and_then(|v| v.as_u64()).unwrap_or(0);
    let rels = stats.get("total_relationships").and_then(|v| v.as_u64()).unwrap_or(0);
    println!("Entities:      {entities}");
    println!("Relationships: {rels}");

    if let Some(types) = stats.get("entity_types").and_then(|v| v.as_object()) {
        println!("\nEntity types:");
        let mut pairs: Vec<_> = types.iter().collect();
        pairs.sort_by_key(|(_, v)| std::cmp::Reverse(v.as_u64().unwrap_or(0)));
        for (t, n) in pairs.iter().take(8) {
            println!("  {t:<20} {}", n.as_u64().unwrap_or(0));
        }
    }

    if let Some(top) = stats.get("top_entities").and_then(|v| v.as_array()) {
        println!("\nMost-mentioned entities:");
        for e in top.iter().take(10) {
            let name = e.get("name").and_then(|v| v.as_str()).unwrap_or("?");
            let mentions = e.get("mention_count").and_then(|v| v.as_u64()).unwrap_or(0);
            let etype = e.get("entity_type").and_then(|v| v.as_str()).unwrap_or("?");
            println!("  {name:<30} {mentions:>4} mentions  [{etype}]");
        }
    }
    Ok(())
}

pub async fn run_search(args: SearchGraphArgs, cfg: Config) -> Result<()> {
    let db = Db::open(&cfg.db_path)?;
    let kg = KnowledgeGraph::new(db);

    match kg.get_entity_by_name(&args.query)? {
        None => {
            println!("Entity \"{}\" not found in knowledge graph.", args.query);
        }
        Some(entity) => {
            println!("Entity: {} [{}]", entity.name, format!("{:?}", entity.entity_type));
            println!("Mentions: {}  |  Confidence: {:.2}", entity.mention_count, entity.confidence);
            println!();

            let neighbors = kg.get_neighbors(&entity.id, 2)?;
            if neighbors.is_empty() {
                println!("No connected entities.");
            } else {
                println!("Connected entities ({}):", neighbors.len().min(args.limit));
                for n in neighbors.iter().take(args.limit) {
                    println!(
                        "  {:<30} [{:?}]  {} mentions",
                        n.name,
                        n.entity_type,
                        n.mention_count
                    );
                }
            }
        }
    }
    Ok(())
}

pub async fn run_stats(_args: GraphStatsArgs, cfg: Config) -> Result<()> {
    let db = Db::open(&cfg.db_path)?;
    let kg = KnowledgeGraph::new(db);
    let stats = kg.get_graph_stats()?;
    println!("{}", serde_json::to_string_pretty(&stats)?);

    let cleaned = kg.cleanup_old_entities()?;
    if cleaned > 0 {
        println!("\nCleaned up {cleaned} stale entities.");
    }
    Ok(())
}
