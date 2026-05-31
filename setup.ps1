$pythonPath = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
if (-not (Test-Path $pythonPath)) {
    $pythonPath = "python" # Fallback to system path
}
& $pythonPath -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Write-Host "Environment Setup Complete. To activate, run: .\venv\Scripts\Activate.ps1"
