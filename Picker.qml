import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick
import qs.Commons
import qs.Commons as Commons

// Scene picker for the cat screensaver: choose the default scene, preview any
// scene, or play them all. Open with `cat-screensaver menu`.
Item {
  id: root

  property var shell: null
  property var manifest: null

  property bool opened: false
  property string scene: "auto"
  readonly property string cli: String(Qt.resolvedUrl("bin/cat-screensaver")).replace(/^file:\/\//, "")

  readonly property var scenes: [
    { id: "auto", icon: "🕰", name: "By time of day", hours: "changes through the day" },
    { id: "cookie", icon: "🍪", name: "Cookie", hours: "05–11" },
    { id: "ramen", icon: "🍜", name: "Ramen", hours: "11–17" },
    { id: "milk", icon: "🥛", name: "Milk", hours: "17–21" },
    { id: "sleep", icon: "💤", name: "Sleep", hours: "21–05" }
  ]

  property color background: Commons.Color.menu.background
  property color foreground: Commons.Color.menu.text
  property color accent: Commons.Color.accent
  property color scrim: Commons.Color.menu.scrim
  property string fontFamily: Style.font.menuFamily
  readonly property int pad: Style.space(14)
  readonly property int radius: Math.max(Style.cornerRadius, Style.space(10))

  function open(payloadJson) {
    root.opened = true
    sceneProc.running = false
    sceneProc.running = true
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function close() {
    root.opened = false
  }

  function dismiss() {
    root.opened = false
    if (root.shell && typeof root.shell.hide === "function")
      root.shell.hide((root.manifest && root.manifest.id) || "cat-screensaver")
  }

  function toggle() {
    if (root.opened) root.dismiss()
    else root.open("{}")
  }

  function choose(id) {
    root.scene = id
    Quickshell.execDetached([root.cli, "scene", id])
  }

  // Close first so the picker isn't drawn over the screensaver window.
  function play(args) {
    root.dismiss()
    Quickshell.execDetached(["bash", "-c", "sleep 0.3; exec \"$0\" \"$@\"", root.cli].concat(args))
  }

  Process {
    id: sceneProc
    command: [root.cli, "scene"]
    stdout: StdioCollector {
      onStreamFinished: {
        var value = String(text || "").trim()
        if (value !== "") root.scene = value
      }
    }
  }

  component SceneRow: Rectangle {
    id: row
    property var item
    readonly property bool current: root.scene === item.id

    width: parent.width
    height: Style.space(48)
    radius: root.radius
    color: current ? Util.alpha(root.accent, 0.28)
         : hover.containsMouse ? Util.alpha(root.foreground, 0.12) : Util.alpha(root.foreground, 0.06)

    MouseArea {
      id: hover
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: root.choose(row.item.id)
    }

    Text {
      id: icon
      anchors.left: parent.left
      anchors.leftMargin: Style.space(12)
      anchors.verticalCenter: parent.verticalCenter
      text: row.item.icon
      font.pixelSize: Style.font.title
    }

    Column {
      anchors.left: icon.right
      anchors.leftMargin: Style.space(10)
      anchors.right: playButton.left
      anchors.verticalCenter: parent.verticalCenter
      Text {
        text: row.item.name
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
        font.bold: row.current
      }
      Text {
        text: row.item.hours
        color: root.foreground
        opacity: 0.6
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
    }

    Rectangle {
      id: playButton
      anchors.right: parent.right
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(34)
      height: Style.space(34)
      radius: width / 2
      color: playHover.containsMouse ? Util.alpha(root.foreground, 0.18) : "transparent"
      Text {
        anchors.centerIn: parent
        text: "▶"
        color: root.foreground
        font.pixelSize: Style.font.body
      }
      MouseArea {
        id: playHover
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.play(["preview", row.item.id])
      }
    }
  }

  PanelWindow {
    id: panel
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "cat-screensaver"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: root.opened ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore

    Rectangle {
      anchors.fill: parent
      color: root.scrim
    }

    MouseArea {
      anchors.fill: parent
      onClicked: root.dismiss()
    }

    Rectangle {
      width: Math.min(Style.space(360), panel.width - Style.space(24))
      height: content.implicitHeight + root.pad * 2
      anchors.centerIn: parent
      radius: root.radius + Style.space(4)
      color: root.background
      border.color: Util.alpha(Commons.Color.foreground, 0.12)
      border.width: 1

      MouseArea { anchors.fill: parent; onClicked: {} }

      Item {
        id: keyCatcher
        anchors.fill: parent
        focus: true
        Keys.onPressed: function(event) {
          if (event.key === Qt.Key_Escape) { root.dismiss(); event.accepted = true }
        }
      }

      Column {
        id: content
        anchors.fill: parent
        anchors.margins: root.pad
        spacing: Style.space(8)

        Text {
          text: "Cat screensaver"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
        }

        Repeater {
          model: root.scenes
          delegate: SceneRow { item: modelData }
        }

        Rectangle {
          width: parent.width
          height: Style.space(40)
          radius: root.radius
          color: allHover.containsMouse ? Util.alpha(root.accent, 0.4) : Util.alpha(root.accent, 0.22)
          Text {
            anchors.centerIn: parent
            text: "▶  Show all scenes"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }
          MouseArea {
            id: allHover
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.play(["demo"])
          }
        }
      }
    }
  }
}
