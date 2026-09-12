param(
    [Parameter(Mandatory=$true)][string]$Password,
    [string]$Config = "$PSScriptRoot\lan_gui_config.json"
)
$env:YSU_GUI_PASSWORD = $Password
python "$PSScriptRoot\lan_gui.py" --config $Config
