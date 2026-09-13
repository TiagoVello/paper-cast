import QtQuick
import QtQuick.Controls as QC
import Quickshell
import Quickshell.Io
// By URI, never by relative path — see BarWidget.qml's comment; a relative
// import here would give this file its own empty Color/Style/Util singletons.
import qs.Commons
import qs.Ui

// The paper-cast dropdown: Variant B, "One surface" (issue #10, #15). No modes
// — composing a Job and watching the Queue share one stack, top to bottom:
// the Steering carousel, Sources (left) and Steering (right), the combine
// toggle, and the Queue itself.
//
// This Item is never drawn. It is loaded by BarWidget.qml's Loader for the
// logic and the KeyboardPanel it owns, which is the actual surface anchored
// under the bar icon. `bar`, `settings`, `owner`, `anchorItem`, `queue` and
// `installed` all arrive by name, after load, from BarWidget.injectPanel() —
// injected properties arrive after Component.onCompleted (see
// quickshell-widget-api.md §13), so nothing here may depend on them being set
// yet at creation time.
//
// The panel never writes config.toml or a Job file itself (#10): every change
// goes out through `paper-cast config set` / `paper-cast queue …`, so the
// CLI's own strict parser stays the single validator and the panel cannot
// invent a config the CLI would reject.
Item {
  id: root

  property QtObject bar: null
  property var settings: ({})
  property QtObject owner: null // the BarWidget/Panel root; KeyboardPanel delegates close() to it
  property Item anchorItem: null // the bar button the dropdown anchors under
  property QtObject queue: null // QueueState — read-only, #13's Job files reduced
  property bool installed: false

  readonly property bool opened: root.owner ? root.owner.opened : false

  // The theme, hoisted onto the root once rather than re-read by every child
  // (variant-b-and-dnd.md).
  readonly property color fg: Color.popups.text
  readonly property color muted: Util.alpha(root.fg, 0.55)
  readonly property color faint: Util.alpha(root.fg, 0.18)
  readonly property color accent: Color.accent
  readonly property color urgent: root.bar ? root.bar.urgent : Color.urgent
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : Style.font.family

  // ---------------------------------------------------------------------
  // Steering presets — config.toml is the source of truth (#10, #11).
  // ---------------------------------------------------------------------

  // [{name, text}, ...], mirrored from `paper-cast config list --json` and
  // kept live locally between writes. Reassigned whole on every change
  // (never mutated in place) so the carousel's bindings see a new array —
  // the settled discipline from variant-b-and-dnd.md.
  property var presets: []
  property int presetIndex: 0
  // The loaded preset's text, edited live. No character limit and no counter
  // here — the 500 cap on Google's own Customize box is not a server rule
  // and this editor does not reimplement it (#15).
  property string steering: ""

  function mutatePreset(index, patch) {
    var next = root.presets.slice()
    next[index] = Object.assign({}, next[index], patch)
    root.presets = next
  }

  onSteeringChanged: {
    // Keep the editor showing whatever is loaded, without a binding loop:
    // the editor's own onTextChanged only writes back here when it differs.
    if (steeringEditor.text !== root.steering) steeringEditor.text = root.steering
    // Editing the loaded text rewrites that preset in place, live (#15).
    if (root.presets.length && root.presets[root.presetIndex]
        && root.presets[root.presetIndex].text !== root.steering) {
      root.mutatePreset(root.presetIndex, { text: root.steering })
      steeringSaveTimer.restart() // debounced — see the Timer below
    }
  }

  function selectPreset(index) {
    var n = root.presets.length
    if (n === 0) return
    root.presetIndex = ((index % n) + n) % n
    root.steering = root.presets[root.presetIndex].text
    root.runConfigSet(presetNameWriter, "steering_preset", root.presets[root.presetIndex].name)
  }

  function cyclePreset(delta) { root.selectPreset(root.presetIndex + delta) }

  function renamePreset(newName) {
    var trimmed = String(newName).trim()
    var current = root.presets[root.presetIndex]
    if (trimmed === "" || !current || trimmed === current.name) {
      // Reject the edit rather than leave the field showing something the
      // carousel never adopted.
      if (current) presetNameEdit.text = current.name
      return
    }
    root.mutatePreset(root.presetIndex, { name: trimmed })
    // parse_config rejects a `steering_preset` that names no preset (#11), and
    // that is true of *either* order here: writing `presets` first leaves
    // `steering_preset` pointing at the name just renamed away, and writing
    // `steering_preset` first points it at a name `presets` doesn't have yet.
    // Detaching it (`""` always passes — CONTEXT.md's "hand-written" state) is
    // the only value valid before *and* after the rename, so it goes first.
    root.renameSteeringPreset(trimmed)
  }

  // Detach `steering_preset`, write the changed `presets` array, then re-point
  // `steering_preset` at `name` — the one ordering the strict parser accepts
  // no matter what `presets` looked like before. Each step waits for the
  // previous CLI call to actually finish, via `writer.onDone`.
  function renameSteeringPreset(name) {
    root.runConfigSet(presetNameWriter, "steering_preset", "")
    presetNameWriter.onDone = function() {
      root.runConfigSet(presetsWriter, "presets", JSON.stringify(root.presets))
      presetsWriter.onDone = function() {
        root.runConfigSet(presetNameWriter, "steering_preset", name)
      }
    }
  }

  function addPreset() {
    // Clones the *loaded* Steering, not a blank one — a good Steering just
    // typed becomes a preset without retyping (variant-b-and-dnd.md).
    if (root.presets.length === 0) return
    var clone = { name: "New preset", text: root.steering }
    var next = root.presets.slice()
    next.splice(root.presetIndex + 1, 0, clone)
    root.presets = next
    root.presetIndex = root.presetIndex + 1
    // Two independent `paper-cast` subprocesses started back to back still run
    // concurrently — the second one's own `load_config` can race the first
    // one's write and read `presets` before "New preset" is in it, and the
    // strict parser then rejects `steering_preset` for naming a preset that
    // (from where that read stood) doesn't exist yet. Wait for the first
    // write to actually land before starting the second.
    root.runConfigSet(presetsWriter, "presets", JSON.stringify(root.presets))
    presetsWriter.onDone = function() {
      root.runConfigSet(presetNameWriter, "steering_preset", clone.name)
    }
  }

  function deletePreset() {
    // Never the last — an empty carousel has no way back.
    if (root.presets.length <= 1) return
    var next = root.presets.slice()
    next.splice(root.presetIndex, 1)
    root.presets = next
    root.presetIndex = Math.min(root.presetIndex, next.length - 1)
    root.steering = next[root.presetIndex].text
    // Same reordering as renamePreset(): the deleted preset's name has to be
    // gone from `steering_preset` before `presets` stops holding it.
    root.renameSteeringPreset(next[root.presetIndex].name)
  }

  Timer {
    id: steeringSaveTimer
    interval: 400 // debounced: a keystroke must not shell out to `config set`
    onTriggered: {
      root.runConfigSet(presetsWriter, "presets", JSON.stringify(root.presets))
      root.runConfigSet(focusWriter, "focus", root.steering)
    }
  }

  // ---------------------------------------------------------------------
  // Sources — a PDF picker, an arXiv box, and the staged list.
  // ---------------------------------------------------------------------

  // [{kind, path, title}], the same shape `queue add` and a Job file use.
  property var staged: []
  property bool combine: false
  property string combinedTitle: ""
  property string arxivError: ""
  property string queueError: ""

  readonly property string defaultCombinedTitle: {
    if (root.staged.length === 0) return ""
    var first = root.staged[0].title || root.basename(root.staged[0].path) || "Untitled"
    var more = root.staged.length - 1
    return more > 0 ? (first + " + " + more + " more") : first
  }

  function basename(path) {
    var s = String(path || "")
    return s.substring(s.lastIndexOf("/") + 1)
  }

  // The named seam #16 calls (via BarWidget.stageDroppedPaths): stage a list
  // of paths, either plain filesystem paths or `file://` URLs (what a
  // Wayland drop hands over), without firing a Job.
  function stagePaths(paths) {
    var added = []
    for (var i = 0; i < paths.length; i++) {
      var raw = String(paths[i])
      var path = raw.indexOf("file://") === 0
        ? decodeURIComponent(raw.replace(/^file:\/\//, ""))
        : raw
      if (path === "") continue
      added.push({ kind: "pdf", path: path, title: root.basename(path) })
    }
    if (added.length === 0) return
    root.staged = root.staged.concat(added)
  }

  function unstage(index) {
    var next = root.staged.slice()
    next.splice(index, 1)
    root.staged = next
    if (next.length <= 1) root.combine = false
  }

  function resolveArxiv(ref) {
    var trimmed = String(ref).trim()
    if (trimmed === "" || resolveProc.running) return
    root.arxivError = ""
    resolveProc.pendingRef = trimmed
    resolveProc.command = root.shellCommand(["paper-cast", "resolve", trimmed, "--json"])
    resolveProc.running = true
  }

  property string resolveStderr: ""

  Process {
    id: resolveProc
    // What the user typed, carried through to `queue add`: a fetched Source has
    // no file on disk until the runner downloads it, so the reference is the
    // only thing that names it (#17).
    property string pendingRef: ""
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.handleResolved(text, resolveProc.pendingRef)
    }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: root.resolveStderr = text.trim() }
    onExited: function(code) {
      if (code === 0) {
        arxivField.text = ""
        root.arxivError = ""
      } else {
        root.arxivError = root.resolveStderr || "could not resolve that reference"
      }
    }
  }

  function handleResolved(raw, ref) {
    var parsed
    try { parsed = JSON.parse(raw) } catch (e) { return }
    if (!parsed || !parsed.kind) return
    root.staged = root.staged.concat([{
      kind: parsed.kind,
      path: parsed.path || "",
      // `queue add` resolves a reference itself (#17), and re-resolving there is
      // what validates it a second time against a paper withdrawn between
      // staging and queuing. A local PDF has no reference and needs none.
      ref: ref || "",
      title: parsed.title || parsed.id || ""
    }])
  }

  // ---------------------------------------------------------------------
  // Queuing a Job.
  // ---------------------------------------------------------------------

  function queueStaged() {
    if (root.staged.length === 0 || queueAddProc.running) return
    var argv = ["paper-cast", "queue", "add"]
    for (var i = 0; i < root.staged.length; i++) {
      var source = root.staged[i]
      // A fetched Source is named by what the user typed; `queue add` resolves
      // it again and downloads it into the Run directory (#17). Only a Source
      // that is neither on disk nor named by a reference is unqueueable.
      var named = source.path || source.ref
      if (!named) {
        root.queueError = "“" + (source.title || "a Source") + "” is not a paper we can fetch"
        return
      }
      argv.push(named)
    }
    var combining = root.combine && root.staged.length > 1
    if (combining) {
      argv.push("--combine")
      var title = root.combinedTitle.trim() !== "" ? root.combinedTitle : root.defaultCombinedTitle
      if (title !== "") { argv.push("--title"); argv.push(title) }
    }
    argv.push("--steering-stdin")
    queueAddProc.pendingValue = root.steering
    queueAddProc.stdinEnabled = true
    queueAddProc.command = root.shellCommand(argv)
    root.queueError = ""
    queueAddProc.running = true
  }

  property string queueAddStderr: ""

  Process {
    id: queueAddProc
    property string pendingValue: ""
    stdinEnabled: true
    onStarted: {
      write(pendingValue)
      pendingValue = ""
      stdinEnabled = false
    }
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector { waitForEnd: true; onStreamFinished: root.queueAddStderr = text.trim() }
    onExited: function(code) {
      if (code === 0) {
        root.staged = []
        root.combine = false
        root.combinedTitle = ""
        root.queueError = ""
        if (root.queue && typeof root.queue.refresh === "function") root.queue.refresh()
      } else {
        root.queueError = root.queueAddStderr || "queueing failed"
      }
    }
  }

  function retryJob(id) {
    retryProc.command = root.shellCommand(["paper-cast", "queue", "retry", id])
    retryProc.running = true
  }

  Process {
    id: retryProc
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("paper-cast: retry failed:", text.trim())
    }
    onExited: if (root.queue && typeof root.queue.refresh === "function") root.queue.refresh()
  }

  function stageGlyph(stage) {
    switch (stage) {
      case "queued": return "○"
      case "generating": return "◔"
      case "muxing": return "◑"
      case "uploading": return "◕"
      case "done": return "●"
      case "failed": return "✕"
      default: return "○"
    }
  }

  function sourceSummary(job) {
    var sources = (job.record && job.record.sources) || []
    if (sources.length === 0) return job.id
    var names = []
    for (var i = 0; i < sources.length; i++)
      names.push(sources[i].title || root.basename(sources[i].path) || "?")
    return names.join(", ")
  }

  function elapsedLabel(job) {
    // `started_at` survives a retry until the runner actually picks the Job
    // back up (#13's queue_cli.retry_command only flips `stage`), so a
    // freshly-retried, still-`queued` Job can carry a stale timestamp from
    // its last attempt — show elapsed only while something is actually
    // running, or "queued" would misread as "queued for 8 hours".
    if (job.stage !== "generating" && job.stage !== "muxing" && job.stage !== "uploading") return ""
    var startedRaw = job.record && job.record.started_at
    if (!startedRaw) return ""
    var started = Date.parse(startedRaw)
    if (isNaN(started)) return ""
    var seconds = Math.max(0, Math.round((Date.now() - started) / 1000))
    var minutes = Math.floor(seconds / 60)
    return minutes > 0 ? (minutes + "m " + (seconds % 60) + "s") : (seconds + "s")
  }

  // ---------------------------------------------------------------------
  // Talking to the CLI. `Process` does not inherit a login shell's PATH
  // (`~/.local/bin` is only on PATH via the login profile), so every call
  // to `paper-cast` goes through `bash -lc`, and every argument is quoted —
  // a Steering or a Source path is prose and a filesystem path, never
  // something safe to splice into a shell string unescaped.
  // ---------------------------------------------------------------------

  function shellCommand(argv) {
    var quoted = []
    for (var i = 0; i < argv.length; i++) quoted.push(Util.shellQuote(String(argv[i])))
    return ["bash", "-lc", quoted.join(" ")]
  }

  function runConfigSet(proc, key, value) {
    proc.stdinEnabled = true
    proc.pendingValue = value
    proc.command = root.shellCommand(["paper-cast", "config", "set", key, "--stdin"])
    proc.running = true
  }

  // One `Process` per key rather than one shared instance: a rename and an
  // edit can land in the same tick (renamePreset writes presets *and*
  // steering_preset), and each needs its own stdin pipe to close cleanly.
  // `onDone` is how renameSteeringPreset() sequences three of these calls in a
  // row without a second one starting before the first's write has landed.
  Process {
    id: presetsWriter
    property string pendingValue: ""
    property var onDone: null
    stdinEnabled: true
    onStarted: { write(pendingValue); pendingValue = ""; stdinEnabled = false }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("paper-cast: config set presets:", text.trim())
    }
    onExited: { var cb = onDone; onDone = null; if (typeof cb === "function") cb() }
  }
  Process {
    id: focusWriter
    property string pendingValue: ""
    property var onDone: null
    stdinEnabled: true
    onStarted: { write(pendingValue); pendingValue = ""; stdinEnabled = false }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("paper-cast: config set focus:", text.trim())
    }
    onExited: { var cb = onDone; onDone = null; if (typeof cb === "function") cb() }
  }
  Process {
    id: presetNameWriter
    property string pendingValue: ""
    property var onDone: null
    stdinEnabled: true
    onStarted: { write(pendingValue); pendingValue = ""; stdinEnabled = false }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("paper-cast: config set steering_preset:", text.trim())
    }
    onExited: { var cb = onDone; onDone = null; if (typeof cb === "function") cb() }
  }

  function refreshConfig() {
    if (configLoadProc.running) return
    configLoadProc.command = root.shellCommand(["paper-cast", "config", "list", "--json"])
    configLoadProc.running = true
  }

  Process {
    id: configLoadProc
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.applyConfig(text) }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text.trim() !== "") console.warn("paper-cast: config list:", text.trim())
    }
  }

  function applyConfig(raw) {
    var parsed
    try { parsed = JSON.parse(raw) } catch (e) { return }
    if (!parsed || !Array.isArray(parsed.presets) || parsed.presets.length === 0) return
    root.presets = parsed.presets
    var index = 0
    for (var i = 0; i < root.presets.length; i++)
      if (root.presets[i].name === parsed.steering_preset) { index = i; break }
    root.presetIndex = index
    root.steering = root.presets[index].text
  }

  onOpenedChanged: {
    if (!root.opened) return
    if (root.installed) root.refreshConfig()
    Qt.callLater(function() { if (keyCatcher) keyCatcher.forceActiveFocus() })
  }

  onInstalledChanged: if (root.installed && root.opened) root.refreshConfig()

  // "Setup" is a command the user runs (#10, #12) — the plugin system has no
  // install hook. The presentation wrapper builds a `bash -c` string from all
  // its args joined with spaces, so anything interpolated into it must go
  // through `printf '%q'` first; there is nothing interpolated here.
  function runSetup() {
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", "paper-cast setup"])
  }

  // ---------------------------------------------------------------------
  // The surface.
  // ---------------------------------------------------------------------

  KeyboardPanel {
    id: dropdown
    anchorItem: root.anchorItem
    owner: root.owner
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: dropdown.fittedContentWidth(Style.space(500))
    contentHeight: dropdown.fittedContentHeight(contentColumn.implicitHeight, Style.space(660))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: presetNameEdit.activeFocus || steeringEditor.activeFocus
        || arxivField.activeFocus || titleField.activeFocus
      onCloseRequested: dropdown.close()

      Column {
        id: contentColumn
        width: parent.width
        spacing: Style.spacing.lg

        // --- Not set up: an explanation and nothing else (#15) -----------
        Column {
          width: parent.width
          visible: !root.installed
          spacing: Style.spacing.md

          Text {
            width: parent.width
            wrapMode: Text.Wrap
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            text: "paper-cast needs a one-time Setup: the NotebookLM session and the "
              + "YouTube credential it casts an Episode through."
          }
          Button {
            width: parent.width
            bordered: true
            text: "Run setup"
            onClicked: root.runSetup()
          }
        }

        // --- Set up: the whole surface ------------------------------------
        Column {
          width: parent.width
          visible: root.installed
          spacing: Style.spacing.lg

          // 1. The Steering carousel.
          Rectangle {
            width: parent.width
            height: Style.space(38)
            radius: Style.cornerRadius
            color: Util.alpha(root.accent, 0.08)

            Button {
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(30)
              text: "‹"
              enabled: root.presets.length > 1
              onClicked: root.cyclePreset(-1)
            }

            Row {
              id: carouselActions
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.rightMargin: Style.space(30) + Style.spacing.xs
              spacing: Style.spacing.xs
              PanelActionButton {
                iconText: "＋"
                tooltipText: "Save the loaded Steering as a new preset"
                onClicked: root.addPreset()
              }
              PanelActionButton {
                iconText: "－"
                tooltipText: "Delete the loaded preset"
                enabled: root.presets.length > 1
                hoverColor: root.urgent
                onClicked: root.deletePreset()
              }
            }

            Button {
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(30)
              text: "›"
              enabled: root.presets.length > 1
              onClicked: root.cyclePreset(1)
            }

            Column {
              anchors.centerIn: parent
              spacing: Style.spacing.xxs

              TextInput {
                id: presetNameEdit
                anchors.horizontalCenter: parent.horizontalCenter
                horizontalAlignment: TextInput.AlignHCenter
                selectByMouse: true
                font.family: root.fontFamily
                font.pixelSize: Style.font.subtitle
                color: root.fg
                text: root.presets.length ? root.presets[root.presetIndex].name : ""
                onEditingFinished: root.renamePreset(text)
              }

              Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: Style.spacing.xs
                Repeater {
                  model: root.presets.length
                  Rectangle {
                    width: Style.space(5)
                    height: Style.space(5)
                    radius: width / 2
                    color: index === root.presetIndex ? root.accent : root.faint
                    MouseArea {
                      anchors.fill: parent
                      anchors.margins: -3
                      onClicked: root.selectPreset(index)
                    }
                  }
                }
              }
            }
          }

          // 2. Sources (left) and Steering (right).
          Row {
            id: sourcesSteeringRow
            width: parent.width
            spacing: Style.spacing.lg

            Column {
              id: sourcesColumn
              width: (parent.width - parent.spacing) * 0.42
              spacing: Style.spacing.sm

              Text {
                text: "SOURCES"
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                font.letterSpacing: 1
                color: root.muted
              }

              Button {
                width: parent.width
                bordered: true
                leftAlign: true
                text: "Choose PDFs…"
                onClicked: if (!filePicker.running) filePicker.running = true
              }

              QC.TextField {
                id: arxivField
                width: parent.width
                placeholderText: "paste an arXiv link or id"
                onAccepted: { root.resolveArxiv(text); }
              }

              Text {
                visible: root.arxivError !== ""
                width: parent.width
                wrapMode: Text.Wrap
                color: root.urgent
                font.pixelSize: Style.font.caption
                text: root.arxivError
              }

              Repeater {
                model: root.staged
                Row {
                  width: sourcesColumn.width
                  spacing: Style.spacing.xs
                  Text { color: root.accent; text: "•" }
                  Text {
                    width: sourcesColumn.width - Style.space(24)
                    elide: Text.ElideMiddle
                    color: root.fg
                    font.pixelSize: Style.font.bodySmall
                    text: modelData.title || modelData.path
                  }
                  Item {
                    width: Style.space(12)
                    height: Style.space(12)
                    Text { anchors.centerIn: parent; color: root.muted; text: "✕" }
                    MouseArea {
                      anchors.fill: parent
                      anchors.margins: -4
                      onClicked: root.unstage(index)
                    }
                  }
                }
              }
            }

            Column {
              id: steeringColumn
              width: (parent.width - parent.spacing) * 0.58
              spacing: Style.spacing.sm

              Text {
                width: parent.width
                elide: Text.ElideRight
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                text: "STEERING — edits save to “"
                  + (root.presets.length ? root.presets[root.presetIndex].name : "") + "”"
              }

              Rectangle {
                width: parent.width
                height: Style.space(104)
                radius: Style.cornerRadius
                color: "transparent"
                border.width: 1
                border.color: steeringEditor.activeFocus ? root.accent : root.faint

                QC.TextArea {
                  id: steeringEditor
                  anchors.fill: parent
                  anchors.margins: Style.spacing.sm
                  background: null
                  wrapMode: TextEdit.Wrap
                  selectByMouse: true
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  color: root.fg
                  // No character limit and no counter (#15): the 500 cap on
                  // Google's own Customize box is the textarea's, not a rule
                  // this editor reimplements.
                  onTextChanged: if (root.steering !== text) root.steering = text
                }
              }

              Button {
                width: parent.width
                bordered: true
                enabled: root.staged.length > 0 && !queueAddProc.running
                foreground: enabled ? root.accent : root.muted
                text: root.staged.length > 0 ? "Queue " + root.staged.length : "Queue"
                onClicked: root.queueStaged()
              }

              Text {
                visible: root.queueError !== ""
                width: parent.width
                wrapMode: Text.Wrap
                color: root.urgent
                font.pixelSize: Style.font.caption
                text: root.queueError
              }
            }
          }

          // 3. The combine toggle — only when it means something — and the
          // title it gives a combined Job.
          Toggle {
            width: parent.width
            visible: root.staged.length > 1
            label: "Combine into one Episode"
            checked: root.combine
            onClicked: root.combine = !root.combine
          }

          QC.TextField {
            id: titleField
            width: parent.width
            visible: root.combine && root.staged.length > 1
            placeholderText: root.defaultCombinedTitle
            text: root.combinedTitle
            onTextChanged: root.combinedTitle = text
          }

          PanelSeparator {}

          // 4. The Queue, as a real list.
          Text {
            text: "QUEUE"
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.letterSpacing: 1
            color: root.muted
          }

          Text {
            visible: !root.queue || root.queue.jobs.length === 0
            color: root.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            text: "Nothing queued yet."
          }

          Repeater {
            model: root.queue ? root.queue.jobs : []
            Rectangle {
              width: parent.width
              implicitHeight: rowContent.implicitHeight + Style.spacing.sm * 2
              radius: Style.cornerRadius
              color: rowHover.containsMouse ? Util.alpha(root.fg, 0.06) : "transparent"

              MouseArea { id: rowHover; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }

              Column {
                id: rowContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Style.spacing.sm
                spacing: Style.spacing.xxs

                Row {
                  width: parent.width
                  spacing: Style.spacing.xs

                  Text {
                    color: modelData.stage === "failed" ? root.urgent
                      : (modelData.stage === "done" ? root.muted : root.accent)
                    text: root.stageGlyph(modelData.stage)
                  }

                  Text {
                    width: parent.width - Style.space(150)
                    elide: Text.ElideMiddle
                    color: root.fg
                    font.pixelSize: Style.font.bodySmall
                    text: modelData.title || root.sourceSummary(modelData)
                  }

                  Text {
                    visible: modelData.stage !== "done" && modelData.stage !== "failed"
                    color: root.muted
                    font.pixelSize: Style.font.caption
                    text: modelData.stage + (root.elapsedLabel(modelData) !== ""
                      ? " · " + root.elapsedLabel(modelData) : "")
                  }

                  Button {
                    visible: modelData.stage === "failed"
                    text: "Retry"
                    foreground: root.urgent
                    onClicked: root.retryJob(modelData.id)
                  }

                  Button {
                    visible: modelData.stage === "done" && !!(modelData.record && modelData.record.video_url)
                    text: "Open ↗"
                    foreground: root.accent
                    onClicked: Qt.openUrlExternally(modelData.record.video_url)
                  }
                }

                Text {
                  visible: modelData.stage === "failed" && !!(modelData.record && modelData.record.error)
                  width: parent.width
                  elide: Text.ElideRight
                  color: root.urgent
                  font.pixelSize: Style.font.caption
                  text: (modelData.record && modelData.record.error) || ""
                }
              }
            }
          }
        }
      }
    }
  }

  Process {
    id: filePicker
    command: ["omarchy-file-select", "--title", "Choose papers", "--multiple", "--extensions", "pdf"]
    running: false
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var paths = String(text || "").split("\n").filter(function(p) { return p.length > 0 })
        root.stagePaths(paths)
      }
    }
    // Exit 1 is the user cancelling — a decision, not a fault; only 2 is a
    // fault (omarchy-helper-commands.md).
    onExited: function(code) { if (code === 2) console.warn("paper-cast: file chooser failed") }
  }
}
