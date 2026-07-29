set repoDir to POSIX path of ((path to me as text) & "::")
set launcherPath to quoted form of (repoDir & "Launch Puddy.command")

tell application "Terminal"
    activate
    do script launcherPath
end tell
