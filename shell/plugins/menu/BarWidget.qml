import QtQuick
import QtQuick.Effects
import Quickshell
import qs.Ui

BarWidget {
  id: root
  moduleName: "omarchy.menu"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    iconComponent: Component {
      Item {
        Image {
          id: logo
          anchors.fill: parent
          source: "file://" + Quickshell.env("OMARCHY_PATH") + "/logo.svg"
          sourceSize.width: Math.round(width * Screen.devicePixelRatio)
          sourceSize.height: Math.round(height * Screen.devicePixelRatio)
          fillMode: Image.PreserveAspectFit
          visible: false
          layer.enabled: true
        }
        Rectangle {
          anchors.fill: parent
          color: button.foreground
          layer.enabled: true
          layer.effect: MultiEffect {
            maskEnabled: true
            maskSource: logo
            maskThresholdMin: 0.5
            maskSpreadAtMin: 1.0
          }
        }
      }
    }
    onPressed: function(button) {
      if (!root.bar) return
      if (button === Qt.RightButton) root.bar.run("xdg-terminal-exec")
      else root.bar.run("omarchy-shell shell toggle omarchy.menu '{\"menu\":\"root\"}'")
    }
  }
}
