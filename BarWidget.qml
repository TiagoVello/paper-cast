import QtQuick
import Quickshell.Io
// By URI, never by relative path: `Color` and `Style` are declared `singleton`
// in the shell's qmldir, and a relative import would hand this plugin a second,
// empty copy of each and no theme at all.
import qs.Commons
import qs.Ui

// The paper-cast bar icon.
//
// A glyph and nothing else — no label, ever (#10). Colour carries the whole
// state: normal when idle, accent while the Queue has work, urgent when a Job
// has failed, and faded when paper-cast is not set up yet. The only motion
// allowed is a slow breath while work is running.
//
// `BarWidget` is the root — the same base every first-party widget-with-a-
// dropdown uses (see plugins/panels/weather/BarWidget.qml,
// plugins/panels/clock/BarWidget.qml): a plain Item that lays out inline in
// the bar's row. `qs.Ui/Panel` (tried first) is *not* a layer-shell surface
// either — it is Item-based too, its `PanelController` a bare QtObject — but
// it carries an unused IpcHandler/switchPanel surface meant for a widget that
// *is* the popout, which this one is not (#15's Panel.qml is). Owning a
// PanelController directly here, the same way `qs.Ui/Panel` does internally,
// keeps the open()/close()/opened contract `Bar.findPanelWidget()` requires
// for `omarchy-shell shell toggle io.github.tiagovello.paper-cast` without
// inheriting machinery this widget never uses.
BarWidget {
  id: root

  // No `ipcTarget`: BarWidget carries none, and none is wanted. One bar
  // surface exists per monitor, so every instance of this widget would race
  // to register the same IPC handler; the bar-widget route above already
  // carries summon/hide/toggle and picks the instance on the focused output
  // for us.

  readonly property bool opened: panelController.open
  property bool popoutSwitchClosing: false

  function open() { panelController.show() }
  function close() { panelController.hide() }
  function toggle() { panelController.toggle() }
  // Mirrors qs.Ui/Panel's own implementation: Bar.requestPopout prefers this
  // over close() when handing the popout to a different panel, so the
  // dropdown's KeyboardPanel (which reads popoutSwitchClosing off `owner`)
  // can skip its normal fade during the handoff.
  function closeForPopoutSwitch() {
    popoutSwitchClosing = true
    close()
    Qt.callLater(function() { popoutSwitchClosing = false })
  }

  PanelController { id: panelController }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // ---------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------

  // Setup (#10) is a command the user runs; the plugin system has no install
  // hook. "Not set up" is exactly "no paper-cast on PATH".
  property bool installed: true

  // Failure is a notice, not a condition: it stands until it has been seen.
  // Ids already seen, so a *second* Job failing lights the icon again.
  property var acknowledgedFailures: []

  readonly property bool failureVisible: {
    var failed = queue.failedIds
    for (var i = 0; i < failed.length; i++)
      if (root.acknowledgedFailures.indexOf(failed[i]) === -1) return true
    return false
  }

  readonly property bool breathing: queue.activeCount > 0 && !root.failureVisible

  readonly property color idleColor: bar ? bar.barForeground : Color.foreground

  readonly property color barColor: {
    if (root.failureVisible) return bar ? bar.urgent : Color.urgent
    if (queue.activeCount > 0) return Color.accent
    // Not set up is idle, held back — the same state channel, turned down,
    // rather than a fourth colour competing with failure for attention.
    return root.installed ? root.idleColor : Util.alpha(root.idleColor, 0.45)
  }

  function acknowledgeFailures() {
    root.acknowledgedFailures = queue.failedIds.slice()
  }

  // A failure clears when the panel is opened by any means — a click, the
  // shell's toggle, or #16's drop, all of which land here.
  onOpenedChanged: {
    if (!root.opened) return
    // One bar surface per monitor: clear it on every instance, or the other
    // screen keeps shouting about a failure the user has already looked at.
    var peers = root.bar && typeof root.bar.moduleWidgets === "function"
      ? root.bar.moduleWidgets(root.moduleName) : [root]
    for (var i = 0; i < peers.length; i++)
      if (peers[i] && typeof peers[i].acknowledgeFailures === "function") peers[i].acknowledgeFailures()
    // Setup may have been run since the last look; re-probe so the icon comes
    // back up to full strength without waiting for a hot reload.
    setupProbe.running = true
  }

  QueueState {
    id: queue
    polled: root.opened
  }

  Process {
    id: setupProbe
    // `bash -lc`: a Process does not inherit a login shell's PATH, and Setup
    // writes the wrapper to ~/.local/bin, which is on PATH only via the
    // login profile.
    command: ["bash", "-lc", "command -v paper-cast >/dev/null && echo yes || echo no"]
    running: true
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.installed = text.trim() === "yes"
    }
  }

  // ---------------------------------------------------------------------
  // The icon
  // ---------------------------------------------------------------------

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar

    // md-file_music, U+F0223. A page with a note on it: the paper going in
    // and the Episode coming out, in one mark. It also reads as somewhere
    // files belong, which is what #16 needs it to say.
    text: "󰈣"

    foreground: root.barColor
    // The bar tints `active` widgets with its urgent colour, which would take
    // the state channel away from us; the bar draws its own indicator under an
    // open panel anyway, so openness is shown there and colour stays state.
    useActiveColor: false

    // A fixed slot, never a shrinking one: this 27 px square is the drop
    // target in #16, and #10's open question is whether a target this size is
    // reliable in daily use. Do not make it smaller.
    slotSize: Style.bar.iconSlot

    // Not on the bar — the hover surface, which is the only place this widget
    // is allowed to use words.
    tooltipText: {
      if (!root.installed) return "paper-cast — run Setup"
      if (root.failureVisible) return "paper-cast — a Job failed"
      if (queue.activeCount === 1) return "paper-cast — 1 Job in the Queue"
      if (queue.activeCount > 1) return "paper-cast — " + queue.activeCount + " Jobs in the Queue"
      return "paper-cast"
    }

    onPressed: function(mouseButton) {
      // Right and middle are unclaimed; #15 decides whether the panel wants
      // them once there is a panel to want them.
      if (mouseButton === Qt.LeftButton) root.toggle()
    }

    // The only motion on the bar. `alwaysRunToEnd` stops the glyph being cut
    // off mid-fade when the Queue drains, and leaves opacity wherever the last
    // frame landed — hence the snap back to 1 when the breath is not running.
    SequentialAnimation on opacity {
      running: root.breathing
      loops: Animation.Infinite
      alwaysRunToEnd: true
      NumberAnimation { to: 0.45; duration: 1100; easing.type: Easing.InOutSine }
      NumberAnimation { to: 1.0; duration: 1100; easing.type: Easing.InOutSine }
    }
    onOpacityChanged: if (!root.breathing) opacity = 1
  }

  // ---------------------------------------------------------------------
  // The drop target (#16)
  // ---------------------------------------------------------------------

  // Whether a drag is currently hovering the icon — the only state the drop
  // target itself owns; what counts as a valid drop lives in Panel.qml's
  // stagePaths(), one function shared with the "Choose PDFs…" picker.
  property bool hoveredByDrag: false

  // A sibling of `button`, filling the same root Item: Qt delivers drops
  // through ItemAcceptsDrops, a path separate from mouse hit-testing, so
  // button's own MouseArea (which sits on top for clicks) never swallows one
  // (variant-b-and-dnd.md).
  DropArea {
    id: dropArea

    // The reserved icon slot (Style.bar.iconSlot, 27px) is already this
    // widget's whole footprint, but a ~27px glyph is a precision target in
    // daily use even though it lands fine in a test (#10's open "Fog").
    // Grow the *drop* target past the *click* target: Style.spacing.lg (8px)
    // of extra reach on each side, horizontally only. There's no vertical
    // room to spare — the bar's own height is fixed and this Item already
    // fills it — but the gap between bar modules leaves room to spare
    // sideways, so a few extra px of reach here doesn't cost anything.
    anchors.fill: parent
    anchors.leftMargin: -Style.spacing.lg
    anchors.rightMargin: -Style.spacing.lg

    onEntered: function(drag) {
      root.hoveredByDrag = true
      // Accept broadly here and filter in onDropped, once we actually know
      // what was offered: declining in onEntered stops the compositor
      // sending onPositionChanged/onDropped at all (variant-b-and-dnd.md).
      drag.accepted = true
    }

    onExited: { root.hoveredByDrag = false }

    onDropped: function(drop) {
      root.hoveredByDrag = false
      var urls = drop.hasUrls ? drop.urls : []
      // Finishes the wl_data_device handshake regardless of what we do with
      // the paths next — refusing to *stage* a non-PDF is a decision made
      // one layer up, not a reason to leave the drag hanging.
      drop.accept(Qt.CopyAction)
      if (urls.length > 0) root.stageDroppedPaths(urls)
    }
  }

  // The only feedback a drag-hover gets, within the no-text rule (#10):
  // colour and the bar's existing motion vocabulary — a fade, the same way
  // the breath above is the icon's only allowed motion — never a hard cut.
  Rectangle {
    anchors.fill: parent
    color: "transparent"
    radius: Style.cornerRadius
    border.width: 2
    border.color: Color.accent
    opacity: root.hoveredByDrag ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 120 } }
  }

  // The named seam #16 calls: stage a list of paths (plain paths or `file://`
  // URLs — Panel.qml.stagePaths() accepts either) and open the panel to them,
  // without queuing anything.
  function stageDroppedPaths(paths) {
    if (panelLoader.item && typeof panelLoader.item.stagePaths === "function")
      panelLoader.item.stagePaths(paths)
    root.open()
  }

  // ---------------------------------------------------------------------
  // The panel is #15's
  // ---------------------------------------------------------------------

  // #15's dropdown. The loaded item is handed what it needs the way the bar
  // hands this widget what it needs — by name, after load, guarded — because
  // injected properties arrive after Component.onCompleted and the bar
  // injects twice.
  readonly property url panelSource: Qt.resolvedUrl("Panel.qml")

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("owner" in target) target.owner = root
    if ("anchorItem" in target) target.anchorItem = button
    if ("queue" in target) target.queue = queue
    if ("installed" in target) target.installed = root.installed
  }

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()
  onInstalledChanged: injectPanel()

  Loader {
    id: panelLoader
    active: String(root.panelSource) !== ""
    source: root.panelSource
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }
}
