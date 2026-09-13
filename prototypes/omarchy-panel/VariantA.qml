import QtQuick
import QtQuick.Controls as QC
import qs.Commons
import qs.Ui
import "Stub.js" as Stub

// VARIANT A — "Compose-first".
// The Steering box is the hero: biggest element, top of the panel, focused on
// open. Sources are a supporting row underneath. The queue is demoted to a
// single status line at the bottom — you came here to start something, not to
// watch it.
Item {
  id: v
  property var host: null
  readonly property int prefWidth: 400
  implicitHeight: col.implicitHeight

  Column {
    id: col
    width: parent.width
    spacing: Style.spacing.lg

    Row {
      width: parent.width
      Text {
        text: "New episode"
        color: host ? host.fg : Color.popups.text
        font.family: host ? host.fontFamily : Style.font.family
        font.pixelSize: Style.font.title
        width: parent.width - queuePill.width
      }
      Text {
        id: queuePill
        text: host && host.runningCount > 0 ? host.runningCount + " in queue" : "idle"
        color: host ? host.muted : Color.popups.text
        font.family: host ? host.fontFamily : Style.font.family
        font.pixelSize: Style.font.caption
        anchors.verticalCenter: parent.verticalCenter
      }
    }

    // --- the hero -----------------------------------------------------------
    Column {
      width: parent.width
      spacing: Style.spacing.labelGap

      Text {
        text: "What should the hosts focus on?"
        color: host ? host.muted : Color.popups.text
        font.family: host ? host.fontFamily : Style.font.family
        font.pixelSize: Style.font.caption
      }

      Rectangle {
        width: parent.width
        height: Style.space(132)
        radius: Style.cornerRadius
        color: "transparent"
        border.width: 1
        border.color: steeringArea.activeFocus
                      ? (host ? host.accent : Color.accent)
                      : (host ? host.faint : Color.popups.border)

        QC.TextArea {
          id: steeringArea
          anchors.fill: parent
          anchors.margins: Style.spacing.controlPaddingX
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

      Text {
        width: parent.width
        text: "applies to this episode · your default is reused next time"
        color: host ? host.muted : Color.popups.text
        font.family: host ? host.fontFamily : Style.font.family
        font.pixelSize: Style.font.caption
      }
    }

    // --- sources ------------------------------------------------------------
    Row {
      width: parent.width
      spacing: Style.spacing.controlGap

      Button {
        text: "Choose PDFs…"
        bordered: true
        foreground: host ? host.fg : Color.popups.text
        onClicked: if (host) host.stageSample()
      }

      TextField {
        id: arxivField
        width: parent.width - Style.space(120) - Style.spacing.controlGap
        placeholderText: "arXiv id or link"
        foreground: host ? host.fg : Color.popups.text
        onActiveFocusChanged: if (host) host.editing = activeFocus
        onAccepted: { if (host) host.stageArxiv(text); text = "" }
      }
    }

    Flow {
      width: parent.width
      spacing: Style.spacing.sm
      visible: host && host.staged.length > 0

      Repeater {
        model: host ? host.staged : []
        Rectangle {
          radius: Style.cornerRadius
          color: Util.alpha(host ? host.accent : Color.accent, 0.16)
          implicitWidth: chipRow.implicitWidth + Style.spacing.controlPaddingX * 2
          implicitHeight: Style.space(22)
          Row {
            id: chipRow
            anchors.centerIn: parent
            spacing: Style.spacing.sm
            Text {
              text: modelData
              color: host ? host.fg : Color.popups.text
              font.family: host ? host.fontFamily : Style.font.family
              font.pixelSize: Style.font.caption
              anchors.verticalCenter: parent.verticalCenter
            }
            Text {
              text: "✕"
              color: host ? host.muted : Color.popups.text
              font.pixelSize: Style.font.caption
              anchors.verticalCenter: parent.verticalCenter
              MouseArea {
                anchors.fill: parent
                anchors.margins: -4
                cursorShape: Qt.PointingHandCursor
                onClicked: if (host) host.unstage(index)
              }
            }
          }
        }
      }
    }

    Toggle {
      width: parent.width
      visible: host && host.staged.length > 1
      label: "One episode for all " + (host ? host.staged.length : 0)
      description: "Otherwise each paper becomes its own episode"
      checked: host ? host.combine : false
      foreground: host ? host.fg : Color.popups.text
      onClicked: if (host) host.combine = !host.combine
    }

    Button {
      width: parent.width
      text: host && host.staged.length > 0
            ? "Add " + host.staged.length + " to queue"
            : "Add to queue"
      bordered: true
      enabled: host && host.staged.length > 0
      foreground: host ? (host.staged.length > 0 ? host.accent : host.muted) : Color.popups.text
      onClicked: if (host) host.addToQueue()
    }

    PanelSeparator { width: parent.width }

    // --- the queue, demoted to one line ------------------------------------
    Text {
      width: parent.width
      elide: Text.ElideRight
      color: host && host.anyFailed ? host.urgent : (host ? host.muted : Color.popups.text)
      font.family: host ? host.fontFamily : Style.font.family
      font.pixelSize: Style.font.caption
      text: {
        if (!host) return ""
        if (host.anyFailed) return "✕ a job failed — open the queue to retry"
        if (host.headJob) return Stub.stageGlyph(host.headJob.stage) + "  "
                                 + Stub.stageLabel(host.headJob.stage) + " · " + host.headJob.title
                                 + " · " + host.headJob.elapsed
        return "nothing running"
      }
    }
  }
}
