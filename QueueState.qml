import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons

// The Queue as the bar sees it: the Job files under ~/.local/state/paper-cast/,
// read and reduced to the handful of facts the icon and (later) the panel need.
//
// EVERYTHING THAT KNOWS THE ON-DISK JOB FORMAT LIVES IN THIS FILE. #13 owns
// that format and is being written in parallel; when it lands, the constants
// and readJob() below are the only things that change, and nothing outside
// this file has to move. Keep it that way.
//
// Read defensively. The runner writes these files while we read them, so a
// half-written or malformed Job must cost us that one Job, never the bar.
Item {
  id: queue

  // ---------------------------------------------------------------------
  // #13's contract. The one place it is spelled out.
  // ---------------------------------------------------------------------

  // Job files are JSON, one per Job, somewhere under the state directory.
  // Two levels deep so a `jobs/` subdirectory needs no change here.
  readonly property int jobDepth: 2

  // Field names inside a Job file.
  readonly property string stageKey: "stage"
  readonly property string idKey: "id"
  readonly property string titleKey: "title"

  // queued → generating → muxing → uploading → done | failed
  readonly property string stageQueued: "queued"
  readonly property string stageFailed: "failed"
  readonly property string stageDone: "done"
  // Stages where the Queue still owes you an Episode. `queued` is in here on
  // purpose: a Job waiting for the runner is outstanding work, and the icon
  // would otherwise go dark between the Jobs of a batch.
  readonly property var activeStages: ["queued", "generating", "muxing", "uploading"]

  // One Job as the rest of the plugin sees it. A record that does not answer
  // this shape is dropped, not defaulted — a Job with no stage is not a Job.
  function readJob(path, text) {
    var record = null
    try {
      record = JSON.parse(text)
    } catch (e) {
      return null // half-written, mid-flush, or not a Job file at all
    }
    if (!record || typeof record !== "object" || Array.isArray(record)) return null
    var stage = record[queue.stageKey]
    if (typeof stage !== "string" || stage === "") return null
    var id = record[queue.idKey]
    if (typeof id !== "string" || id === "") id = queue.basename(path)
    var title = record[queue.titleKey]
    return {
      id: id,
      stage: stage,
      title: typeof title === "string" ? title : "",
      path: path,
      record: record // #15 reads the rest of the Job off here
    }
  }

  // ---------------------------------------------------------------------
  // Below here nothing knows what a Job file looks like.
  // ---------------------------------------------------------------------

  // XDG state, not an Omarchy path: the headless CLI writes the same files.
  readonly property string stateDir: {
    var base = Quickshell.env("XDG_STATE_HOME")
    if (!base || base === "") base = Quickshell.env("HOME") + "/.local/state"
    return base + "/paper-cast"
  }

  // Set by the widget root from `opened`. Gates the poll below — this widget
  // is alive for the whole session, so nothing may tick while it is at rest.
  property bool polled: false

  property var jobs: []

  readonly property int activeCount: {
    var n = 0
    for (var i = 0; i < queue.jobs.length; i++)
      if (queue.activeStages.indexOf(queue.jobs[i].stage) !== -1) n++
    return n
  }

  readonly property var failedIds: {
    var out = []
    for (var i = 0; i < queue.jobs.length; i++)
      if (queue.jobs[i].stage === queue.stageFailed) out.push(queue.jobs[i].id)
    return out
  }

  function basename(path) {
    var name = String(path).substring(String(path).lastIndexOf("/") + 1)
    return name.replace(/\.json$/, "")
  }

  function refresh() {
    if (scan.running) return // never overlap runs
    scan.running = true
  }

  function parseScan(raw) {
    var next = []
    var blocks = String(raw).split("\n=== EOM ===\n")
    for (var i = 0; i < blocks.length; i++) {
      var block = blocks[i]
      var header = block.indexOf("=== JOB ")
      if (header === -1) continue
      var pathEnd = block.indexOf(" ===\n", header)
      if (pathEnd === -1) continue
      var path = block.substring(header + 8, pathEnd)
      var job = queue.readJob(path, block.substring(pathEnd + 5))
      if (job) next.push(job)
    }
    queue.jobs = next
  }

  // No jq: the shell's own plugin scan frames files in bash and parses the
  // JSON in QML (PluginRegistry.qml), and that keeps the parsing — and the
  // defensiveness — in one place instead of two languages.
  Process {
    id: scan
    command: ["bash", "-c",
      "find \"$0\" -maxdepth " + queue.jobDepth + " -type f -name '*.json' -print0 2>/dev/null"
        + " | while IFS= read -r -d '' f; do"
        + " printf '=== JOB %s ===\\n' \"$f\"; cat -- \"$f\"; printf '\\n=== EOM ===\\n'; done",
      queue.stateDir]
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: queue.parseScan(text)
    }
  }

  // The colour on the bar has to be right while the panel is shut, so the
  // always-on half of this is an inotify watch rather than a timer: it costs
  // nothing until a Job file moves. inotifywait exits non-zero when the state
  // directory does not exist yet — before the first Job is ever queued — so
  // the retry lives inside bash, where it is a sleep, rather than in a QML
  // Timer respawning a process every second for the length of a session. Only
  // the branch that actually saw something says so: a retry that reports a
  // change too would have a machine with no Queue yet rescanning every five
  // seconds for the whole session, which is the cost this watch exists to avoid.
  Process {
    id: watcher
    command: ["bash", "-c",
      "while :; do if inotifywait -q -q -e close_write,create,delete,moved_to,moved_from -r \"$0\""
        + " >/dev/null 2>&1; then printf 'changed\\n'; else sleep 5; fi; done",
      queue.stateDir]
    running: true
    stdout: SplitParser {
      onRead: queue.refresh()
    }
  }

  // The gated half. A watch can miss an event; while the panel is open the
  // Queue is being read by a human, so pay for a poll then and only then.
  Timer {
    interval: 2000
    running: queue.polled
    repeat: true
    triggeredOnStart: true
    onTriggered: queue.refresh()
  }

  // Once at startup, so the icon is the right colour before anything moves.
  Component.onCompleted: queue.refresh()
}
