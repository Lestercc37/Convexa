# Shows a Windows toast with the text in the given file. Run by the
# ConvexaStreamAlertToast scheduled task, which runs as the logged-in desktop
# user (a task running as SYSTEM cannot show anything on that user's screen);
# backend/scripts/stream_health_check.py writes the message file and starts
# that task. Uses Windows PowerShell's own AppUserModelID, because toasts from
# an unregistered app id are silently dropped.
param([Parameter(Mandatory = $true)][string]$MessageFile)

$message = (Get-Content -LiteralPath $MessageFile -Raw -Encoding UTF8).Trim()
if (-not $message) { exit 0 }
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'

[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$escaped = [System.Security.SecurityElement]::Escape($message)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@"
<toast scenario="reminder">
  <visual><binding template="ToastGeneric">
    <text>Convexa</text>
    <text>$escaped</text>
  </binding></visual>
  <actions><action content="OK" arguments="dismiss" activationType="system"/></actions>
</toast>
"@)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
    [Windows.UI.Notifications.ToastNotification]::new($xml))
