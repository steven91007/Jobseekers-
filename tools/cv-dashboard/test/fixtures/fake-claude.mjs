#!/usr/bin/env node
// Stand-in for `claude -p … --output-format stream-json` used by the HTTP tests.
// FAKE_CLAUDE_DELAY_MS: how long the "run" takes (default 150). A prompt containing FAKE_EDIT adds an Edit call.
const argv = process.argv.slice(2);
const prompt = argv[argv.indexOf("-p") + 1] ?? "";
const out = (o) => process.stdout.write(JSON.stringify(o) + "\n");
out({ type: "system", subtype: "init", session_id: "test", model: "fake", permissionMode: "acceptEdits" });
out({ type: "assistant", message: { content: [{ type: "tool_use", name: "Read", input: { file_path: ".claude/skills/tailor-cv/SKILL.md" } }] } });
out({ type: "user", message: { content: [{ type: "tool_result", content: "# tailor-cv", is_error: false }] } });
if (prompt.includes("FAKE_EDIT")) {
  out({ type: "assistant", message: { content: [{ type: "tool_use", name: "Edit", input: { file_path: `${process.cwd()}/cv/profile/library_seed.json` } }] } });
}
// Auto apply fill runs (prompt starts with the job-apply skill): write what the real run leaves in its cwd.
// FAKE_APPLY_STATE: filled_awaiting_confirm (default) | blocked_login | submitted (a misbehaving run) | none.
// FAKE_APPLY_URL: the form URL to report (default https://example.test/form).
if (prompt.startsWith("/job-apply:job-apply")) {
  const fs = await import("node:fs");
  const state = process.env.FAKE_APPLY_STATE ?? "filled_awaiting_confirm";
  const url = process.env.FAKE_APPLY_URL ?? "https://example.test/form";
  out({ type: "assistant", message: { content: [{ type: "tool_use", name: "mcp__playwright__browser_navigate", input: { url } }] } });
  // no letter yet → the prompt asks to generate one with the cover-letter skill: fake its outputs (unscored)
  let coverLetter = { source: "existing", uploaded: true, pasted: false };
  const gen = prompt.match(/\$D = (\S+) \(write ONLY/);
  if (gen) {
    const co = (prompt.match(/CoverLetter_AmoHsu_([A-Za-z0-9]+)\.pdf/) ?? [])[1] ?? "Co";
    out({ type: "assistant", message: { content: [{ type: "tool_use", name: "Skill", input: { skill: "cover-letter" } }] } });
    fs.mkdirSync(gen[1], { recursive: true });
    fs.writeFileSync(`${gen[1]}/CoverLetter_AmoHsu_${co}.pdf`, "%PDF-1.4 fake letter");
    fs.writeFileSync(`${gen[1]}/letter.txt`, "Dear hiring team, ...");
    fs.copyFileSync(`${gen[1]}/CoverLetter_AmoHsu_${co}.pdf`, `CoverLetter_AmoHsu_${co}.pdf`);
    coverLetter = { source: "generated", dir: gen[1], scored: false, verdict: null, uploaded: true, pasted: false };
  }
  if (state !== "none") {
    const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64");
    fs.writeFileSync("step-01.png", png);
    if (state !== "blocked_login") fs.writeFileSync("review.png", png);
    fs.writeFileSync("fields.json", JSON.stringify([
      { label: "First Name", value: "Chi-Yi", source: "profile", required: true, step: 1 },
      { label: "Years of experience", value: "6", source: "inferred", required: false, step: 1 },
      { label: "Desired salary", value: "", source: "blank", required: true, step: 1 },
      { label: "Describe one AI application *", value: "Generated draft about deliverytrack.", source: "generated", required: true, step: 1 },
    ]));
    fs.writeFileSync("status.json", JSON.stringify({
      state, reachedReview: state !== "blocked_login", submitted: state === "submitted", url, formType: "fixture",
      submitControl: { text: "Bewerbung abschicken" }, pendingCount: 1, requiredBlank: 1, coverLetter,
      blocker: state === "blocked_login" ? "sign-in page before the form" : null, finishedAt: new Date().toISOString(),
    }));
    fs.writeFileSync("summary.md", "| First Name | Chi-Yi | profile |\nNOT SUBMITTED");
  }
}
out({ type: "assistant", message: { content: [{ type: "text", text: `argv ok: ${argv.includes("-p") && argv.includes("stream-json")}` }] } });
setTimeout(() => {
  out({ type: "result", subtype: "success", result: `Done. Changed nothing. run=${process.env.CV_DASHBOARD_RUN_ID ?? ""}`, is_error: false, total_cost_usd: 0, duration_ms: 5, num_turns: 1 });
  process.exit(0);
}, Number(process.env.FAKE_CLAUDE_DELAY_MS ?? 150));
