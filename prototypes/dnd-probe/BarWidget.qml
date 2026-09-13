import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Empirical probe for whether an external drag (e.g. a file dragged out of
// Nautilus) can be dropped directly onto a bar widget's icon.
//
// It does three things whenever the drag crosses onEntered/onExited/onDropped:
//   1. Appends a line to ~/dndtest.log (so you can `tail -f` it).
//   2. Fires `notify-send` so you see it even if you're not watching a
//      terminal.
//   3. Recolors the icon while a drag is hovering it, as a visual sanity
//      check independent of the log/notification path.
//
// Left-click opens the log file's folder; middle-click clears the counter.
BarWidget {
  id: root
  moduleName: "local.dndtest"

  property int dropCount: 0
  property int dragCount: 0
  property bool hoveredByDrag: false
  readonly property string logPath: Quickshell.env("HOME") + "/dndtest.log"

  function timestamp() {
    return new Date().toISOString()
  }

  // Keep this simple and shell-safe: single quotes in dragged paths would
  // break the naive quoting below, which is acceptable for a throwaway probe
  // (test with a normally-named file, e.g. a PDF from your Downloads folder).
  function shellQuote(s) {
    return "'" + String(s).replace(/'/g, "'\\''") + "'"
  }

  function appendLog(line) {
    var full = timestamp() + "  " + line
    console.log("[dnd-test] " + full)
    Quickshell.execDetached(["bash", "-lc", "echo " + shellQuote(full) + " >> " + shellQuote(root.logPath)])
  }

  // Toasts get one line, and a full path eats it. Names only.
  function basenames(urls) {
    var out = []
    for (var i = 0; i < urls.length; i++) {
      var u = String(urls[i])
      out.push(decodeURIComponent(u.substring(u.lastIndexOf("/") + 1)))
    }
    return out.join("\n")
  }

  function notify(summary, body) {
    Quickshell.execDetached(["notify-send", "--app-name=DnD Probe", summary, body || ""])
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: (root.vertical ? "" : "DnD ")
          + (root.hoveredByDrag ? "◎" : "◯")
          + (root.hoveredByDrag ? " +" + root.dragCount : (root.dropCount > 0 ? " " + root.dropCount : ""))
    tooltipText: "DnD probe — drops so far: " + root.dropCount + "\nLog: " + root.logPath
    hasVisualContent: true

    onPressed: function(b) {
      if (b === Qt.MiddleButton) {
        root.dropCount = 0
        root.appendLog("counter reset")
      } else if (b === Qt.RightButton) {
        Quickshell.execDetached(["xdg-open", Quickshell.env("HOME")])
      } else {
        root.notify("DnD Probe", "drops so far: " + root.dropCount)
      }
    }
  }

  // The actual point under test: can this DropArea, living inside a bar
  // widget's root Item on a zwlr_layer_surface_v1-backed PanelWindow, receive
  // wl_data_device enter/motion/drop for a drag that started in an entirely
  // different, unrelated top-level application (Nautilus)?
  DropArea {
    id: dropArea
    anchors.fill: parent

    onEntered: function(drag) {
      root.hoveredByDrag = true
      var list = drag.hasUrls ? drag.urls : []
      root.dragCount = list.length
      var urls = list.length > 0 ? list.join(", ") : "(no urls)"
      root.appendLog("ENTERED count=" + list.length + " hasUrls=" + drag.hasUrls + " hasText=" + drag.hasText + " formats=" + JSON.stringify(drag.formats) + " urls=" + urls)
      root.notify("Hovering " + list.length + " file" + (list.length === 1 ? "" : "s"), root.basenames(list))
      // Accept unconditionally so onPositionChanged/onDropped keep firing;
      // this is a probe, not a real widget, so we don't filter by mime type.
      drag.accepted = true
    }

    onExited: {
      root.hoveredByDrag = false
      root.appendLog("EXITED")
    }

    onDropped: function(drop) {
      root.hoveredByDrag = false
      var list = drop.hasUrls ? drop.urls : []
      var n = list.length
      root.dropCount += n
      var urls = n > 0 ? list.join(", ") : "(no urls)"
      root.appendLog("DROPPED count=" + n + " hasUrls=" + drop.hasUrls + " hasText=" + drop.hasText + " urls=" + urls)
      // The whole point of this revision: say HOW MANY, and name them by
      // basename so two files are visibly two and not one wall of path.
      root.notify("Dropped " + n + " file" + (n === 1 ? "" : "s"), root.basenames(list))
      drop.accept(Qt.CopyAction)
    }
  }

  Rectangle {
    anchors.fill: parent
    visible: root.hoveredByDrag
    color: "transparent"
    border.color: "lime"
    border.width: 2
    radius: 4
  }
}
