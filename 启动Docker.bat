@echo off
setlocal
pushd "%~dp0"

if not exist ".env" (
  echo .env was not found. Creating it from .env.example...
  copy ".env.example" ".env" >nul
)

where docker >nul 2>nul
if errorlevel 1 (
  echo Docker command was not found.
  echo Please install and start Docker Desktop, then run this script again.
  pause
  exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop is not running yet.
  echo Please open Docker Desktop and wait until it says "Docker Desktop is running".
  echo Then run this script again.
  pause
  exit /b 1
)

echo Building and starting Docker service...
docker compose up -d --build

echo.
echo Docker service command finished.
echo Open: http://127.0.0.1:8000/
echo.
echo If it fails, make sure port 8000 is not occupied by the local uvicorn service.
pause
