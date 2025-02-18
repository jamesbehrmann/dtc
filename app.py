import streamlit as st
import imaplib
import email
import sqlite3
from openai import OpenAI
import re
import pandas as pd
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

# Page configuration
st.set_page_config(layout="wide", initial_sidebar_state="expanded")
st.markdown("""
    <style>
        .stApp {
            background-color: #0E1117;
            color: white;
        }
    </style>
""", unsafe_allow_html=True)

# Configuration
IMAP_SERVER = "imap.ionos.com"
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASSWORD = st.secrets["EMAIL_PASSWORD"]
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Get unique vehicles for filtering
def get_unique_vehicles():
    conn = sqlite3.connect("dtc_logs.db")
    c = conn.cursor()
    c.execute("SELECT DISTINCT vehicle_name FROM dtc_logs")
    vehicles = [row[0] for row in c.fetchall()]
    conn.close()
    return vehicles

# Sidebar setup
st.sidebar.title("FLEET NEXIS DTC INTERPRETER")
st.sidebar.divider()

# Add date pickers
start_date = st.sidebar.date_input("Start Date", datetime.now().replace(day=1))
end_date = st.sidebar.date_input("End Date", datetime.now())
st.sidebar.divider()

# Add vehicle filter
vehicles = get_unique_vehicles()
selected_vehicle = st.sidebar.selectbox(
    "Filter by Vehicle",
    ["All Vehicles"] + vehicles,
    index=0
)
st.sidebar.divider()

# Database initialization and migration
def init_db():
    conn = sqlite3.connect("dtc_logs.db")
    c = conn.cursor()
    
    try:
        c.execute("SELECT * FROM dtc_logs LIMIT 1")
        existing_columns = [description[0] for description in c.description]
    except sqlite3.OperationalError:
        existing_columns = []
    
    if not existing_columns:
        c.execute('''CREATE TABLE dtc_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vehicle_name TEXT,
                    dtc_text TEXT,
                    ai_interpretation TEXT,
                    gps_coordinates TEXT,
                    location_address TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    email_timestamp TEXT,
                    raw_email TEXT
                 )''')
    else:
        required_columns = {
            'vehicle_name': 'TEXT',
            'dtc_text': 'TEXT',
            'ai_interpretation': 'TEXT',
            'gps_coordinates': 'TEXT',
            'location_address': 'TEXT',
            'timestamp': 'DATETIME DEFAULT CURRENT_TIMESTAMP',
            'email_timestamp': 'TEXT',
            'raw_email': 'TEXT'
        }
        
        for col_name, col_type in required_columns.items():
            if col_name not in existing_columns:
                try:
                    c.execute(f"ALTER TABLE dtc_logs ADD COLUMN {col_name} {col_type}")
                except sqlite3.OperationalError as e:
                    st.error(f"Error adding column {col_name}: {str(e)}")
    
    conn.commit()
    conn.close()

# Fetch emails via IMAP
def fetch_emails():
    status_placeholder = st.empty()
    debug_placeholder = st.empty()
    status_placeholder.info("Connecting to email server...")
    
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993)
    mail.login(EMAIL_USER, EMAIL_PASSWORD)
    mail.select("inbox")
    
    # Format dates for IMAP (DD-MMM-YYYY) and make uppercase for IMAP
    start_date_str = start_date.strftime("%d-%b-%Y").upper()
    end_date_str = (end_date + timedelta(days=1)).strftime("%d-%b-%Y").upper()
    
    status_placeholder.info("Searching for emails from notify@onestepgps.com...")
    
    # Simplified search criteria
    search_criteria = f'FROM "notify@onestepgps.com" SINCE {start_date_str} BEFORE {end_date_str}'
    
    try:
        status, messages = mail.search('UTF-8', search_criteria)
        debug_info = [f"Search criteria: {search_criteria}"]
        email_ids = messages[0].split()
        
        if not email_ids:
            status_placeholder.warning("No emails found in the selected date range.")
            return []
        
        status_placeholder.info(f"Processing {len(email_ids)} emails...")
        dtc_entries = []
        
        for e_id in email_ids:
            status, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject = msg.get('subject', '')
                    debug_info.append(f"Processing email with subject: {subject}")
                    
                    email_body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                email_body = part.get_payload(decode=True).decode()
                                break
                    else:
                        email_body = msg.get_payload(decode=True).decode()
                    
                    # Extract email timestamp
                    email_date = parsedate_to_datetime(msg.get('date'))
                    
                    entry = extract_dtc_info(email_body)
                    if entry:
                        entry["raw_email"] = email_body
                        entry["email_timestamp"] = email_date.strftime("%Y-%m-%d %I:%M:%S %p")
                        dtc_entries.append(entry)
                        debug_info.append(f"Successfully processed email: {subject}")
        
        # Display debug information
        with st.expander("Debug Information"):
            st.write("\n".join(debug_info))
        
        mail.logout()
        status_placeholder.success(f"Email processing complete! Found {len(dtc_entries)} emails.")
        return dtc_entries
        
    except Exception as e:
        status_placeholder.error(f"Error searching emails: {str(e)}")
        return []

# Extract DTC info from email body
def extract_dtc_info(text):
    match = re.search(r'Device: (.*?)\nEvent: (.*?)\nSpeed:', text, re.DOTALL)
    time_match = re.search(r'Time: (.*?)\n', text)
    
    if match:
        vehicle_name = match.group(1).strip()
        dtc_text = match.group(2).strip()
        timestamp = time_match.group(1).strip() if time_match else "N/A"
        
        return {
            "vehicle_name": vehicle_name,
            "dtc_text": dtc_text,
            "timestamp": timestamp,
            "gps_coordinates": "N/A",
            "location_address": "N/A",
            "raw_email": text
        }
    return None

# Interpret DTC codes using GPT
def interpret_dtc(dtc_text, status_placeholder):
    status_placeholder.info("Interpreting DTC codes...")
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an expert vehicle diagnostic assistant. Provide direct interpretations without disclaimers or recommendations to consult mechanics. Provide Ai Recommendations" on next steps"},
                {"role": "user", "content": f"What do these diagnostic trouble codes mean: {dtc_text}?"}
            ]
        )
        status_placeholder.success("DTC interpretation complete!")
        return response.choices[0].message.content
    except Exception as e:
        status_placeholder.error(f"Error interpreting DTC: {str(e)}")
        return "Error interpreting DTC code"

# Save to database
def save_to_db(vehicle_name, dtc_text, ai_interpretation, gps_coordinates, location_address, raw_email, email_timestamp):
    conn = sqlite3.connect("dtc_logs.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO dtc_logs 
        (vehicle_name, dtc_text, ai_interpretation, gps_coordinates, location_address, raw_email, email_timestamp) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (vehicle_name, dtc_text, ai_interpretation, gps_coordinates, location_address, raw_email, email_timestamp))
    conn.commit()
    conn.close()

# Display DTC entry
def display_dtc_entry(entry, show_raw=False):
    st.markdown("### DTC Alert Details")
    st.markdown(f"**Vehicle Name:** {entry['vehicle_name']}")
    
    # Display timestamp
    if 'email_timestamp' in entry:
        st.markdown(f"**Alert Received:** {entry['email_timestamp']}")
    
    st.markdown("**Interpretation:**")
    st.markdown(entry['ai_interpretation'])
    
    if show_raw:
        with st.expander("Raw Email Data"):
            st.text(entry['raw_email'])
    st.divider()

# Initialize database on startup
init_db()

# Sidebar navigation
fetch_analyze = st.sidebar.button("Fetch & Analyze DTCs")
show_history = st.sidebar.button("View DTC History")
show_raw = st.sidebar.checkbox("Show Raw Email Data")

# Main content area
if fetch_analyze:
    status_placeholder = st.empty()
    dtc_entries = fetch_emails()
    
    if dtc_entries:
        for entry in dtc_entries:
            if entry:
                ai_result = interpret_dtc(entry["dtc_text"], status_placeholder)
                save_to_db(
                    entry["vehicle_name"],
                    entry["dtc_text"],
                    ai_result,
                    entry["gps_coordinates"],
                    entry["location_address"],
                    entry["raw_email"],
                    entry["email_timestamp"]
                )
                
                # Display current analysis results
                st.subheader("Current Analysis Results")
                current_entry = {
                    'vehicle_name': entry['vehicle_name'],
                    'ai_interpretation': ai_result,
                    'raw_email': entry['raw_email'],
                    'email_timestamp': entry['email_timestamp']
                }
                display_dtc_entry(current_entry, show_raw)
                
        status_placeholder.success("All DTCs processed and stored successfully!")
    else:
        status_placeholder.warning("No DTC codes found in recent emails.")

# Only show history if history button is clicked
if show_history:
    st.subheader("DTC History")
    conn = sqlite3.connect("dtc_logs.db")
    
    if selected_vehicle == "All Vehicles":
        query = """
            SELECT * FROM dtc_logs 
            WHERE DATE(timestamp) BETWEEN ? AND ?
            ORDER BY timestamp DESC
        """
        params = (start_date, end_date)
    else:
        query = """
            SELECT * FROM dtc_logs 
            WHERE DATE(timestamp) BETWEEN ? AND ?
            AND vehicle_name = ?
            ORDER BY timestamp DESC
        """
        params = (start_date, end_date, selected_vehicle)
    
    df = pd.read_sql_query(query, conn, params=params, parse_dates=['timestamp'])
    conn.close()

    if not df.empty:
        st.info(f"Showing {len(df)} entries between {start_date} and {end_date}")
        for _, row in df.iterrows():
            display_dtc_entry(row, show_raw)
    else:
        st.info(f"No DTC history found for the selected criteria")