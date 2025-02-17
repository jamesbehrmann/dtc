import streamlit as st
import imaplib
import email
import sqlite3
import OpenAI
import re
import pandas as pd
from datetime import datetime

# Configuration
IMAP_SERVER = "imap.ionos.com"
EMAIL_USER = st.secrets["EMAIL_USER"]
EMAIL_PASSWORD = st.secrets["EMAIL_PASSWORD"]
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Sidebar setup
st.sidebar.title("FLEET NEXIS DTC INTERPRETER")
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
    status_placeholder.info("Connecting to email server...")
    
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, 993)
    mail.login(EMAIL_USER, EMAIL_PASSWORD)
    mail.select("inbox")
    
    status_placeholder.info("Searching for emails with subject 'DTC Detected'...")
    status, messages = mail.search(None, '(SUBJECT "DTC Detected")')
    email_ids = messages[0].split()
    
    if not email_ids:
        status_placeholder.warning("No new DTC emails found.")
        return []
    
    status_placeholder.info(f"Processing {len(email_ids)} emails...")
    dtc_entries = []
    
    for e_id in email_ids[-5:]:  # Fetch last 5 emails for testing
        status, msg_data = mail.fetch(e_id, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                email_body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            email_body = part.get_payload(decode=True).decode()
                            break
                else:
                    email_body = msg.get_payload(decode=True).decode()
                
                entry = extract_dtc_info(email_body)
                if entry:
                    entry["raw_email"] = email_body
                    dtc_entries.append(entry)
    
    mail.logout()
    status_placeholder.success("Email processing complete!")
    return dtc_entries

# Extract DTC info from email body
def extract_dtc_info(text):
    match = re.search(r'Device: (.*?)\nEvent: (.*?)\nSpeed:', text, re.DOTALL)
    if match:
        vehicle_name = match.group(1).strip()
        dtc_text = match.group(2).strip()
        
        return {
            "vehicle_name": vehicle_name,
            "dtc_text": dtc_text,
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
                {"role": "system", "content": "You are an expert vehicle diagnostic assistant."},
                {"role": "user", "content": f"What do these diagnostic trouble codes mean: {dtc_text}?"}
            ]
        )
        status_placeholder.success("DTC interpretation complete!")
        return response.choices[0].message.content
    except Exception as e:
        status_placeholder.error(f"Error interpreting DTC: {str(e)}")
        return "Error interpreting DTC code"

# Save to database
def save_to_db(vehicle_name, dtc_text, ai_interpretation, gps_coordinates, location_address, raw_email):
    conn = sqlite3.connect("dtc_logs.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO dtc_logs 
        (vehicle_name, dtc_text, ai_interpretation, gps_coordinates, location_address, raw_email) 
        VALUES (?, ?, ?, ?, ?, ?)
    """, (vehicle_name, dtc_text, ai_interpretation, gps_coordinates, location_address, raw_email))
    conn.commit()
    conn.close()

# Display DTC entry
def display_dtc_entry(entry, show_raw=False):
    st.markdown(f"### 🚗 {entry['vehicle_name']}")
    if 'timestamp' in entry:  # Only show timestamp for database entries
        st.markdown(f"**Date:** {entry['timestamp']}")
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
                    entry["raw_email"]
                )
                
                # Display current analysis results
                st.subheader("Current Analysis Results")
                current_entry = {
                    'vehicle_name': entry['vehicle_name'],
                    'ai_interpretation': ai_result,
                    'raw_email': entry['raw_email']
                }
                display_dtc_entry(current_entry, show_raw)
                
        status_placeholder.success("All DTCs processed and stored successfully!")
    else:
        status_placeholder.warning("No DTC codes found in recent emails.")

# Only show history if history button is clicked
if show_history:
    st.subheader("DTC History")
    conn = sqlite3.connect("dtc_logs.db")
    df = pd.read_sql_query(
        "SELECT * FROM dtc_logs ORDER BY timestamp DESC LIMIT 10", 
        conn,
        parse_dates=['timestamp']
    )
    conn.close()

    if not df.empty:
        for _, row in df.iterrows():
            display_dtc_entry(row, show_raw)
    else:
        st.info("No DTC history found in the database.")