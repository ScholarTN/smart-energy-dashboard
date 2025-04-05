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
import base64
import google.generativeai as genai
from pymongo import MongoClient
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# ========== Gemini AI Configuration ==========
GOOGLE_API_KEY = "AIzaSyCL67tXc9wo6I2dvLCHZsKTms5VEKLuKEM"  # Replace with your actual key

try:
    genai.configure(
        api_key=GOOGLE_API_KEY,
        client_options={"api_endpoint": "generativelanguage.googleapis.com/v1"}
    )
    model = genai.GenerativeModel('gemini-1.0-pro')
    print("✅ Gemini AI successfully configured")
except Exception as e:
    print(f"❌ Gemini AI configuration failed: {str(e)}")
    model = None

# ========== MongoDB Connection ==========
client = MongoClient("mongodb+srv://Scholar:Scholar101!@cluster0.rub78kd.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
db = client["energydb"]
collection = db["sensorData"]

# ========== Data Fetching ==========
def fetch_data(start_date=None, end_date=None):
    query = {}
    if start_date and end_date:
        query = {"payload.timestamp": {"$gte": start_date, "$lte": end_date}}

    data = list(collection.find(query).sort("payload.timestamp", -1).limit(1000))
    if not data:
        return pd.DataFrame()

    df = pd.json_normalize(data)
    
    # Data processing
    df["timestamp"] = pd.to_datetime(df["payload.timestamp"], errors='coerce')
    df["energy_consumption_kWh"] = pd.to_numeric(df["payload.energy_consumption_kWh"], errors='coerce')
    df["voltage"] = pd.to_numeric(df["payload.voltage"], errors='coerce')
    df.dropna(subset=["timestamp", "energy_consumption_kWh", "voltage"], inplace=True)
    
    # Calculations
    df["cost"] = df["energy_consumption_kWh"] * 0.12
    df["anomaly"] = df["energy_consumption_kWh"] > (df["energy_consumption_kWh"].mean() + 2 * df["energy_consumption_kWh"].std())
    
    return df

# ========== Flask/Dash Setup ==========
server = Flask(__name__)
socketio = SocketIO(server)
app = dash.Dash(__name__, server=server, external_stylesheets=[dbc.themes.BOOTSTRAP])

# ========== Dashboard Layout ==========
app.layout = dbc.Container([
    html.H1("⚡ Smart Energy Consumption Dashboard", className="text-center mt-4 mb-4"),
    
    # Date Picker and Download Buttons
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
    
    # Summary Panel (Maintaining original structure)
    dbc.Row([
        dbc.Col(html.Div(id="summary-panel", className="alert alert-info"), width=12)
    ], className="mb-4"),
    
    # Main Energy Graph
    dbc.Row([
        dbc.Col(dcc.Graph(id="energy-graph"), width=12)
    ], className="mb-4"),
    
    # Energy AI Assistant
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

# ========== Callbacks ==========
@app.callback(
    [Output("energy-graph", "figure"), Output("summary-panel", "children")],
    [Input("interval-component", "n_intervals"),
     Input("date-picker-range", "start_date"),
     Input("date-picker-range", "end_date")]
)
def update_graph(n, start_date, end_date):
    df = fetch_data(start_date, end_date)
    
    if df.empty:
        return go.Figure(), "No Data Available"
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["timestamp"], 
        y=df["energy_consumption_kWh"], 
        mode="lines", 
        name="Energy (kWh)", 
        line=dict(color="blue")
    ))
    fig.add_trace(go.Scatter(
        x=df["timestamp"], 
        y=df["voltage"], 
        mode="lines", 
        name="Voltage", 
        line=dict(color="red")
    ))
    
    anomalies = df[df["anomaly"]]
    fig.add_trace(go.Scatter(
        x=anomalies["timestamp"], 
        y=anomalies["energy_consumption_kWh"], 
        mode="markers", 
        name="Anomalies", 
        marker=dict(color="orange", size=10)
    ))
    
    fig.update_layout(
        title="Energy Consumption Over Time",
        xaxis_title="Time",
        yaxis_title="Value",
        template="plotly_dark"
    )
    
    # Maintaining original summary structure exactly
    summary_text = f"""
    🔹 Total Energy: {df['energy_consumption_kWh'].sum():.2f} kWh  
    🔹 Peak Usage: {df['timestamp'][df['energy_consumption_kWh'].idxmax()]}  
    🔹 Estimated Cost: Rs{df['cost'].sum():.2f}  
    🔹 Voltage Range: {df['voltage'].min():.2f}V - {df['voltage'].max():.2f}V  
    """
    
    return fig, summary_text

# Fixed Download CSV callback
@app.callback(
    Output("download-dataframe-csv", "data"),
    Input("btn_csv", "n_clicks"),
    State("date-picker-range", "start_date"),
    State("date-picker-range", "end_date"),
    prevent_initial_call=True
)
def download_csv(n_clicks, start_date, end_date):
    df = fetch_data(start_date, end_date)
    if df.empty:
        return None
    return dcc.send_data_frame(df.to_csv, "energy_data.csv")

# Fixed Download PDF callback
@app.callback(
    Output("download-dataframe-pdf", "data"),
    Input("btn_pdf", "n_clicks"),
    State("date-picker-range", "start_date"),
    State("date-picker-range", "end_date"),
    prevent_initial_call=True
)
def download_pdf(n_clicks, start_date, end_date):
    df = fetch_data(start_date, end_date)
    if df.empty:
        return None
    
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    
    # Add title
    c.setFont("Helvetica-Bold", 16)
    c.drawString(100, 750, "Energy Consumption Report")
    
    # Add summary
    c.setFont("Helvetica", 12)
    c.drawString(100, 700, f"Date Range: {start_date} to {end_date}")
    c.drawString(100, 680, f"Total Energy: {df['energy_consumption_kWh'].sum():.2f} kWh")
    c.drawString(100, 660, f"Estimated Cost: Rs{df['cost'].sum():.2f}")
    
    # Save and return
    c.save()
    buffer.seek(0)
    return dcc.send_bytes(buffer.getvalue(), "energy_report.pdf")

# Fixed AI Response callback
@app.callback(
    Output("ai-response", "children"),
    Input("ask-ai-btn", "n_clicks"),
    State("user-question", "value"),
    prevent_initial_call=True
)
def get_ai_response(n_clicks, user_input):
    if not user_input:
        return "⚠️ Please enter a question!"
    
    if not model:
        return "❌ AI service is currently unavailable"
    
    try:
        response = model.generate_content(
            f"You are an energy consumption assistant. Analyze this energy data query: {user_input}\n"
            "Provide specific recommendations based on kWh consumption, voltage readings, and detected anomalies.\n"
            "Format your response with bullet points for clarity."
        )
        return f"💡 AI Suggestion: {response.text}"
    except Exception as e:
        return f"❌ Error: {str(e)}"

# ========== Run the App ==========
if __name__ == "__main__":
    app.run_server(debug=True, port=8025)
