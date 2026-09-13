import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "Stub.js" as Stub

// PROTOTYPE — THROWAWAY. Not production code.
//
// Three variants of the paper-cast dropdown panel, on the real bar dropdown
// surface, switchable from the footer switcher or the ← / → keys.
//
// Nothing behind it: the queue is stubbed in Stub.js, "Choose PDFs…" stages a
// fake filename, and "Add to queue" mutates an in-memory array. No disk, no
// NotebookLM, no upload.
Panel {
  id: root

  moduleName: "local.papercast-proto"
  ipcTarget: "papercast-proto"

  // --- theme ---------------------------------------------------------------
  readonly property color fg: Color.popups.text
  readonly property color muted: Util.alpha(fg, 0.55)
  readonly property color faint: Util.alpha(fg, 0.18)
  readonly property color accent: Color.accent
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // --- prototype state (in memory only) ------------------------------------
  property var queue: Stub.initialQueue()
  property var staged: []
  property var presets: Stub.PRESETS.slice()
  property int presetIndex: 0
  property string steering: Stub.PRESETS[0].text

  readonly property string presetName: presets[presetIndex] ? presets[presetIndex].name : ""

  // Edits are live: typing in the Steering box rewrites the loaded preset.
  // Reassign rather than mutate so the carousel's bindings see it.
  function mutatePreset(i, patch) {
    var next = presets.slice()
    next[i] = Object.assign({}, next[i], patch)
    root.presets = next
  }

  onSteeringChanged: {
    if (presets[presetIndex] && presets[presetIndex].text !== steering)
      mutatePreset(presetIndex, { text: steering })
  }

  function cyclePreset(delta) {
    var n = presets.length
    root.presetIndex = ((presetIndex + delta) % n + n) % n
    root.steering = presets[presetIndex].text
  }

  function usePreset(i) {
    root.presetIndex = i
    root.steering = presets[i].text
  }

  // Add clones what is loaded, so a good steering you just wrote becomes a
  // preset without retyping it. Rename it in place.
  function addPreset() {
    var next = presets.slice()
    next.splice(presetIndex + 1, 0, { name: "New preset", text: root.steering })
    root.presets = next
    root.presetIndex = presetIndex + 1
  }

  // Never delete the last one — an empty carousel has no way back.
  function deletePreset() {
    if (presets.length <= 1) return
    var next = presets.slice()
    next.splice(presetIndex, 1)
    root.presets = next
    root.presetIndex = Math.min(presetIndex, next.length - 1)
    root.steering = next[root.presetIndex].text
  }
  property bool combine: false
  property int nextSample: 0
  property bool editing: false

  readonly property int runningCount: {
    var n = 0
    for (var i = 0; i < queue.length; i++)
      if (queue[i].stage !== "done" && queue[i].stage !== "failed") n++
    return n
  }
  readonly property bool anyFailed: {
    for (var i = 0; i < queue.length; i++) if (queue[i].stage === "failed") return true
    return false
  }
  readonly property var headJob: {
    for (var i = 0; i < queue.length; i++) {
      var s = queue[i].stage
      if (s === "generating" || s === "muxing" || s === "uploading") return queue[i]
    }
    return null
  }

  function stageSample() {
    var next = staged.slice()
    next.push(Stub.SAMPLE_PDFS[root.nextSample % Stub.SAMPLE_PDFS.length])
    root.nextSample += 1
    root.staged = next
  }

  function stageArxiv(text) {
    var t = String(text || "").trim()
    if (!t) return
    var next = staged.slice()
    next.push("arXiv:" + t.replace(/^https?:\/\/(www\.)?arxiv\.org\/(abs|pdf)\//, "").replace(/\.pdf$/, ""))
    root.staged = next
  }

  function unstage(index) {
    var next = staged.slice()
    next.splice(index, 1)
    root.staged = next
  }

  function addToQueue() {
    if (staged.length === 0) return
    var next = queue.slice()
    if (combine && staged.length > 1) {
      next.push({ id: "n" + Date.now(), title: staged[0] + " + " + (staged.length - 1) + " more",
                  stage: "queued", pct: 0, sources: staged.length, elapsed: "", link: "" })
    } else {
      for (var i = 0; i < staged.length; i++)
        next.push({ id: "n" + Date.now() + "-" + i, title: staged[i],
                    stage: "queued", pct: 0, sources: 1, elapsed: "", link: "" })
    }
    root.queue = next
    root.staged = []
    root.combine = false
  }

  function retry(id) {
    var next = queue.slice()
    for (var i = 0; i < next.length; i++)
      if (next[i].id === id) next[i] = Object.assign({}, next[i], { stage: "queued", error: undefined })
    root.queue = next
  }

  function dismiss(id) {
    var next = []
    for (var i = 0; i < queue.length; i++) if (queue[i].id !== id) next.push(queue[i])
    root.queue = next
  }

  // --- variant switcher ----------------------------------------------------
  readonly property var variants: [
    { key: "A", name: "Compose-first",  file: "VariantA.qml" },
    { key: "B", name: "One surface",    file: "VariantB.qml" },
    { key: "C", name: "Tabbed",         file: "VariantC.qml" }
  ]
  property int variantIndex: 0
  readonly property var variant: variants[variantIndex]

  function cycle(delta) {
    var n = variants.length
    root.variantIndex = ((variantIndex + delta) % n + n) % n
  }

  // --- bar button ----------------------------------------------------------
  visible: true
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // Glyph only — no text on the bar, ever. That leaves colour as the entire
  // state channel: normal when idle, accent (breathing) while a Job runs,
  // urgent when one has failed and not yet been dealt with.
  readonly property color barColor: {
    if (anyFailed) return urgent
    if (runningCount > 0) return accent
    return bar ? bar.barForeground : Color.foreground
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "\uF00BD"
    foreground: root.barColor
    tooltipText: "paper-cast (PROTOTYPE) — variant " + root.variant.key + ": " + root.variant.name
    active: root.opened

    // The only motion the bar is allowed: a slow breath while work is running.
    SequentialAnimation on opacity {
      running: root.runningCount > 0 && !root.anyFailed
      loops: Animation.Infinite
      alwaysRunToEnd: true
      NumberAnimation { to: 0.45; duration: 1100; easing.type: Easing.InOutSine }
      NumberAnimation { to: 1.0;  duration: 1100; easing.type: Easing.InOutSine }
    }
    onOpacityChanged: if (root.runningCount === 0 || root.anyFailed) opacity = 1

    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.cycle(1)
      else root.toggle()
    }
  }

  onOpenedChanged: if (opened) Qt.callLater(function() { keyCatcher.forceActiveFocus() })

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(loader.item ? loader.item.prefWidth : 420))
    contentHeight: panel.fittedContentHeight(outer.implicitHeight, Style.space(660))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.editing
      onCloseRequested: root.close()
      onMoveRequested: function(dx, dy) { if (dx !== 0) root.cycle(dx) }

      Column {
        id: outer
        width: parent.width
        spacing: Style.spacing.md

        Loader {
          id: loader
          width: parent.width
          source: Qt.resolvedUrl(root.variant.file)
          onLoaded: if (item) item.host = root
        }

        // --- the switcher: deliberately ugly so it reads as scaffolding ----
        Rectangle {
          width: parent.width
          height: Style.space(30)
          radius: Style.cornerRadius
          color: Util.alpha(root.accent, 0.14)
          border.color: Util.alpha(root.accent, 0.45)
          border.width: 1

          Row {
            anchors.centerIn: parent
            spacing: Style.spacing.md

            Button {
              text: "‹"
              foreground: root.fg
              width: Style.space(26)
              onClicked: root.cycle(-1)
            }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: "PROTOTYPE  ·  " + root.variant.key + " — " + root.variant.name
                    + "  ·  " + (root.variantIndex + 1) + "/" + root.variants.length
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
            Button {
              text: "›"
              foreground: root.fg
              width: Style.space(26)
              onClicked: root.cycle(1)
            }
          }
        }

        Text {
          width: parent.width
          text: "← / → switch variant · middle-click the bar icon also switches · Esc closes"
          color: root.muted
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          horizontalAlignment: Text.AlignHCenter
        }
      }
    }
  }
}
