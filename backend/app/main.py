"""
Joulescope Logger - Web app for continuous power/energy logging.
Single-page frontend with capture controls and visualization.
"""

import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from joulescope_manager import JoulescopeManager

LOG_DIR = os.getenv("LOG_DIR", "/app/logs")
PORT = int(os.getenv("PORT", "8080"))

manager = JoulescopeManager(log_dir=LOG_DIR)

app = FastAPI(title="Joulescope Logger")


# --- REST API ---


@app.get("/api/devices")
async def list_devices():
    """List available Joulescope devices."""
    return {"devices": manager.get_devices()}


@app.get("/api/capture/status")
async def capture_status():
    """Get current capture status."""
    return manager.get_status()


class CaptureStartRequest(BaseModel):
    window_duration: float = 10.0
    output_file: str = "joulescope_log.csv"
    sampling_rate: float | None = None
    max_windows: int = 0


@app.post("/api/capture/start")
async def capture_start(body: CaptureStartRequest):
    """Start continuous capture."""
    try:
        devices = manager.get_devices()
        has_error = devices and isinstance(devices[0], dict) and "error" in devices[0]
        has_devices = devices and not has_error and len(devices) > 0
        if not has_devices:
            err_msg = devices[0].get("error", "Nenhum dispositivo Joulescope encontrado") if has_error else "Nenhum dispositivo Joulescope encontrado. Conecte o dispositivo via USB."
            return JSONResponse(status_code=400, content={"error": err_msg})
        result = manager.start_capture(
            window_duration=body.window_duration,
            output_file=body.output_file,
            sampling_rate=body.sampling_rate,
            max_windows=body.max_windows,
        )
        if "error" in result:
            return JSONResponse(status_code=400, content=result)
        return result
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/capture/stop")
async def capture_stop():
    """Stop current capture."""
    return manager.stop_capture()


@app.get("/api/experiments")
async def list_experiments():
    """List available experiment CSV files."""
    log_path = Path(LOG_DIR)
    if not log_path.exists():
        return {"files": []}
    files = []
    for f in sorted(log_path.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True):
        if "event_" not in f.name:
            files.append({"name": f.name, "path": f.name})
    return {"files": files}


def load_experiment_data(filepath: Path) -> pd.DataFrame | None:
    """Load experiment CSV file."""
    try:
        df = pd.read_csv(filepath)
        if "Window Start" in df.columns:
            df["Window Start"] = pd.to_datetime(df["Window Start"])
        if "Window End" in df.columns:
            df["Window End"] = pd.to_datetime(df["Window End"])
        numeric_cols = [
            "Duration (s)", "Samples",
            "Current Mean (A)", "Current Std (A)", "Current Min (A)", "Current Max (A)",
            "Voltage Mean (V)", "Voltage Std (V)", "Voltage Min (V)", "Voltage Max (V)",
            "Power Mean (W)", "Power Std (W)", "Power Min (W)", "Power Max (W)",
            "Energy (J)", "Energy (mWh)", "Cumulative Energy (J)", "Cumulative Energy (mWh)",
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return None


def create_plots(df: pd.DataFrame) -> dict:
    """Create Plotly figures for experiment data."""
    figures = {}
    if df is None or len(df) == 0:
        return figures

    # Time series: Current, Voltage, Power
    fig1 = make_subplots(
        rows=3, cols=1,
        subplot_titles=("Current (A)", "Voltage (V)", "Power (W)"),
        vertical_spacing=0.08,
        shared_xaxes=True,
    )
    if "Window Start" in df.columns and "Current Mean (A)" in df.columns:
        x = df["Window Start"].tolist()
        fig1.add_trace(
            go.Scatter(x=x, y=df["Current Mean (A)"].tolist(), mode="lines+markers",
                       name="Current Mean", line=dict(color="#58a6ff")),
            row=1, col=1,
        )
    if "Window Start" in df.columns and "Voltage Mean (V)" in df.columns:
        x = df["Window Start"].tolist()
        fig1.add_trace(
            go.Scatter(x=x, y=df["Voltage Mean (V)"].tolist(), mode="lines+markers",
                       name="Voltage Mean", line=dict(color="#3fb950")),
            row=2, col=1,
        )
    if "Window Start" in df.columns and "Power Mean (W)" in df.columns:
        x = df["Window Start"].tolist()
        fig1.add_trace(
            go.Scatter(x=x, y=df["Power Mean (W)"].tolist(), mode="lines+markers",
                       name="Power Mean", line=dict(color="#f85149")),
            row=3, col=1,
        )
    fig1.update_xaxes(title_text="Time", row=3, col=1)
    fig1.update_layout(
        height=500, title_text="Current, Voltage, Power Over Time",
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    figures["time_series"] = fig1.to_json()

    # Energy
    if "Window Start" in df.columns and "Cumulative Energy (J)" in df.columns:
        fig2 = go.Figure()
        x = df["Window Start"].tolist()
        fig2.add_trace(go.Scatter(
            x=x, y=df["Cumulative Energy (J)"].tolist(),
            mode="lines+markers", name="Cumulative Energy",
            line=dict(color="#a371f7", width=2),
        ))
        if "Energy (J)" in df.columns:
            fig2.add_trace(go.Bar(
                x=x, y=df["Energy (J)"].tolist(),
                name="Energy per Window", marker_color="#f0883e", opacity=0.6,
            ))
        fig2.update_layout(
            title="Energy Consumption",
            xaxis_title="Time", yaxis_title="Energy (J)", height=350,
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        )
        figures["energy"] = fig2.to_json()

    return figures


@app.get("/api/experiment/{filename}")
async def get_experiment(filename: str):
    """Get experiment data and plots."""
    try:
        path = Path(LOG_DIR) / filename
        if not path.exists():
            status = manager.get_status()
            out_file = status.get("output_file") or ""
            out_name = Path(out_file).name if out_file else ""
            if status.get("running") and (out_name == path.name or path.name in out_file):
                return {
                    "stats": {
                        "total_windows": 0, "total_energy": "0", "total_energy_mwh": "0",
                        "avg_current": "N/A", "avg_voltage": "N/A", "avg_power": "N/A",
                        "duration": "0h 0m 0s",
                    },
                    "plots": {},
                }
            return JSONResponse(status_code=404, content={"error": "File not found"})

        df = load_experiment_data(path)
        if df is None:
            return JSONResponse(status_code=400, content={"error": "Failed to load file"})

        if len(df) == 0:
            stats = {
                "total_windows": 0, "total_energy": "0", "total_energy_mwh": "0",
                "avg_current": "N/A", "avg_voltage": "N/A", "avg_power": "N/A",
                "duration": "N/A",
            }
        else:
            stats = {
                "total_windows": len(df),
                "total_energy": f"{df['Cumulative Energy (J)'].iloc[-1]:.6f}" if "Cumulative Energy (J)" in df.columns else "N/A",
                "total_energy_mwh": f"{df['Cumulative Energy (mWh)'].iloc[-1]:.6f}" if "Cumulative Energy (mWh)" in df.columns else "N/A",
                "avg_current": f"{df['Current Mean (A)'].mean():.6f}" if "Current Mean (A)" in df.columns else "N/A",
                "avg_voltage": f"{df['Voltage Mean (V)'].mean():.6f}" if "Voltage Mean (V)" in df.columns else "N/A",
                "avg_power": f"{df['Power Mean (W)'].mean():.6f}" if "Power Mean (W)" in df.columns else "N/A",
            }
            if "Window Start" in df.columns and "Window End" in df.columns:
                dur = (df["Window End"].iloc[-1] - df["Window Start"].iloc[0]).total_seconds()
                h, m = int(dur // 3600), int((dur % 3600) // 60)
                stats["duration"] = f"{h}h {m}m"
            else:
                stats["duration"] = "N/A"

        plots = create_plots(df)
        return {"stats": stats, "plots": plots}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# --- WebSocket for live updates ---


@app.websocket("/api/ws/capture")
async def websocket_capture(websocket: WebSocket):
    """WebSocket for live capture updates."""
    await websocket.accept()
    import asyncio
    queue = asyncio.Queue(maxsize=100)
    loop = asyncio.get_running_loop()

    def on_window(data: dict):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, data)
        except asyncio.QueueFull:
            pass

    manager.subscribe(on_window)
    try:
        while True:
            data = await queue.get()
            await websocket.send_json(data)
    except WebSocketDisconnect:
        pass
    finally:
        manager.unsubscribe(on_window)


# --- Static frontend (must be last) ---

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
