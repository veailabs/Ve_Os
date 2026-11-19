import QtQuick
import QtQuick.Effects
import Quickshell

Item {
  id: root
  required property color tint

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
    color: root.tint
    layer.enabled: true
    layer.effect: MultiEffect {
      maskEnabled: true
      maskSource: logo
      maskThresholdMin: 0.5
      maskSpreadAtMin: 1.0
    }
  }
}
