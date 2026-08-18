// I/O signature probe for the FastVLM ONNX graphs (Phase 3 spike).
// Run: cargo run --example fastvlm_probe -- <model_dir>
// Prints each graph's input/output names + value types so the decode loop can be
// wired exactly (esp. decoder KV-cache tensor names + vision encoder output).

use ort::session::Session;

fn describe(label: &str, path: &str) -> ort::Result<()> {
    println!("\n========== {label} ==========\n  {path}");
    let session = Session::builder()?.commit_from_file(path)?;
    println!("  --- inputs ({}) ---", session.inputs.len());
    for i in &session.inputs {
        println!("    {:<34} {:?}", i.name, i.input_type);
    }
    println!("  --- outputs ({}) ---", session.outputs.len());
    for o in &session.outputs {
        println!("    {:<34} {:?}", o.name, o.output_type);
    }
    Ok(())
}

fn main() -> ort::Result<()> {
    let arg = std::env::args().nth(1).unwrap_or_else(|| ".".to_string());
    if arg.ends_with(".onnx") {
        describe("model", &arg)?;
        return Ok(());
    }
    describe(
        "vision_encoder",
        &format!("{arg}/onnx/vision_encoder_q4f16.onnx"),
    )?;
    describe(
        "embed_tokens",
        &format!("{arg}/onnx/embed_tokens_q4f16.onnx"),
    )?;
    describe(
        "decoder_model_merged",
        &format!("{arg}/onnx/decoder_model_merged_q4f16.onnx"),
    )?;
    Ok(())
}
