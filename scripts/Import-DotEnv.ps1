param(
    [string]$Path = ""
)

if (-not $Path) {
    $Path = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path ".env"
}

if (-not (Test-Path -LiteralPath $Path)) {
    throw "Файл .env не найден: $Path"
}

$loaded = 0
Get-Content -LiteralPath $Path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line) { return }
    if ($line.StartsWith("#")) { return }
    if ($line.StartsWith("export ")) {
        $line = $line.Substring(7).Trim()
    }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) { return }

    $name = $line.Substring(0, $eq).Trim()
    $value = $line.Substring($eq + 1)

    if (
        ($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))
    ) {
        $value = $value.Substring(1, $value.Length - 2)
    }

    [Environment]::SetEnvironmentVariable($name, $value, "Process")
    $loaded++
}

Write-Host "Загружены переменные из $Path ($loaded шт.)"
