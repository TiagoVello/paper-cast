.pragma library

// PROTOTYPE DATA — nothing here is real. No pipeline, no queue, no disk.

var SAMPLE_PDFS = [
  "attention-is-all-you-need.pdf",
  "2401.04088v1.pdf",
  "chinchilla-scaling-laws.pdf",
  "mamba-linear-time-sequence.pdf"
]

var SAMPLE_TITLES = [
  "Attention Is All You Need",
  "Mixtral of Experts",
  "Training Compute-Optimal Large Language Models",
  "Mamba: Linear-Time Sequence Modeling"
]

// Pre-written Steering, swapped through with the carousel. "Custom" is what
// you get the moment you edit the text of any of the others.
var PRESETS = [
  { name: "ML researcher", text:
      "The listener is an ML researcher. Skip the basics \u2014 no explaining what " +
      "attention, AdamW or batch norm are. Go straight to what is new here, what " +
      "the ablations actually show, and where the authors are overclaiming." },
  { name: "Skim it", text:
      "Give me the shortest useful version. Lead with the claim and the result. " +
      "Say whether it is worth reading in full and why, then stop." },
  { name: "Critical read", text:
      "Be adversarial. Spend most of the episode on what is weak: unconvincing " +
      "baselines, missing ablations, cherry-picked benchmarks, claims the " +
      "experiments do not support. Say plainly what would change your mind." },
  { name: "Teach me", text:
      "Assume I know general programming but not this subfield. Define the terms " +
      "as they come up, build up from the problem the paper is solving, and use " +
      "concrete examples rather than notation." },
  { name: "Adjacent field", text:
      "I work in a neighbouring field. Translate the contribution into terms that " +
      "travel: what problem it solves, what it would let someone build, and which " +
      "assumptions are specific to this domain." }
]

var DEFAULT_STEERING =
  "The listener is an ML researcher. Skip the basics — no explaining what " +
  "attention, AdamW or batch norm are. Go straight to what is new here, what " +
  "the ablations actually show, and where the authors are overclaiming."

function initialQueue() {
  return [
    { id: "j1", title: "Attention Is All You Need", stage: "generating",
      pct: 0.45, sources: 1, elapsed: "3m12s", link: "" },
    { id: "j2", title: "Mixtral of Experts", stage: "queued",
      pct: 0, sources: 1, elapsed: "", link: "" },
    { id: "j3", title: "Chinchilla + 2 more", stage: "failed",
      pct: 0, sources: 3, elapsed: "",
      error: "nlm auth refresh failed: session expired", link: "" },
    { id: "j4", title: "Mamba: Linear-Time Sequence Modeling", stage: "done",
      pct: 1, sources: 1, elapsed: "11m04s",
      link: "https://studio.youtube.com/video/dQw4w9WgXcQ/edit" }
  ]
}

function stageGlyph(stage) {
  if (stage === "queued") return "○"
  if (stage === "generating") return "◔"
  if (stage === "muxing") return "◑"
  if (stage === "uploading") return "◕"
  if (stage === "done") return "●"
  if (stage === "failed") return "✕"
  return "?"
}

function stageLabel(stage) {
  if (stage === "queued") return "queued"
  if (stage === "generating") return "generating"
  if (stage === "muxing") return "muxing"
  if (stage === "uploading") return "uploading"
  if (stage === "done") return "done"
  if (stage === "failed") return "failed"
  return stage
}
