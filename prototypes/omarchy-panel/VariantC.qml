import QtQuick
import QtQuick.Controls as QC
import qs.Commons
import qs.Ui
import "Stub.js" as Stub

// VARIANT C — "Tabbed".
// Two modes, one at a time, so neither is cramped: New Episode gets a roomy
// pick-or-paste target and a full-height Steering box; Queue gets the whole
// panel for a list with real actions. Fixed height, so the panel never jumps.
Item {
  id: v
  property var host: null
  readonly property int prefWidth: 440
  property int tab: 0
  implicitHeight: Style.space(430)

  Column {
    id: col
    anchors.fill: parent
    spacing: Style.spacing.md

    // --- tabs ---------------------------------------------------------------
    Row {
      width: parent.width
      spacing: 0

      Repeater {
        model: [
          { label: "New episode", i: 0 },
          { label: host && host.queue.length > 0 ? "Queue (" + host.queue.length + ")" : "Queue", i: 1 }
        ]

        Rectangle {
          width: parent.width / 2
          height: Style.space(30)
          color: "transparent"

          Text {
            anchors.centerIn: parent
            text: modelData.label
            color: v.tab === modelData.i ? (host ? host.fg : Color.popups.text)
                                         : (host ? host.muted : Color.popups.text)
            font.family: host ? host.fontFamily : Style.font.family
            font.pixelSize: Style.font.bodySmall
          }

          Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: v.tab === modelData.i ? 2 : 1
            color: v.tab === modelData.i ? (host ? host.accent : Color.accent)
                                         : (host ? host.faint : Color.popups.border)
          }

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: v.tab = modelData.i
          }
        }
      }
    }

    // --- tab 0: new episode -------------------------------------------------
    Column {
      width: parent.width
      spacing: Style.spacing.md
      visible: v.tab === 0

      // pick-or-paste target, sized like a place you put things
      Rectangle {
        width: parent.width
        height: Style.space(92)
        radius: Style.cornerRadius
        color: pickHover.containsMouse ? Util.alpha(host ? host.accent : Color.accent, 0.08) : "transparent"
        border.width: 1
        border.color: Util.alpha(host ? host.accent : Color.accent, pickHover.containsMouse ? 0.7 : 0.35)

        MouseArea {
          id: pickHover
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onClicked: if (host) host.stageSample()
        }

        Column {
          anchors.centerIn: parent
          spacing: Style.spacing.xs
          Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Choose PDFs…"
            color: host ? host.fg : Color.popups.text
            font.family: host ? host.fontFamily : Style.font.family
            font.pixelSize: Style.font.subtitle
          }
          Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: host && host.staged.length > 0
                  ? host.staged.length + " staged · " + host.staged.join(", ")
                  : "or paste an arXiv link below"
            width: Math.min(implicitWidth, v.width - Style.space(40))
            elide: Text.ElideMiddle
            color: host ? host.muted : Color.popups.text
            font.family: host ? host.fontFamily : Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
      }

      TextField {
        width: parent.width
        placeholderText: "arxiv.org/abs/2401.12345"
        foreground: host ? host.fg : Color.popups.text
        onActiveFocusChanged: if (host) host.editing = activeFocus
        onAccepted: { if (host) host.stageArxiv(text); text = "" }
      }

      Rectangle {
        width: parent.width
        height: Style.space(120)
        radius: Style.cornerRadius
        color: "transparent"
        border.width: 1
        border.color: areaC.activeFocus ? (host ? host.accent : Color.accent)
                                        : (host ? host.faint : Color.popups.border)

        QC.TextArea {
          id: areaC
          anchors.fill: parent
          anchors.margins: Style.spacing.sm
          text: host ? host.steering : ""
          onTextChanged: if (host) host.steering = text
          onActiveFocusChanged: if (host) host.editing = activeFocus
          wrapMode: TextEdit.Wrap
          selectByMouse: true
          background: null
          color: host ? host.fg : Color.popups.text
          font.family: host ? host.fontFamily : Style.font.family
          font.pixelSize: Style.font.bodySmall
        }
      }

      Toggle {
        width: parent.width
        visible: host && host.staged.length > 1
        label: "One episode for all " + (host ? host.staged.length : 0)
        checked: host ? host.combine : false
        foreground: host ? host.fg : Color.popups.text
        onClicked: if (host) host.combine = !host.combine
      }

      Button {
        width: parent.width
        text: "Add to queue"
        bordered: true
        enabled: host && host.staged.length > 0
        foreground: host ? (host.staged.length > 0 ? host.accent : host.muted) : Color.popups.text
        onClicked: { if (host) host.addToQueue(); v.tab = 1 }
      }
    }

    // --- tab 1: queue -------------------------------------------------------
    QC.ScrollView {
      width: parent.width
      height: v.height - Style.space(46)
      visible: v.tab === 1
      clip: true

      Column {
        width: v.width
        spacing: Style.spacing.sm

        Repeater {
          model: host ? host.queue : []

          Rectangle {
            width: parent.width
            implicitHeight: jobCol.implicitHeight + Style.spacing.md * 2
            radius: Style.cornerRadius
            color: Util.alpha(host ? host.fg : Color.popups.text, 0.05)

            Column {
              id: jobCol
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.spacing.md
              spacing: Style.spacing.xs

              Text {
                width: parent.width
                text: modelData.title
                elide: Text.ElideRight
                color: host ? host.fg : Color.popups.text
                font.family: host ? host.fontFamily : Style.font.family
                font.pixelSize: Style.font.bodySmall
              }

              Row {
                width: parent.width
                spacing: Style.spacing.sm

                Text {
                  text: Stub.stageGlyph(modelData.stage) + " " + Stub.stageLabel(modelData.stage)
                        + (modelData.elapsed ? " · " + modelData.elapsed : "")
                        + (modelData.sources > 1 ? " · " + modelData.sources + " sources" : "")
                  color: modelData.stage === "failed" ? (host ? host.urgent : Color.urgent)
                                                      : (host ? host.muted : Color.popups.text)
                  font.family: host ? host.fontFamily : Style.font.family
                  font.pixelSize: Style.font.caption
                  anchors.verticalCenter: parent.verticalCenter
                }

                Item { width: Style.space(4); height: 1 }

                Button {
                  visible: modelData.stage === "failed"
                  text: "Retry"
                  fontSize: Style.font.caption
                  foreground: host ? host.urgent : Color.urgent
                  onClicked: if (host) host.retry(modelData.id)
                }
                Button {
                  visible: modelData.stage === "failed" || modelData.stage === "done"
                  text: "Dismiss"
                  fontSize: Style.font.caption
                  foreground: host ? host.muted : Color.popups.text
                  onClicked: if (host) host.dismiss(modelData.id)
                }
                Button {
                  visible: modelData.stage === "done"
                  text: "Open ↗"
                  fontSize: Style.font.caption
                  foreground: host ? host.accent : Color.accent
                  onClicked: {}
                }
              }

              Text {
                visible: modelData.stage === "failed"
                width: parent.width
                text: modelData.error || ""
                wrapMode: Text.Wrap
                color: host ? host.urgent : Color.urgent
                font.family: host ? host.fontFamily : Style.font.family
                font.pixelSize: Style.font.caption
              }

              Rectangle {
                visible: modelData.stage === "generating" || modelData.stage === "muxing" || modelData.stage === "uploading"
                width: parent.width
                height: 2
                color: Util.alpha(host ? host.fg : Color.popups.text, 0.12)
                Rectangle {
                  width: parent.width * (modelData.pct || 0)
                  height: parent.height
                  color: host ? host.accent : Color.accent
                }
              }
            }
          }
        }
      }
    }
  }
}
