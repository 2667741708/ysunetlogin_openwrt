param(
    [Parameter(Mandatory=$true)][string]$Password,
    [string]$Config = "$PSScriptRoot\..\lan_gui_config.json"
)
$root = Split-Path -Parent $PSScriptRoot
$env:YSU_GUI_PASSWORD = $Password
python "$root\lan_gui.py" --config $Config
