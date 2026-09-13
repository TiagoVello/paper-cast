import QtQuick
import QtQuick.Controls as QC
import qs.Commons
import qs.Ui
import "Stub.js" as Stub

// VARIANT B — "One surface".
// No modes, no demotion: composing and watching share the panel top-to-bottom,
// and the queue is a real list you can act on, not a status line. Wider, so the
// two halves can sit side by side in the head: sources left, steering right.
//
// Topped by the Steering carousel: pre-written --focus texts you flip through
// rather than retype. Editing the loaded text detaches it to "Custom" instead
// of quietly overwriting the preset.
Item {
  id: v
  property var host: null
  readonly property int prefWidth: 500
  implicitHeight: col.implicitHeight

  Column {
    id: col
    width: parent.width
    spacing: Style.spacing.md

    // --- compose head: two columns -----------------------------------------
    // --- Steering carousel --------------------------------------------------
    Rectangle {
      width: parent.width
      height: Style.space(38)
      radius: Style.cornerRadius
      color: Util.alpha(host ? host.accent : Color.accent, 0.08)

      Button {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: "‹"
        width: Style.space(30)
        foreground: host ? host.fg : Color.popups.text
        onClicked: if (host) host.cyclePreset(-1)
      }

      Column {
        anchors.centerIn: parent
        spacing: 3

        // Click to rename in place — "add" clones the loaded preset as
        // "New preset", and this is where you fix that name.
        TextInput {
          id: nameField
          anchors.horizontalCenter: parent.horizontalCenter
          text: host ? host.presetName : ""
          color: host ? host.fg : Color.popups.text
          font.family: host ? host.fontFamily : Style.font.family
          font.pixelSize: Style.font.subtitle
          horizontalAlignment: TextInput.AlignHCenter
          selectByMouse: true
          onActiveFocusChanged: if (host) host.editing = activeFocus
          onTextChanged: {
            if (host && activeFocus && text !== host.presetName)
              host.mutatePreset(host.presetIndex, { name: text })
          }
          onAccepted: focus = false
        }

        Row {
          anchors.horizontalCenter: parent.horizontalCenter
          spacing: Style.spacing.xs

          Repeater {
            model: host ? host.presets : []
            Rectangle {
              width: Style.space(5)
              height: Style.space(5)
              radius: width / 2
              color: (host && index === host.presetIndex)
                     ? (host ? host.accent : Color.accent)
                     : (host ? host.faint : Color.popups.border)
              MouseArea {
                anchors.fill: parent
                anchors.margins: -3
                cursorShape: Qt.PointingHandCursor
                onClicked: if (host) host.usePreset(index)
              }
            }
          }
        }
      }

      Button {
        id: fwd
        anchors.right: presetActions.left
        anchors.verticalCenter: parent.verticalCenter
        text: "›"
        width: Style.space(30)
        foreground: host ? host.fg : Color.popups.text
        onClicked: if (host) host.cyclePreset(1)
      }

      Row {
        id: presetActions
        anchors.right: parent.right
        anchors.rightMargin: Style.spacing.sm
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.spacing.xs

        PanelActionButton {
          iconText: "＋"
          tooltipText: "Save the loaded steering as a new preset"
          foreground: host ? host.muted : Color.popups.text
          hoverColor: host ? host.accent : Color.accent
          onClicked: if (host) host.addPreset()
        }

        PanelActionButton {
          iconText: "－"
          enabled: host && host.presets.length > 1
          tooltipText: host && host.presets.length > 1
                       ? "Delete this preset"
                       : "The last preset can't be deleted"
          foreground: host ? host.muted : Color.popups.text
          hoverColor: host ? host.urgent : Color.urgent
          onClicked: if (host) host.deletePreset()
        }
      }
    }

    Row {
      width: parent.width
      spacing: Style.spacing.lg

      // left: sources
      Column {
        width: (parent.width - Style.spacing.lg) * 0.42
        spacing: Style.spacing.sm

        Text {
          text: "SOURCES"
          color: host ? host.muted : Color.popups.text
          font.family: host ? host.fontFamily : Style.font.family
          font.pixelSize: Style.font.caption
          font.letterSpacing: 1
        }

        Button {
          width: parent.width
          text: "Choose PDFs…"
          bordered: true
          leftAlign: true
          foreground: host ? host.fg : Color.popups.text
          onClicked: if (host) host.stageSample()
        }

        TextField {
          width: parent.width
          placeholderText: "paste arXiv link"
          foreground: host ? host.fg : Color.popups.text
          onActiveFocusChanged: if (host) host.editing = activeFocus
          onAccepted: { if (host) host.stageArxiv(text); text = "" }
        }

        Repeater {
          model: host ? host.staged : []
          Row {
            width: parent.width
            spacing: Style.spacing.sm
            Text {
              text: "•"
              color: host ? host.accent : Color.accent
              font.pixelSize: Style.font.caption
            }
            Text {
              width: parent.width - Style.space(28)
              text: modelData
              elide: Text.ElideMiddle
              color: host ? host.fg : Color.popups.text
              font.family: host ? host.fontFamily : Style.font.family
              font.pixelSize: Style.font.caption
            }
            Text {
              text: "✕"
              color: host ? host.muted : Color.popups.text
              font.pixelSize: Style.font.caption
              MouseArea {
                anchors.fill: parent; anchors.margins: -4
                cursorShape: Qt.PointingHandCursor
                onClicked: if (host) host.unstage(index)
              }
            }
          }
        }

        Toggle {
          width: parent.width
          visible: host && host.staged.length > 1
          label: "Combine into one"
          checked: host ? host.combine : false
          foreground: host ? host.fg : Color.popups.text
          onClicked: if (host) host.combine = !host.combine
        }
      }

      // right: steering
      Column {
        width: (parent.width - Style.spacing.lg) * 0.58
        spacing: Style.spacing.sm

        Text {
          width: parent.width
          text: "STEERING — edits save to “" + (host ? host.presetName : "") + "”"
          elide: Text.ElideRight
          color: host ? host.muted : Color.popups.text
          font.family: host ? host.fontFamily : Style.font.family
          font.pixelSize: Style.font.caption
        }

        Rectangle {
          width: parent.width
          height: Style.space(104)
          radius: Style.cornerRadius
          color: "transparent"
          border.width: 1
          border.color: area.activeFocus ? (host ? host.accent : Color.accent)
                                         : (host ? host.faint : Color.popups.border)
          QC.TextArea {
            id: area
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
            font.pixelSize: Style.font.caption
          }
        }

        Button {
          width: parent.width
          text: host && host.staged.length > 0 ? "Queue " + host.staged.length : "Queue"
          bordered: true
          enabled: host && host.staged.length > 0
          foreground: host ? (host.staged.length > 0 ? host.accent : host.muted) : Color.popups.text
          onClicked: if (host) host.addToQueue()
        }
      }
    }

    PanelSeparator { width: parent.width }

    Text {
      text: "QUEUE"
      color: host ? host.muted : Color.popups.text
      font.family: host ? host.fontFamily : Style.font.family
      font.pixelSize: Style.font.caption
      font.letterSpacing: 1
    }

    // --- the queue, as a real list -----------------------------------------
    Column {
      width: parent.width
      spacing: Style.spacing.xs

      Repeater {
        model: host ? host.queue : []

        Rectangle {
          width: parent.width
          implicitHeight: rowCol.implicitHeight + Style.spacing.sm * 2
          radius: Style.cornerRadius
          color: hover.containsMouse ? Util.alpha(host ? host.fg : Color.popups.text, 0.06) : "transparent"

          MouseArea { id: hover; anchors.fill: parent; hoverEnabled: true }

          Column {
            id: rowCol
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.margins: Style.spacing.sm
            spacing: 2

            Row {
              width: parent.width
              spacing: Style.spacing.sm

              Text {
                text: Stub.stageGlyph(modelData.stage)
                color: modelData.stage === "failed" ? (host ? host.urgent : Color.urgent)
                     : modelData.stage === "done" ? (host ? host.muted : Color.popups.text)
                     : (host ? host.accent : Color.accent)
                font.pixelSize: Style.font.bodySmall
                anchors.verticalCenter: parent.verticalCenter
              }

              Text {
                width: parent.width - Style.space(150)
                text: modelData.title
                elide: Text.ElideRight
                color: host ? host.fg : Color.popups.text
                font.family: host ? host.fontFamily : Style.font.family
                font.pixelSize: Style.font.bodySmall
                anchors.verticalCenter: parent.verticalCenter
              }

              Text {
                visible: modelData.stage !== "failed" && modelData.stage !== "done"
                text: Stub.stageLabel(modelData.stage) + (modelData.elapsed ? " · " + modelData.elapsed : "")
                color: host ? host.muted : Color.popups.text
                font.family: host ? host.fontFamily : Style.font.family
                font.pixelSize: Style.font.caption
                anchors.verticalCenter: parent.verticalCenter
              }

              Button {
                visible: modelData.stage === "failed"
                text: "Retry"
                foreground: host ? host.urgent : Color.urgent
                fontSize: Style.font.caption
                onClicked: if (host) host.retry(modelData.id)
              }

              Button {
                visible: modelData.stage === "done"
                text: "Open ↗"
                foreground: host ? host.accent : Color.accent
                fontSize: Style.font.caption
                onClicked: {}
              }
            }

            // progress / error line
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

            Text {
              visible: modelData.stage === "failed"
              width: parent.width
              text: modelData.error || ""
              elide: Text.ElideRight
              color: host ? host.urgent : Color.urgent
              font.family: host ? host.fontFamily : Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }
      }
    }
  }
}
