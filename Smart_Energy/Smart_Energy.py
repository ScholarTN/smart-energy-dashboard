import dash
import dash_bootstrap_components as dbc
from dash import dcc, html
from dash.dependencies import Input, Output, State
from flask import Flask
from flask_socketio import SocketIO
import pandas as pd
import datetime
import plotly.graph_objects as go
import io
import openai
from pymongo import MongoClient
import google.generativeai as genai
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# ✅ Initialize Gemini AI
genai.configure(api_key="AIzaSyDJBErHnC-7WPAqXfBdr8cjebynAMm08SA")

# ✅ MongoDB Atlas Connection
client = MongoClient("mongodb+srv://Scholar:Scholar101!@cluster0.rub78kd.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
db = client["energydb"]
collection = db["sensorData"]

# ✅ Fetch Data from MongoDB Atlas
def fetch_data(start_date=None, end_date=None):
    query = {}
    if start_date and end_date:
        start_str = pd.to_datetime(start_date)
        end_str = pd.to_datetime(end_date)
        query = {"payload.timestamp": {"$gte": start_str.isoformat(), "$lte": end_str.isoformat()}}

    data = list(collection.find(query).sort("payload.timestamp", -1).limit(1000))
    if not data:
        return pd.DataFrame()

    df = pd.json_normalize(data)

    df["timestamp"] = pd.to_datetime(df["payload.timestamp"])
    df["energy_consumption_kWh"] = pd.to_numeric(df["payload.energy_consumption_kWh"], errors='coerce')
    df["voltage"] = pd.to_numeric(df["payload.voltage"], errors='coerce')
    df = df.dropna(subset=["timestamp", "energy_consumption_kWh", "voltage"])
    df["cost"] = df["energy_consumption_kWh"] * 0.12
    df["anomaly"] = df["energy_consumption_kWh"] > (df["energy_consumption_kWh"].mean() + 2 * df["energy_consumption_kWh"].std())

    return df

# ✅ Flask app for real-time updates
server = Flask(__name__)
socketio = SocketIO(server)

# ✅ Dash app
app = dash.Dash(__name__, server=server, external_stylesheets=[dbc.themes.BOOTSTRAP])

# ✅ Layout
app.layout = dbc.Container([
    html.H1("⚡ Smart Energy Consumption Dashboard", className="text-center mt-4 mb-4"),

    dbc.Row([
        dbc.Col(dcc.DatePickerRange(
            id='date-picker-range',
            start_date=datetime.datetime.now() - datetime.timedelta(days=7),
            end_date=datetime.datetime.now(),
            display_format='YYYY-MM-DD'
        ), width=6),
        dbc.Col(html.Button("Download CSV", id="btn_csv", className="btn btn-primary"), width=3),
        dbc.Col(html.Button("Download PDF", id="btn_pdf", className="btn btn-danger"), width=3)
    ], className="mb-4"),

    dbc.Row([dbc.Col(html.Div(id="summary-panel", className="alert alert-info"), width=12)], className="mb-4"),
    
    dbc.Row([dbc.Col(dcc.Graph(id="energy-graph"), width=12)], className="mb-4"),

    # ✅ AI Chat Section
    dbc.Row([
        dbc.Col([
            html.H4("🤖 Ask Energy AI"),
            dcc.Input(id="user-question", type="text", placeholder="Ask about energy usage...", className="form-control"),
            html.Button("Get Answer", id="ask-ai-btn", className="btn btn-success mt-2"),
            html.Div(id="ai-response", className="alert alert-warning mt-2")
        ], width=12)
    ], className="mb-4"),

    dcc.Interval(id="interval-component", interval=5000, n_intervals=0),
    
    dcc.Download(id="download-dataframe-csv"),
    dcc.Download(id="download-dataframe-pdf")
])

# ✅ Callback to update the graph
@app.callback(
    Output("energy-graph", "figure"),
    Output("summary-panel", "children"),
    Input("interval-component", "n_intervals"),
    Input("date-picker-range", "start_date"),
    Input("date-picker-range", "end_date")
)
def update_graph(n, start_date, end_date):
    df = fetch_data(start_date, end_date)

    if df.empty:
        return go.Figure(), "⚠️ No Data Available"

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["timestamp"], y=df["energy_consumption_kWh"], mode="lines", name="Energy (kWh)", line=dict(color="blue")))
    fig.add_trace(go.Scatter(x=df["timestamp"], y=df["voltage"], mode="lines", name="Voltage", line=dict(color="red")))

    anomalies = df[df["anomaly"]]
    fig.add_trace(go.Scatter(x=anomalies["timestamp"], y=anomalies["energy_consumption_kWh"], mode="markers", name="Anomalies", marker=dict(color="orange", size=10)))

    fig.update_layout(title="Energy Consumption Over Time", xaxis_title="Time", yaxis_title="Value", template="plotly_dark")

    summary_text = f"""
    🔹 Total Energy: {df['energy_consumption_kWh'].sum():.2f} kWh  
    🔹 Peak Usage: {df['timestamp'][df['energy_consumption_kWh'].idxmax()]}  
    🔹 Estimated Cost: Rs{df['cost'].sum():.2f}  
    🔹 Voltage Range: {df['voltage'].min():.2f}V - {df['voltage'].max():.2f}V  
    """

    return fig, summary_text

# ✅ AI Chat Callback
@app.callback(
    Output("ai-response", "children"),
    Input("ask-ai-btn", "n_clicks"),
    State("user-question", "value"),
    prevent_initial_call=True
)
def get_ai_response(n_clicks, user_input):
    if not user_input:
        return "⚠️ Please enter a question!"

    try:
        response = genai.generate_content(user_input)
        return f"💡 AI Suggestion: {response.text}"
    except Exception as e:
        return f"❌ Error: {str(e)}"

# ✅ Callback for CSV download
@app.callback(
    Output("download-dataframe-csv", "data"),
    Input("btn_csv", "n_clicks"),
    prevent_initial_call=True
)
def download_csv(n_clicks):
    df = fetch_data()
    if df.empty:
        return dcc.send_data_frame(pd.DataFrame().to_csv, "empty.csv")
    return dcc.send_data_frame(df.to_csv, "energy_data.csv")

# ✅ Callback for PDF download
@app.callback(
    Output("download-dataframe-pdf", "data"),
    Input("btn_pdf", "n_clicks"),
    prevent_initial_call=True
)
def download_pdf(n_clicks):
    df = fetch_data()
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.drawString(100, 750, "Smart Energy Consumption Report")
    if not df.empty:
        pdf.drawString(100, 730, f"Total Energy: {df['energy_consumption_kWh'].sum():.2f} kWh")
        pdf.drawString(100, 710, f"Peak Usage: {df['timestamp'][df['energy_consumption_kWh'].idxmax()]}")
        pdf.drawString(100, 690, f"Estimated Cost: Rs{df['cost'].sum():.2f}")
    else:
        pdf.drawString(100, 730, "No data available.")
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return dcc.send_bytes(buffer.getvalue(), "energy_report.pdf")

# ✅ Gunicorn server requirement for deployment
server = app.server

if __name__ == "__main__":
    app.run(debug=False, port=8025, host="0.0.0.0")
