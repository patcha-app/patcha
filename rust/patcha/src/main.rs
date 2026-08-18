use anyhow::Result;
use clap::{Parser, Subcommand};
use patcha::{cli, config, daemon, mcp};
use tracing_subscriber::{fmt, EnvFilter};

#[derive(Parser)]
#[command(
    name = "patcha",
    version,
    about = "Local observability for your computer"
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    // Daemon control
    #[command(name = "daemon")]
    Daemon(daemon::DaemonArgs),

    // MCP server
    #[command(name = "mcp")]
    Mcp(mcp::McpArgs),

    // Collection
    #[command(name = "collect", about = "Run one collection cycle")]
    Collect(cli::collect::CollectArgs),
    #[command(name = "observe", about = "Collect and cluster without LLM")]
    Observe(cli::observe::ObserveArgs),
    #[command(
        name = "caption-eval",
        about = "Run the FastVLM captioner over a folder of screenshots"
    )]
    CaptionEval(cli::caption_eval::CaptionEvalArgs),

    // Summarization
    #[command(name = "summarize", about = "Generate daily summary")]
    Summarize(cli::summarize::SummarizeArgs),

    // Search
    #[command(name = "search", about = "Semantic search across activities")]
    Search(cli::search::SearchArgs),
    #[command(name = "review", about = "Review activities over a date range")]
    Review(cli::review::ReviewArgs),

    // Clustering / patterns
    #[command(name = "cluster", about = "Cluster activities by similarity")]
    Cluster(cli::cluster::ClusterArgs),
    #[command(name = "patterns", about = "Find recurring activity patterns")]
    Patterns(cli::patterns::PatternsArgs),

    // Tasks
    #[command(name = "tasks", about = "List tasks")]
    Tasks(cli::tasks::TasksArgs),
    #[command(name = "task-details", about = "Show task details")]
    TaskDetails(cli::tasks::TaskDetailsArgs),
    #[command(name = "task-summary", about = "Daily task summary")]
    TaskSummary(cli::tasks::TaskSummaryArgs),
    #[command(name = "search-tasks", about = "Semantic search for tasks")]
    SearchTasks(cli::tasks::SearchTasksArgs),
    #[command(name = "complete-task", about = "Mark a task as completed")]
    CompleteTask(cli::tasks::CompleteTaskArgs),
    #[command(name = "task-stats", about = "Productivity statistics")]
    TaskStats(cli::tasks::TaskStatsArgs),
    #[command(
        name = "identify-tasks",
        about = "Identify tasks from activities for a date"
    )]
    IdentifyTasks(cli::tasks::IdentifyTasksArgs),
    #[command(name = "compact-day", about = "Compact raw activities into tasks")]
    CompactDay(cli::compact::CompactDayArgs),

    // RAG / graph
    #[command(name = "rag-summary", about = "RAG-enhanced summary")]
    RagSummary(cli::rag::RagSummaryArgs),
    #[command(name = "project-analysis", about = "Project analysis with RAG")]
    ProjectAnalysis(cli::rag::ProjectAnalysisArgs),
    #[command(name = "contextual-search", about = "Search with RAG insights")]
    ContextualSearch(cli::rag::ContextualSearchArgs),
    #[command(name = "analyze-graph", about = "Analyze knowledge graph")]
    AnalyzeGraph(cli::graph::AnalyzeGraphArgs),
    #[command(name = "search-graph", about = "Search knowledge graph")]
    SearchGraph(cli::graph::SearchGraphArgs),
    #[command(name = "graph-stats", about = "Knowledge graph statistics")]
    GraphStats(cli::graph::GraphStatsArgs),

    // Auth / system
    #[command(name = "login", about = "Authenticate with patcha cloud")]
    Login(cli::auth::LoginArgs),
    #[command(name = "logout", about = "Sign out")]
    Logout(cli::auth::LogoutArgs),
    #[command(name = "update", about = "Check for updates")]
    Update(cli::auth::UpdateArgs),
    #[command(name = "models", about = "List available models")]
    Models(cli::auth::ModelsArgs),

    // Maintenance
    #[command(name = "reembed", about = "Re-embed stored events with current model")]
    Reembed(cli::maintenance::ReembedArgs),
    #[command(name = "migrate", about = "Migrate data from Qdrant to sqlite-vec")]
    Migrate(cli::maintenance::MigrateArgs),
    #[command(
        name = "categorization-analysis",
        about = "Analyze categorization performance"
    )]
    CategorizationAnalysis(cli::maintenance::CategorizationAnalysisArgs),
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli_args = Cli::parse();

    // Initialize tracing — respects RUST_LOG / LOG_LEVEL env vars
    let filter = EnvFilter::try_from_env("RUST_LOG")
        .or_else(|_| EnvFilter::try_from_env("LOG_LEVEL"))
        .unwrap_or_else(|_| EnvFilter::new("warn,patcha=info"));

    fmt().with_env_filter(filter).with_target(false).init();

    let cfg = config::Config::load()?;

    match cli_args.command {
        Commands::Daemon(args) => daemon::run(args, cfg).await,
        Commands::Mcp(args) => mcp::run(args, cfg).await,
        Commands::Collect(args) => cli::collect::run(args, cfg).await,
        Commands::Observe(args) => cli::observe::run(args, cfg).await,
        Commands::CaptionEval(args) => cli::caption_eval::run(args, cfg).await,
        Commands::Summarize(args) => cli::summarize::run(args, cfg).await,
        Commands::Search(args) => cli::search::run(args, cfg).await,
        Commands::Review(args) => cli::review::run(args, cfg).await,
        Commands::Cluster(args) => cli::cluster::run(args, cfg).await,
        Commands::Patterns(args) => cli::patterns::run(args, cfg).await,
        Commands::Tasks(args) => cli::tasks::run_list(args, cfg).await,
        Commands::TaskDetails(args) => cli::tasks::run_details(args, cfg).await,
        Commands::TaskSummary(args) => cli::tasks::run_summary(args, cfg).await,
        Commands::SearchTasks(args) => cli::tasks::run_search(args, cfg).await,
        Commands::CompleteTask(args) => cli::tasks::run_complete(args, cfg).await,
        Commands::TaskStats(args) => cli::tasks::run_stats(args, cfg).await,
        Commands::IdentifyTasks(args) => cli::tasks::run_identify(args, cfg).await,
        Commands::CompactDay(args) => cli::compact::run(args, cfg).await,
        Commands::RagSummary(args) => cli::rag::run_summary(args, cfg).await,
        Commands::ProjectAnalysis(args) => cli::rag::run_project(args, cfg).await,
        Commands::ContextualSearch(args) => cli::rag::run_search(args, cfg).await,
        Commands::AnalyzeGraph(args) => cli::graph::run_analyze(args, cfg).await,
        Commands::SearchGraph(args) => cli::graph::run_search(args, cfg).await,
        Commands::GraphStats(args) => cli::graph::run_stats(args, cfg).await,
        Commands::Login(args) => cli::auth::run_login(args, cfg).await,
        Commands::Logout(args) => cli::auth::run_logout(args, cfg).await,
        Commands::Update(args) => cli::auth::run_update(args, cfg).await,
        Commands::Models(args) => cli::auth::run_models(args, cfg).await,
        Commands::Reembed(args) => cli::maintenance::run_reembed(args, cfg).await,
        Commands::Migrate(args) => cli::maintenance::run_migrate(args, cfg).await,
        Commands::CategorizationAnalysis(args) => {
            cli::maintenance::run_categorization_analysis(args, cfg).await
        }
    }
}
