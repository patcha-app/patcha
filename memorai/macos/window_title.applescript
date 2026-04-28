tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    try
        set windowTitle to name of front window of frontApp
    on error
        set windowTitle to ""
    end try
    return appName & "|||" & windowTitle
end tell
