cd "C:\Sales Dashboard"
curl "http://localhost:5000/api/managers?date_from=2025-11-01&date_to=2025-11-30" |
  ConvertFrom-Json |
  Select-Object -ExpandProperty data |
  Where-Object { $_.fCODE -eq 'A003' } |
  Format-List fNAME, TotalSales, Debt, CustomerCount