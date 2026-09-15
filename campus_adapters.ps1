$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$adapters = @(Get-NetAdapter -Physical)
$addresses = @(Get-NetIPAddress -AddressFamily IPv4)
$dnsRows = @(Get-DnsClientServerAddress -AddressFamily IPv4)
$routes = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0')
$rows = @()
foreach ($adapter in $adapters) {
    $index = [int]$adapter.ifIndex
    $ips = @($addresses | Where-Object InterfaceIndex -EQ $index | Select-Object -ExpandProperty IPAddress)
    $dns = @($dnsRows | Where-Object InterfaceIndex -EQ $index | Select-Object -ExpandProperty ServerAddresses)
    $gateways = @($routes | Where-Object InterfaceIndex -EQ $index | Select-Object -ExpandProperty NextHop)
    $rows += [pscustomobject]@{
        name = [string]$adapter.Name
        description = [string]$adapter.InterfaceDescription
        index = $index
        up = ([string]$adapter.Status -eq 'Up')
        kind = $(if ([int]$adapter.InterfaceType -eq 71) { 'wifi' } else { 'ethernet' })
        addresses = $ips
        dns_servers = $dns
        gateways = $gateways
    }
}
ConvertTo-Json -InputObject $rows -Depth 5 -Compress
