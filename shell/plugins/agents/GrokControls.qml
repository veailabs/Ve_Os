import QtQuick
import QtQuick.Controls as Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

FocusScope {
  id: root
  readonly property string home: Quickshell.env("HOME")
  property bool expanded: false
  property bool hasCursor: false
  property string status: "idle"
  property string result: ""
  property string sessionId: ""
  property string error: ""
  property string botNotice: ""
  property var permission: null
  property var queued: null
  readonly property bool busy: ["starting", "running", "permission", "cancelling"].indexOf(status) >= 0
  readonly property color foreground: Commons.Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.5)
  implicitHeight: contents.implicitHeight
  signal leave()
  signal hovered()

  function activate() {
    expanded = true
    openBot.forceActiveFocus()
    if (!bridge.running) send({action: "status"})
  }

  function send(message) {
    if (bridge.running) bridge.write(JSON.stringify(message) + "\n")
    else {
      queued = message
      bridge.running = true
    }
  }

  function start(resume) {
    if (busy || !prompt.text.trim() || !directory.text.trim()) return
    error = ""
    result = ""
    permission = null
    status = "starting"
    send({action: "start", cwd: directory.text.trim(), prompt: prompt.text, resume: resume === true})
  }

  function receive(line) {
    try {
      var event = JSON.parse(line)
      if (event.type === "status") {
        status = String(event.status || "error")
        sessionId = String(event.sessionId || "")
        if (typeof event.cwd === "string" && event.cwd !== "") directory.text = event.cwd
        if (typeof event.text === "string") result = event.text
        if (status !== "permission") permission = null
      } else if (event.type === "text") {
        result = (result + String(event.text || "")).slice(-65536)
      } else if (event.type === "permission") {
        permission = event
        status = "permission"
      } else if (event.type === "error") {
        error = String(event.message || "")
        if (status === "starting") status = "error"
      }
    } catch (e) {
      error = Commons.I18n.t("agents.grokInvalidResponse")
    }
  }

  function decide(optionId) {
    if (!permission) return
    send({action: "permission", id: permission.id, optionId: optionId})
  }

  function bot(action) {
    if (botProcess.running) return
    botNotice = ""
    botProcess.command = ["omarchy-agent-grok-bot", action]
    if (action === "draft") botProcess.command = ["omarchy-agent-grok-bot", action, prompt.text]
    botProcess.running = true
  }

  Keys.onEscapePressed: function(event) {
    root.leave()
    event.accepted = true
  }

  Process {
    id: bridge
    command: ["omarchy-agent-grok-control"]
    stdinEnabled: true
    onStarted: {
      if (root.queued) {
        write(JSON.stringify(root.queued) + "\n")
        root.queued = null
      }
    }
    stdout: SplitParser { onRead: function(line) { root.receive(line) } }
    onExited: {
      root.permission = null
      if (root.busy) {
        root.status = "error"
        root.error = Commons.I18n.t("agents.grokDisconnected")
      }
    }
  }

  Process {
    id: botProcess
    stdout: SplitParser {
      onRead: function(line) {
        try {
          var response = JSON.parse(line)
          root.botNotice = String(response.message || response.status || "")
        } catch (e) {}
      }
    }
    onExited: function(code) {
      if (code !== 0) root.botNotice = Commons.I18n.t("agents.grokBotFailed")
    }
  }

  Column {
    id: contents
    width: parent.width
    spacing: Style.space(10)

    Ui.Button {
      width: parent.width
      text: Commons.I18n.t("agents.grokControls")
      hasCursor: root.hasCursor
      focusable: true
      selected: root.expanded
      onHovered: function(value) { if (value) root.hovered() }
      onClicked: {
        if (root.expanded) {
          root.expanded = false
          root.leave()
        } else root.activate()
      }
    }

    Column {
      visible: root.expanded
      width: parent.width
      spacing: Style.space(10)

      Flow {
        width: parent.width
        spacing: Style.space(6)
        Ui.Button {
          id: openBot
          text: Commons.I18n.t("agents.grokOpenBot")
          focusable: true
          enabled: !botProcess.running
          onClicked: root.bot("open")
        }
        Ui.Button {
          text: Commons.I18n.t("agents.grokCloseBot")
          focusable: true
          enabled: !botProcess.running
          onClicked: root.bot("close")
        }
        Ui.Button {
          text: Commons.I18n.t("agents.grokOpenCli")
          focusable: true
          onClicked: Quickshell.execDetached(["omarchy-launch-terminal", "grok"])
        }
      }

      Text {
        width: parent.width
        text: Commons.I18n.t("agents.grokCloudNotice")
        textFormat: Text.PlainText
        color: root.dim
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }

      Ui.TextField {
        id: directory
        width: parent.width
        text: root.home
        placeholderText: Commons.I18n.t("agents.grokDirectory")
        Accessible.name: Commons.I18n.t("agents.grokDirectory")
        enabled: !root.busy
      }

      Controls.TextArea {
        id: prompt
        width: parent.width
        height: Style.space(90)
        placeholderText: Commons.I18n.t("agents.grokPrompt")
        Accessible.name: Commons.I18n.t("agents.grokPrompt")
        wrapMode: TextEdit.Wrap
        color: root.foreground
        placeholderTextColor: root.dim
        selectionColor: Commons.Color.accent
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        enabled: !root.busy
        background: Rectangle {
          color: Commons.Color.popups.background
          border.color: prompt.activeFocus ? Commons.Color.accent : root.dim
          radius: Style.cornerRadius
        }
      }

      Flow {
        width: parent.width
        spacing: Style.space(6)
        Ui.Button {
          text: Commons.I18n.t("agents.grokRunTask")
          enabled: !root.busy && prompt.text.trim() !== "" && directory.text.trim() !== ""
          focusable: true
          onClicked: root.start(false)
        }
        Ui.Button {
          text: Commons.I18n.t("agents.grokResumeTask")
          enabled: !root.busy && root.sessionId !== "" && prompt.text.trim() !== ""
          focusable: true
          onClicked: root.start(true)
        }
        Ui.Button {
          text: Commons.I18n.t("agents.grokCancelTask")
          enabled: root.busy && root.status !== "cancelling"
          focusable: true
          onClicked: {
            root.send({action: "cancel"})
            root.status = "cancelling"
            root.permission = null
          }
        }
        Ui.Button {
          text: Commons.I18n.t("agents.grokBotDraft")
          enabled: !botProcess.running && prompt.text.trim() !== "" && Array.from(prompt.text).length <= 1000
          focusable: true
          onClicked: root.bot("draft")
        }
      }

      Text {
        width: parent.width
        text: Commons.I18n.t("agents.grokDraftNotice")
        textFormat: Text.PlainText
        color: root.dim
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }

      Text {
        width: parent.width
        text: Commons.I18n.t("agents.grokTaskStatus").arg(root.status)
        textFormat: Text.PlainText
        color: root.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
      }

      Text {
        width: parent.width
        visible: root.error !== "" || root.botNotice !== "" || root.status === "sign_in_required"
        text: root.status === "sign_in_required" ? Commons.I18n.t("agents.grokSignIn") : root.error || root.botNotice
        textFormat: Text.PlainText
        color: root.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }

      Column {
        width: parent.width
        visible: root.permission !== null
        spacing: Style.space(6)
        Text {
          width: parent.width
          text: root.permission ? Commons.I18n.t("agents.grokPermission").arg(root.permission.title || "") : ""
          textFormat: Text.PlainText
          color: root.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          wrapMode: Text.WordWrap
        }
        Flow {
          width: parent.width
          spacing: Style.space(6)
          Ui.Button {
            text: Commons.I18n.t("agents.grokDeny")
            focusable: true
            onClicked: root.decide(null)
          }
          Repeater {
            model: root.permission ? root.permission.options : []
            Ui.Button {
              required property var modelData
              text: modelData.name
              focusable: true
              onClicked: root.decide(modelData.optionId)
            }
          }
        }
      }

      Controls.ScrollView {
        width: parent.width
        height: Math.min(Style.space(220), output.implicitHeight + Style.space(16))
        visible: root.result !== ""
        clip: true
        Controls.TextArea {
          id: output
          width: parent.width
          text: root.result
          readOnly: true
          selectByMouse: true
          textFormat: TextEdit.PlainText
          wrapMode: TextEdit.Wrap
          color: root.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          Accessible.name: Commons.I18n.t("agents.grokResult")
        }
      }
    }
  }
}
