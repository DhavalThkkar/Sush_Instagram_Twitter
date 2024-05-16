import streamlit as st
import pandas as pd
import re
from datetime import datetime
from instagrapi import Client
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    FeedbackRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    RecaptchaChallengeForm,
    ReloginAttemptExceeded,
    SelectContactPointRecoveryForm,
    UserNotFound,
)
from loguru import logger
import os
import time
import random

# Constants
SESSION_DURATION = 86400  # 24 hours in seconds
LOG_FILE = "app.log"

# Configure Loguru
logger.add(LOG_FILE, rotation="10 MB")

# Define Account Class with Exception Handling
class Account:
    def __init__(self, username, password):
        self.username = username
        self.password = password

    def get_client(self):
        def handle_exception(client, e):
            if isinstance(e, BadPassword):
                logger.error(f"Bad Password: {e}")
                client.set_proxy(client.next_proxy().href)
                if client.relogin_attempt > 0:
                    self.freeze("Manual login required", days=7)
                    raise ReloginAttemptExceeded(e)
                client.settings = client.rebuild_client_settings()
                return client.update_client_settings(client.get_settings())
            elif isinstance(e, LoginRequired):
                logger.error(f"Login Required: {e}")
                client.relogin()
                return client.update_client_settings(client.get_settings())
            elif isinstance(e, ChallengeRequired):
                api_path = client.last_json.get("challenge", {}).get("api_path")
                if api_path == "/challenge/":
                    client.set_proxy(client.next_proxy().href)
                    client.settings = client.rebuild_client_settings()
                else:
                    try:
                        client.challenge_resolve(client.last_json)
                    except ChallengeRequired as e:
                        self.freeze("Manual Challenge Required", days=2)
                        raise e
                    except (ChallengeRequired, SelectContactPointRecoveryForm, RecaptchaChallengeForm) as e:
                        self.freeze(str(e), days=4)
                        raise e
                    client.update_client_settings(client.get_settings())
                return True
            elif isinstance(e, FeedbackRequired):
                message = client.last_json["feedback_message"]
                logger.warning(f"Feedback Required: {message}")
                if "This action was blocked. Please try again later" in message:
                    self.freeze(message, hours=12)
                elif "We restrict certain activity to protect our community" in message:
                    self.freeze(message, hours=12)
                elif "Your account has been temporarily blocked" in message:
                    self.freeze(message)
            elif isinstance(e, PleaseWaitFewMinutes):
                logger.warning(f"Please Wait: {e}")
                self.freeze(str(e), hours=1)
            raise e

        cl = Client()
        cl.handle_exception = handle_exception
        try:
            cl.login(self.username, self.password)
        except Exception as e:
            handle_exception(cl, e)
        return cl

    def freeze(self, message, hours=0, days=0):
        duration = hours * 3600 + days * 86400
        logger.warning(f"Account frozen: {message} for {duration} seconds")

# Helper Functions
def is_session_valid(session_file):
    if os.path.exists(session_file):
        file_age = time.time() - os.path.getmtime(session_file)
        if file_age < SESSION_DURATION:
            return True
    return False

def extract_ig_username(url):
    if isinstance(url, str):
        match = re.search(r"instagram\.com/([^/?]+)", url)
        return match.group(1) if match else None
    return None

def extract_ig_usernames(text):
    lines = text.splitlines()
    usernames = []
    for line in lines:
        parts = line.split()
        for part in parts:
            if 'instagram.com' in part:
                username = extract_ig_username(part)
                if username:
                    usernames.append(username)
            elif 'IG:' in part:
                username = part.split('IG:')[-1].strip().replace('/', '')
                if username:
                    usernames.append(username)
            elif re.match(r'^[\w.]+$', part.strip()):  # For simple usernames not part of a URL
                usernames.append(part.strip())
    return list(filter(None, usernames))

def count_posts_for_month(user_id, year, month, client):
    posts = client.user_medias(user_id, amount=1000)
    start_date = datetime(year, month, 1)
    end_date = datetime(year, month + 1, 1) if month < 12 else datetime(year + 1, 1, 1)
    count = 0
    links = []
    for post in posts:
        post_date = post.taken_at.replace(tzinfo=None) if post.taken_at.tzinfo else post.taken_at
        if start_date <= post_date < end_date:
            count += 1
            links.append(f"https://www.instagram.com/p/{post.code}/")
    return count, links

def manual_search(username, start_year, start_month, end_year, end_month, client):
    all_posts = []
    try:
        user_id = client.user_id_from_username(username)
        for year in range(start_year, end_year + 1):
            for month in range(start_month if year == start_year else 1, end_month + 1 if year == end_year else 13):
                start_time = time.time()
                post_count, post_links = count_posts_for_month(user_id, year, month, client)
                month_name = datetime(year, month, 1).strftime('%B')
                all_posts.append({
                    "Instagram ID": username,
                    "Post Count": post_count,
                    "Year": str(year),  # Ensure the year is stored as a string
                    "Month": month_name if month_name else "-",
                    "Links": " | ".join(post_links)
                })
                elapsed_time = time.time() - start_time
                completion_message = f"Completed {username} | {month_name} {year} | Posts: {post_count} | Time: {elapsed_time:.2f} sec"
                st.write(completion_message)
                logger.info(completion_message)
                time.sleep(random.uniform(0.5, 1.5))
    except UserNotFound:
        error_message = f"Error: User {username} not found."
        st.error(error_message)
        logger.error(error_message)
        all_posts.append({
            "Instagram ID": username,
            "Post Count": 0,
            "Year": "-",
            "Month": "-",
            "Links": "User not found"
        })
    except Exception as e:
        if client:
            client.handle_exception(client, e)
        st.error(f"Error retrieving posts for {username}: {e}")
        all_posts.append({
            "Instagram ID": username,
            "Post Count": 0,
            "Year": "-",
            "Month": "-",
            "Links": "Error occurred"
        })
    return all_posts

def save_to_csv(data):
    df = pd.DataFrame(data)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_filename = f"instagram_results_{timestamp}.csv"
    df.to_csv(csv_filename, index=False)
    return csv_filename

def clean_up_files(file_list):
    for file in file_list:
        if os.path.exists(file):
            os.remove(file)
            logger.info(f"Removed file: {file}")

# Streamlit App
st.title("Instagram Data Processor")

# Sidebar for User Credentials
with st.sidebar:
    st.header("Instagram Credentials")
    
    USERNAME = st.text_input("Instagram Username")
    PASSWORD = st.text_input("Instagram Password", type="password")
    login_button = st.button("Login")

# Session Management
if 'client' not in st.session_state:
    st.session_state['client'] = None

session_file = f"session_{USERNAME}.json"  # Moved up for visibility

# Initialize Instagram Client in Session State if not already present
if 'client' not in st.session_state or not st.session_state.client:
    if USERNAME and is_session_valid(session_file):
        try:
            st.session_state.client = Client()
            st.session_state.client.load_settings(session_file)
            st.sidebar.success(f"Session loaded for {USERNAME}")
            logger.info(f"Session loaded successfully for user: {USERNAME}")
        except Exception as e:
            logger.error(f"Failed to load session for user {USERNAME}: {e}")
            st.sidebar.error("Failed to load existing session. Please log in again.")
    elif USERNAME and PASSWORD and login_button:
        account = Account(USERNAME, PASSWORD)
        try:
            st.session_state.client = account.get_client()
            st.session_state.client.dump_settings(session_file)
            st.sidebar.success(f"Logged in as {USERNAME}")
            logger.info(f"Logged in and saved session for user: {USERNAME}")
        except Exception as e:
            logger.error(f"Login failed for user {USERNAME}: {e}")
            st.sidebar.error("Login failed. Please check your credentials.")
    else:
        st.sidebar.error("Please enter both username and password.")

# Main App Section for Inputs
st.header("Input Details")

# User ID Input Section
st.subheader("Social Media Handles")
user_input = st.text_area("Enter Social Media Handles (one per line)")
instagram_usernames = extract_ig_usernames(user_input)

# Date Range Input
st.subheader("Specify Date Range")
col1, col2 = st.columns(2)
with col1:
    start_year = st.number_input("Start Year", min_value=2000, max_value=datetime.now().year, value=datetime.now().year)
    start_month = st.selectbox("Start Month", [datetime(2000, i, 1).strftime('%B') for i in range(1, 13)], key="start_month")
with col2:
    end_year = st.number_input("End Year", min_value=2000, max_value=datetime.now().year, value=datetime.now().year)
    end_month = st.selectbox("End Month", [datetime(2000, i, 1).strftime('%B') for i in range(1, 13)], key="end_month")

# Convert month names to numbers
start_month_num = datetime.strptime(start_month, '%B').month
end_month_num = datetime.strptime(end_month, '%B').month

# Check Date Range Validity
valid_date_range = True
if (end_year < start_year) or (end_year == start_year and end_month_num < start_month_num):
    valid_date_range = False
    st.warning("End date cannot be earlier than start date.")

# Initialize results container
if 'results' not in st.session_state:
    st.session_state.results = []

# Process All User IDs if valid and client is initialized
if valid_date_range:
    process_button = st.button("Process All IDs")
    if process_button:
        if not instagram_usernames:
            st.warning("No valid Instagram usernames found.")
        elif 'client' not in st.session_state or not st.session_state.client:
            st.warning("Please log in before processing IDs.")
        else:
            # Clear previous results before processing new ones
            st.session_state.results = []
            progress_bar = st.progress(0)
            total_ids = len(instagram_usernames)
            processed_usernames = set()  # To avoid duplicate processing
            for idx, username in enumerate(instagram_usernames):
                if username not in processed_usernames:
                    posts_info = manual_search(username, start_year, start_month_num, end_year, end_month_num, st.session_state.client)
                    st.session_state.results.extend(posts_info)
                    processed_usernames.add(username)
                    progress_bar.progress((idx + 1) / total_ids)
                    st.write(f"Completed processing for {username}.")
            st.session_state['process_all'] = True  

# Display results
results_df = pd.DataFrame(st.session_state.results)

# Utility Functions (make sure to define these properly)
def generate_csv(results):
    """Generate and cache the CSV file for download."""
    df = pd.DataFrame(results)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_filename = f"instagram_results_{timestamp}.csv"
    df.to_csv(csv_filename, index=False)
    return csv_filename

def generate_log_file():
    """Generate and cache the log file for download."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"processed_logs_{timestamp}.log"
    with open(LOG_FILE, "r") as log_file, open(log_filename, "w") as filtered_log_file:
        for line in log_file:
            if "Completed" in line or "Error" in line:
                filtered_log_file.write(line)
    return log_filename

# Display results
st.header("Results")
if 'results' in st.session_state and st.session_state.results:
    results_df = pd.DataFrame(st.session_state.results)
    st.dataframe(results_df)

    # Generate and provide a download button for the results CSV
    csv_filename = generate_csv(st.session_state.results)
    with open(csv_filename, "rb") as file:
        st.download_button("Download CSV", file.read(), csv_filename, "text/csv")
        os.remove(csv_filename)  # Clean up the file immediately after use

# Logging and Downloads
st.header("Download Logs")
if os.path.exists(LOG_FILE):
    log_filename = generate_log_file()
    with open(log_filename, "rb") as file:
        st.download_button("Download Log File", file.read(), log_filename, "text/plain")
        os.remove(log_filename)  # Clean up the log file immediately after use

# Clean up old session files and processed files
if is_session_valid(session_file) and (time.time() - os.path.getmtime(session_file)) >= SESSION_DURATION:
    os.remove(session_file)
    logger.info(f"Old session file {session_file} removed.")
