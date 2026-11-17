# Abre as planilhas de exemplo no Excel já formatadas para a gravação do vídeo.
# A formatação fica só na tela: o CSV não guarda formatação, e o arquivo que o robô lê continua igual.
#
#   powershell.exe -ExecutionPolicy Bypass -File scripts\abrir_planilhas_excel.ps1
param([string]$Pasta = (Join-Path $PSScriptRoot "..\exemplos"))

$Pasta = (Resolve-Path $Pasta).ProviderPath
$arquivos = @("planilha-errada.csv", "relatos-central156-novembro.csv")  # o último fica na frente

try { $excel = [Runtime.InteropServices.Marshal]::GetActiveObject("Excel.Application") }
catch { $excel = New-Object -ComObject Excel.Application }
$excel.Visible = $true
$excel.DisplayAlerts = $false
$m = [Type]::Missing

foreach ($nome in $arquivos) {
    foreach ($wb in @($excel.Workbooks)) { if ($wb.Name -eq $nome) { $wb.Close($false) } }

    # Local = true: usa o separador ";" e a vírgula decimal do Windows em português
    $wb = $excel.Workbooks.Open((Join-Path $Pasta $nome), $m, $true, $m, $m, $m, $m, $m, $m, $m, $m, $m, $false, $true)
    $ws = $wb.Worksheets.Item(1)
    $faixa = $ws.UsedRange
    $colunas = $faixa.Columns.Count

    $tabela = $ws.ListObjects.Add(1, $faixa, $m, 1)
    $tabela.TableStyle = "TableStyleMedium2"
    $faixa.Font.Name = "Segoe UI"
    $faixa.Font.Size = 11
    $faixa.VerticalAlignment = -4160  # topo

    for ($c = 1; $c -le $colunas; $c++) {
        $titulo = [string]$ws.Cells.Item(1, $c).Value2
        $coluna = $ws.Columns.Item($c)
        switch ($titulo) {
            # NumberFormatLocal: o formato escrito como no Excel em português (vírgula decimal, "aaaa")
            "latitude"  { $coluna.NumberFormatLocal = "0,0000"; $coluna.HorizontalAlignment = -4152 }
            "longitude" { $coluna.NumberFormatLocal = "0,0000"; $coluna.HorizontalAlignment = -4152 }
            "data"      { $coluna.NumberFormatLocal = "dd/mm/aaaa" }
        }
    }
    $ws.Columns.AutoFit() | Out-Null
    for ($c = 1; $c -le $colunas; $c++) {
        $titulo = [string]$ws.Cells.Item(1, $c).Value2
        $coluna = $ws.Columns.Item($c)
        if ($titulo -in @("descricao", "endereco") -and $coluna.ColumnWidth -gt 38) {
            $coluna.ColumnWidth = 38
            $coluna.WrapText = $true
        }
        $coluna.ColumnWidth = $coluna.ColumnWidth + 2
    }
    $ws.Rows.AutoFit() | Out-Null

    $wb.Activate()
    $janela = $excel.ActiveWindow
    $janela.DisplayGridlines = $false
    $janela.Zoom = 120
    $janela.SplitRow = 1
    $janela.FreezePanes = $true
    $ws.Range("A1").Select() | Out-Null
}
$excel.DisplayAlerts = $true
