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
import google.generativeai as genai
from pymongo import MongoClient
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# ========== Gemini AI Configuration ==========
GOOGLE_API_KEY = "AIzaSyCL67tXc9wo6I2dvLCHZsKTms5VEKLuKEM"  # Replace with your actual new key

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
    
    # Summary Panel
    dbc.Row([dbc.Col(html.Div(id="summary-panel", className="alert alert-info"), width=12)], className="mb-4"),
    
    # Main Energy Graph
    dbc.Row([dbc.Col(dcc.Graph(id="energy-graph"), width=12)], className="mb-4"),
    
    # Energy AI Assistant
    dbc.Row([
        dbc.Col([
            html.H4("🤖 Energy AI Assistant", className="mb-3"),
            dcc.Textarea(
                id="user-question",
                placeholder="Ask about energy patterns, anomalies, or conservation tips...",
                style={'width': '100%', 'height': 100},
                className="mb-2"
            ),
            dbc.Button("Analyze with AI", id="ask-ai-btn", color="success", className="mb-3"),
            html.Div(id="ai-response", className="mt-3")
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
        return go.Figure(), "No data available for the selected time range"
    
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
        line=dict(color="red", dash="dot")
    ))
    
    anomalies = df[df["anomaly"]]
    fig.add_trace(go.Scatter(
        x=anomalies["timestamp"], 
        y=anomalies["energy_consumption_kWh"], 
        mode="markers", 
        name="Anomalies", 
        marker=dict(color="orange", size=10, symbol="x")
    ))
    
    fig.update_layout(
        title="Energy Consumption Over Time",
        xaxis_title="Time",
        yaxis_title="Value",
        template="plotly_dark",
        hovermode="x unified"
    )
    
    summary_text = [
        html.H5("📊 Energy Summary", className="alert-heading"),
        html.P(f"🔸 Total Consumption: {df['energy_consumption_kWh'].sum():.2f} kWh"),
        html.P(f"🔸 Estimated Cost: Rs{df['cost'].sum():.2f}"),
        html.P(f"🔸 Voltage Range: {df['voltage'].min():.2f}V - {df['voltage'].max():.2f}V"),
        html.P(f"🔸 Anomalies Detected: {len(anomalies)}"),
    ]
    
    return fig, dbc.Alert(summary_text, color="info")

@app.callback(
    Output("ai-response", "children"),
    Input("ask-ai-btn", "n_clicks"),
    State("user-question", "value"),
    prevent_initial_call=True
)
def get_ai_response(n_clicks, user_input):
    if not user_input:
        return dbc.Alert("Please enter a question about your energy data", color="warning")
    
    if not model:
        return dbc.Alert("AI service is currently unavailable", color="danger")
    
    try:
        # Enhanced prompt with data context
        prompt = f"""You are an expert energy analyst. Analyze this query about electricity data:

        Data Context:
        - Metrics: kWh consumption and voltage readings
        - Time range: Last 7 days (default)
        - Anomalies: Detected at >2 standard deviations
        - Cost calculation: Rs 0.12 per kWh

        User Question: {user_input}

        Provide your response with:
        1. Technical analysis of patterns
        2. Explanation of anomalies
        3. Energy conservation tips
        4. Cost-saving recommendations
        
        Format your response in clear markdown with bullet points."""
        
        response = model.generate_content(prompt)
        
        if not response.text:
            return dbc.Alert("The AI response was empty. Please try again.", color="warning")
        
        return dbc.Card([
            dbc.CardHeader("🔍 Energy AI Analysis"),
            dbc.CardBody(dcc.Markdown(response.text))
        ], className="mt-3")
        
    except Exception as e:
        return dbc.Alert([
            html.H5("⚠️ AI Service Error"),
            html.P(str(e)),
            html.P("Please try again later or rephrase your question")
        ], color="danger")

# ========== Run the App ==========
if __name__ == "__main__":
    app.run_server(debug=True, port=8025)
